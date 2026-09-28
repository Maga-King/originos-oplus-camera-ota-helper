"""Local-ROM first, explicitly enabled read-only ADB fallback, reusable cache."""
from __future__ import annotations
import json,re,sys,subprocess,datetime
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'OplusCameraOTA'))
from core import Rom
from native import Unsupported
from camera_capture import list_devices,choose_device,run_adb,parse_capture

TARGET_FILES=(
 'system/bin/cameraserver','system/lib64/libcamera_client.so',
 'system/lib64/libcameraservice.so','system/lib64/libvivocameraservice.so',
 'system/lib64/libandroid.so','system/lib64/libnativewindow.so',
 'system/lib64/libstagefright_bufferqueue_helper.so','system/lib64/libstagefright.so',
)

@dataclass
class Inputs:
    target_rom:str=''
    stock_rom:str=''
    adb_enabled:bool=False
    adb_serial:str=''
    adb_target_files:bool=False
    adb_hardware_files:bool=True
    camera_dump:str=''

class DeviceReadError(Unsupported):
    def __init__(self,path,detail):
        lowered=detail.lower()
        if 'permission denied' in lowered or 'access denied' in lowered or 'not allowed' in lowered:
            self.kind='permission';label='设备拒绝读取（请检查 root 授权/SELinux，不是文件缺失）'
        elif 'no such file or directory' in lowered:
            self.kind='missing';label='设备上没有这个文件/目录'
        else:
            self.kind='read-failed';label='设备读取失败（不会当作缺文件兜底）'
        super().__init__(f'{label}：/{path}\n{detail.strip()}')

class Sources:
    def __init__(self,inputs:Inputs,cache:Path,log=print):
        self.inputs=inputs
        self.cache=cache.resolve()/('capture_'+datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
        self.log=log;self.records=[]
        self.target=Rom(inputs.target_rom) if inputs.target_rom else None
        self.stock=Rom(inputs.stock_rom) if inputs.stock_rom else None
        self.serial=None
        # No ADB discovery or command at all when checkbox is disabled.
        if inputs.adb_enabled:self.serial=choose_device(list_devices(),inputs.adb_serial)

    def resolve(self,role,relative,required=True):
        if role not in ('target','stock'):raise ValueError(role)
        if not re.fullmatch(r'[A-Za-z0-9_./@+\-]+',relative) or '..' in relative.split('/') or relative.startswith('/'):
            raise ValueError('非法取材路径')
        rom=self.target if role=='target' else self.stock
        local=rom.find(relative) if rom else None
        if local:
            self.records.append({'role':role,'path':relative,'source':str(local),'kind':'rom'})
            return local
        # Running OriginOS cannot stand in for stock ColorOS system/product/my_*.
        allowed=(role=='target' and self.inputs.adb_target_files) or (
            role=='stock' and self.inputs.adb_hardware_files and relative.split('/')[0] in ('vendor','odm'))
        if self.serial and allowed:
            saved=self.cache/role/relative
            if saved.exists():
                self.records.append({'role':role,'path':relative,'source':str(saved),'kind':'adb-session-cache'})
                return saved
            try:
                try:data=run_adb(['-s',self.serial,'exec-out','su','-c','cat /'+relative],90)
                except Unsupported as exc:raise DeviceReadError(relative,str(exc)) from exc
                if not data or data.startswith((b'cat: ',b'/system/bin/sh:',b'su: ')):
                    raise DeviceReadError(relative,data.decode('utf8',errors='replace') or '返回空内容')
                if relative.endswith('.so') or relative=='system/bin/cameraserver':
                    if not data.startswith(b'\x7fELF'):raise Unsupported('设备读取结果不是ELF：'+relative)
                saved.parent.mkdir(parents=True,exist_ok=True);saved.write_bytes(data)
                self.records.append({'role':role,'path':relative,'source':'adb:/'+relative,'kind':'adb','cached':str(saved)})
                return saved
            except DeviceReadError as exc:
                self.records.append({'role':role,'path':relative,'kind':'adb-read-error','reason':exc.kind})
                if required or exc.kind!='missing':raise
                self.log(str(exc));return None
        if required:raise Unsupported(f'缺少 {role} 原件：{relative}；可补齐解包或明确开启对应ADB备用取材')
        return None

    def camera_evidence(self):
        """Optional evidence; absent dumpsys does not automatically block offline parsing."""
        if self.inputs.camera_dump:
            p=Path(self.inputs.camera_dump);raw=p.read_bytes();source=str(p.resolve())
        elif self.serial:
            raw=run_adb(['-s',self.serial,'exec-out','dumpsys','media.camera'],60)
            p=self.cache/'camera_runtime.txt';p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
            source=str(p)
        else:return {'available':False,'reason':'未选择运行时资料，继续离线解析原厂HAL'}
        text=raw.decode('utf-16' if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig',errors='replace')
        try:report=parse_capture(text)
        except (Unsupported,ValueError) as exc:
            return {'available':True,'parsed':False,'source':source,'warning':str(exc)}
        report.update(available=True,parsed=True,source=source,
                      warning='当前系统HAL的快照；请确认与所选vendor/odm对应，序列号不作机型兼容锁。')
        return report

    def named_files(self,role,directory,basename):
        """Enumerate only a requested ROM subtree; preserves model-specific subdirectories."""
        if role not in ('target','stock') or not re.fullmatch(r'[A-Za-z0-9_./-]+',directory) or '..' in directory.split('/'):
            raise ValueError('非法目录')
        if '/' in basename or not re.fullmatch(r'[A-Za-z0-9_.-]+',basename):raise ValueError('非法文件名')
        rom=self.target if role=='target' else self.stock;names=set()
        if rom:
            part,rest=directory.split('/',1)
            candidates=[rom.root/directory,rom.root/part/part/rest]
            if part!='system':candidates += [rom.root/'system'/directory,rom.root/'system/system'/directory]
            for root in candidates:
                if root.is_dir():
                    for p in root.rglob(basename):
                        if p.is_file():names.add(directory+'/'+p.relative_to(root).as_posix())
        allowed=(role=='target' and self.inputs.adb_target_files) or (role=='stock' and self.inputs.adb_hardware_files and directory.split('/')[0] in ('vendor','odm'))
        if self.serial and allowed and not names:
            try:raw=run_adb(['-s',self.serial,'exec-out','su','-c',f'find /{directory} -type f -name {basename}'],60)
            except Unsupported as exc:
                error=DeviceReadError(directory,str(exc))
                if error.kind!='missing':raise error from exc
                self.log(str(error));return []
            for line in raw.decode('utf8',errors='replace').splitlines():
                if line.startswith('/'+directory+'/') and line.endswith('/'+basename):names.add(line[1:])
                elif line.strip():
                    error=DeviceReadError(directory,line)
                    if error.kind!='missing':raise error
                    self.log(str(error))
        return [(n,self.resolve(role,n)) for n in sorted(names)]

    def hardware_properties(self):
        result=self.stock.properties() if self.stock else {}
        if self.serial and self.inputs.adb_hardware_files and not result.get('ro.board.platform'):
            value=run_adb(['-s',self.serial,'exec-out','getprop','ro.board.platform'],15).decode('utf8',errors='replace').strip()
            if re.fullmatch(r'[A-Za-z0-9_-]+',value):result['ro.board.platform']=value
        return result

    def save_manifest(self,path):
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps({'files':self.records,'device_modified':False,
            'warning':'仅缓存当前已加载分区；ADB不能还原此时未运行的原厂系统公共分区。'},ensure_ascii=False,indent=2),encoding='utf8')
