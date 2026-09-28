from __future__ import annotations
import csv
import datetime as dt
import json
import os
import re
import shutil
import stat
import sys
import zipfile
from dataclasses import dataclass, asdict
from pathlib import Path, PurePosixPath
from native import Elf, audit_native, patch_basic, patch_yuv

VERSION = '0.4.3-installer-template'
ASSETS = Path(getattr(sys, '_MEIPASS', Path(__file__).parent)) / 'assets'
PARTITIONS = {'system', 'system_ext', 'product', 'vendor', 'odm'}
NATIVE = {
 'system/bin/cameraserver': [('N008 connect1',0x11031c),('N008 connect2',0x11e650),('N008 configure',0x2132c4),('N001 timestamp',0x23ea28),('N002/N003 size',0x2d084c)],
 'system/lib64/libandroid.so': [('N004 lockPlanes',0x40d20),('N004 classifier',0x40e4c)],
 'system/lib64/libnativewindow.so': [('N004 lockPlanes',0x8864),('N004 classifier',0x8990)],
 'system/lib64/libstagefright_bufferqueue_helper.so': [('N006 skip',0xd840),('N006 consumer',0xda6c)],
}
TARGET_NATIVE = set(NATIVE) | {'system/lib64/libcamera_hyp.so','system/lib64/libsatbridge.so','system/lib64/libocg.so'}


def safe_rel(value):
    value = value.replace('\\','/')
    p = PurePosixPath(value)
    if p.is_absolute() or '..' in p.parts or not p.parts or any(':' in s for s in p.parts):
        raise ValueError('不安全的相对路径：'+value)
    return p.as_posix()


def device_path(module_path):
    p = safe_rel(module_path)
    parts = p.split('/')
    if len(parts)>2 and parts[0]=='system' and (parts[1] in PARTITIONS-{'system'} or parts[1].startswith('my_')):
        return '/'.join(parts[1:])
    return p


class Rom:
    def __init__(self, path):
        self.root = Path(path).resolve()
        if not self.root.is_dir():
            raise ValueError('ROM 解包目录不存在：'+str(self.root))

    def find(self, rel):
        rel = safe_rel(rel)
        parts = rel.split('/')
        partition, rest = parts[0], '/'.join(parts[1:])
        paths = [self.root/rel, self.root/partition/partition/rest]
        if partition != 'system':
            paths += [self.root/'system'/rel, self.root/'system/system'/rel]
        existing = []
        for p in paths:
            if p.is_file():
                resolved = self.resolve_link(p)
                if resolved not in existing:
                    existing.append(resolved)
        if len(existing)>1:
            # Explicit ambiguity instead of silently selecting a stale duplicate.
            if any(p.read_bytes()!=existing[0].read_bytes() for p in existing[1:]):
                raise ValueError('解包目录有多份不同原件：'+rel)
        return existing[0] if existing else None

    def resolve_link(self, p):
        seen = set()
        for _ in range(16):
            p = p.resolve()
            if not p.is_relative_to(self.root):
                raise ValueError('链接越出 ROM 目录：'+str(p))
            if p in seen:
                raise ValueError('循环链接：'+str(p))
            seen.add(p)
            if not p.is_file():
                raise FileNotFoundError(str(p))
            with p.open('rb') as f:
                head = f.read(4096)
            if not head.startswith(b'!<symlink>'):
                return p
            raw = head[len(b'!<symlink>'):]
            target = raw.decode('utf-16' if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8').rstrip('\0\r\n')
            if target.startswith('/'):
                found = self.root/target.lstrip('/')
                if not found.exists() and target.startswith('/system/'):
                    found = self.root/'system'/target.lstrip('/')
                p = found
            else:
                p = p.parent/target
        raise ValueError('链接层数过多')

    def properties(self):
        props = {}
        for part in ['system','system_ext','product','vendor','odm','my_product','my_manifest']:
            for filename in ['build.prop','etc/build.prop']:
                p = self.find(f'{part}/{filename}')
                if p:
                    for line in p.read_text('utf-8',errors='replace').splitlines():
                        if '=' in line and not line.lstrip().startswith('#'):
                            key,value=line.split('=',1)
                            props[key.strip()]=value.strip()
        return props


class Base:
    def __init__(self,path):
        self.path=Path(path).resolve()
        self.archive=None
        if self.path.is_file():
            self.archive=zipfile.ZipFile(self.path)
            self.members={}
            try:
                for item in self.archive.infolist():
                    if item.is_dir(): continue
                    name=safe_rel(item.filename)
                    if stat.S_ISLNK(item.external_attr>>16): continue
                    if name in self.members: raise ValueError('ZIP 重复文件：'+name)
                    self.members[name]=item
                if 'module.prop' not in self.members: raise ValueError('请选择根目录含 module.prop 的模块 ZIP')
            except Exception:
                self.archive.close()
                raise
        elif not (self.path/'module.prop').is_file():
            raise ValueError('模块目录缺少 module.prop')

    def exists(self,rel):
        rel=safe_rel(rel)
        return rel in self.members if self.archive else (self.path/rel).is_file()

    def read(self,rel):
        rel=safe_rel(rel)
        if self.archive:
            item=self.members[rel]
            if item.file_size>2*1024**3: raise ValueError('单文件过大')
            return self.archive.read(item)
        p=(self.path/rel).resolve()
        if not p.is_relative_to(self.path): raise ValueError('模块文件越界')
        return p.read_bytes()

    def close(self):
        if self.archive:self.archive.close()


@dataclass
class Inputs:
    os4: str
    donor: str
    fallback: str = ''
    base: str = ''
    output: str = ''
    capture: str = ''

    def __post_init__(self):
        # Retain the legacy field/recipe branches, but never use a third ROM.
        # This also normalizes old project JSON and legacy CLI --fallback.
        self.fallback = self.donor
        if not self.base:self.base=str(ASSETS/'materials.zip')

    @classmethod
    def from_project(cls,data):
        data=data.get('inputs',data)
        # Old saved base/fallback paths must never recreate the removed input.
        return cls(os4=data['os4'],donor=data['donor'],output=data['output'],capture=data.get('capture',''))


def load_rules():
    return json.loads((ASSETS/'rules.json').read_text('utf-8'))


def find_material(rom, device, rule):
    partition=device.split('/')[0]
    candidates=[device]+rule.get('source_candidates',[])
    for candidate in dict.fromkeys(candidates):
        source_part=candidate.split('/')[0]
        # Do not smuggle a cross-partition/hardware fallback through an alias.
        if partition in ('vendor','odm') and source_part!=partition:continue
        if partition not in ('vendor','odm') and source_part in ('vendor','odm'):continue
        source=rom.find(candidate)
        if source:return source
    return None


def plan(inputs:Inputs, log=lambda s:None):
    from materials import generated_config,HARDWARE_EXCEPTIONS,hardware_path,RETIRED,is_builtin
    os4, donor = Rom(inputs.os4),Rom(inputs.donor)
    fallback = Rom(inputs.fallback) if inputs.fallback.strip() else None
    base = Base(inputs.base) if inputs.base.strip() else None
    result=dict(version=VERSION, inputs=asdict(inputs), files=[], native=[],
                properties={'os4':os4.properties(),'donor':donor.properties()},
                blockers=[],warnings=[], flashable=False)
    try:
        for rule in load_rules()['files']:
            dest=rule['destination']; dev=device_path(dest); partition=dev.split('/')[0]
            row=dict(rule);row.update(source='',source_kind='',state='')
            try:
                if dest in RETIRED:
                    row.update(state='已退役，不复制',source_kind='retired')
                elif generated_config(dest):
                    row.update(state='从所选本机原厂 ROM 动态生成/按 Include 闭包提取',source_kind='config-rebuild')
                elif dest.endswith('dolby_vision.cfg'):
                    # These aliases used to be byte-identical to a public
                    # partition file. That is NOT evidence they are hardware
                    # independent; never take these from the fallback ROM.
                    source=find_material(donor,dev,rule)
                    if source:row.update(state='本机原厂显示配置，等待成组审计',source=str(source),source_kind='donor')
                    else:row.update(state='本机杜比别名待生成；禁止旧机型兜底',source_kind='config-rebuild')
                elif dest=='system/vendor/etc/media_codecs_dolby_vision_playback.xml':
                    row.update(state='从本机原厂杜比定义生成；不硬编码能力上限',source_kind='config-rebuild')
                elif dest in TARGET_NATIVE:
                    original=dev.replace('libcamera_hyp.so','libcamera_client.so')
                    if dest.endswith(('libsatbridge.so','libocg.so')):
                        row.update(state='需要目标 ABI 适配/重编译',source_kind='native-rebuild')
                    else:
                        source=os4.find(original)
                        row.update(state='当前 OS4 原件，等待补丁',source=str(source or ''),source_kind='native-rebuild')
                        if not source:row['state']='缺少关键 OS4 原件'
                elif dest in HARDWARE_EXCEPTIONS or (rule['policy']=='fixed' and not hardware_path(dest)):
                    if base and base.exists(dest):row.update(state='内置兼容材料' if is_builtin(inputs.base) else '开发用兼容材料',source=f'{base.path}!/{dest}',source_kind='base')
                    else:row.update(state='工具缺少必需的内置兼容材料',source_kind='missing')
                else:
                    source=find_material(donor,dev,rule)
                    if source:row.update(state='使用所选原厂包',source=str(source),source_kind='donor')
                    elif not hardware_path(dest) and rule.get('crd_exception') and base and base.exists(dest):
                        row.update(state='类原生明确例外：保留修复版',source=f'{base.path}!/{dest}',source_kind='base')
                    elif partition not in ('vendor','odm') and fallback:
                        source=find_material(fallback,dev,rule)
                        if source:row.update(state='所选本机原厂 ROM 同源兜底',source=str(source),source_kind='fallback')
                    if not row['state']:
                        row.update(state='缺失：不跨机型补 vendor/odm' if partition in ('vendor','odm') else '缺失：待处理',source_kind='missing')
                if rule.get('review'):
                    row['review']=rule['review']
            except Exception as e:
                row.update(state='读取被阻止',error=str(e),source_kind='missing')
            result['files'].append(row)
        for rel,sites in NATIVE.items():
            source=os4.find(rel)
            baseline=ASSETS/'baseline'/Path(rel).name
            item=dict(file=rel)
            try:
                if not source:raise FileNotFoundError(rel)
                description, checks=audit_native(source.read_bytes(),baseline.read_bytes(),sites)
                item.update(description=description,checks=checks)
                log(f'分析 {Path(rel).name}：'+str(len(checks))+' 个补丁入口')
            except Exception as e:item['error']=str(e)
            result['native'].append(item)
        config_paths=[]
        for folder in [donor.root/'odm/etc/camera',donor.root/'vendor/etc/camera',donor.root/'my_product/etc/camera']:
            if folder.is_dir():
                config_paths.extend(str(p.relative_to(donor.root)).replace('\\','/') for p in folder.rglob('*') if p.is_file())
        result['donor_camera_files']=sorted(config_paths)
        # Sensor names are evidence for review, never translated into guessed camera IDs.
        result['sensor_name_candidates']=sorted({x for p in config_paths for x in re.findall(r'(?i)(?:dodge|hh|ossi|sm\d+|imx\d+|lyt\d+|jn\d+)[a-z0-9_]*',p)})
        result['blockers'] += [
            '本页是基础扫描；完整 N001–N008 候选补丁须运行“监督回归”，以该轮阶段结果为准。',
            '候选补丁静态通过不等于运行通过；未知指令、ABI、硬件规则会阻止生成。',
            'vendor tag 仅允许已验证硬件 profile；不从机型名猜编号或物理镜头 ID。',
            'APK 的现有 DPI 策略保留；其他分辨率/显示策略尚未自动适配，不修改系统 density。',
            '监督回归检查三个 JAR 共享库 XML；不改小米 JAR，不覆盖旧 bootclasspath.pb。',
            '尚未完成全模块依赖/SELinux 类型/运行时验证，禁止输出可刷 ZIP。',
        ]
        if any(r['source_kind']=='missing' for r in result['files']):result['blockers'].append('存在缺件；详见逐文件列表。')
        result['warnings'] += ['固定 APK/JAR 及类原生库为用户指定沿用，不代表所有机型已经验证。',
            '不会遍历复制整分区；只处理昨天 366 项清单及列出的原厂相机配置。',
            '修复 APK/JAR/桥及具名历史例外随工具内置，不需要用户提供旧模块。',
            '无 ADB、无 WSL、无自动安装/重启、无输入文件修改、无哈希计算。']
        return result
    finally:
        if base:base.close()


def output_dir(inputs):
    out=Path(inputs.output).resolve()
    for value in [inputs.os4,inputs.donor,inputs.fallback,inputs.base]:
        if value:
            source=Path(value).resolve()
            if source.is_file():
                if out==source:raise ValueError('输出不能覆盖输入')
            elif out==source or out.is_relative_to(source) or source.is_relative_to(out):
                raise ValueError('输出目录必须与 ROM/模块输入目录相互独立，不能互相包含')
    run=out/('适配_'+dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    run.mkdir(parents=True,exist_ok=False)
    return run


def export(inputs:Inputs,result,materialize=False,log=lambda s:None):
    if result['inputs']!=asdict(inputs):raise ValueError('路径已变化，请重新分析')
    run=output_dir(inputs)
    (run/'分析结果.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
    with (run/'文件取材表.csv').open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.writer(f);w.writerow(['目标路径','用途（昨日文档）','策略','本次状态','实际来源','待复核'])
        for r in result['files']:w.writerow([r['destination'],r.get('purpose',''),r['policy'],r['state'],r['source'],r.get('review','')])
    lines=['# 欧加相机 OTA 适配工作目录（不可刷入）','',f'工具版本：{VERSION}',
           '', '## 未完成/阻止打包原因','']+['- '+s for s in result['blockers']]
    lines+=['','## 原生补丁定位','']
    for n in result['native']:
        lines+=['### '+n['file'],'']
        for check in n.get('checks',[]):lines+=['- '+json.dumps(check,ensure_ascii=False)]
        if n.get('error'):lines+=['- '+n['error']]
        lines+=['']
    lines+=['## 边界','']+['- '+s for s in result['warnings']]
    (run/'先读报告.md').write_text('\n'.join(lines),'utf-8')
    # A bounded, genuinely executed migration, clearly kept outside any module.
    try:
        original=Rom(inputs.os4).find('system/bin/cameraserver').read_bytes()
        patched,audit=patch_basic(original,(ASSETS/'baseline/cameraserver').read_bytes(),'cameraserver')
        candidate=run/'native_中间产物_禁止刷入';candidate.mkdir()
        (candidate/'cameraserver.raw_timestamp.partial').write_bytes(patched)
        (candidate/'修改记录.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),'utf-8')
        log('N001/N002 已按目标函数位置生成中间产物；未冒充完整 cameraserver。')
    except Exception as e:
        (run/'原生补丁阻止原因.txt').write_text(str(e),'utf-8')
        log('中间产物生成被阻止：'+str(e))
    for name in ['libandroid.so','libnativewindow.so']:
        candidate=run/'native_中间产物_禁止刷入';candidate.mkdir(exist_ok=True)
        try:
            source=Rom(inputs.os4).find('system/lib64/'+name)
            data,audit=patch_yuv(source.read_bytes(),(ASSETS/'baseline'/name).read_bytes(),
                                 (ASSETS/'helpers'/(name+'.bin')).read_bytes(),name)
            (candidate/(name+'.candidate')).write_bytes(data)
            (candidate/(name+'.json')).write_text(json.dumps(audit,ensure_ascii=False,indent=2),'utf-8')
            log('N004 '+name+' 已动态分配 RX 空洞并重定位跳板，待运行验证。')
        except Exception as e:
            (candidate/(name+'.blocked.txt')).write_text(str(e),'utf-8')
            log('N004 '+name+' 被阻止：'+str(e))
    if materialize:
        base=Base(inputs.base) if inputs.base else None
        try:
            for index,row in enumerate(result['files']):
                if row['source_kind'] not in ('base','donor','fallback'):continue
                dest=run/'材料_不是模块'/safe_rel(row['destination'])
                dest.parent.mkdir(parents=True,exist_ok=True)
                if row['source_kind']=='base':dest.write_bytes(base.read(row['destination']))
                else:shutil.copyfile(row['source'],dest)
                if index%20==0:log(f'整理材料 {index+1}/{len(result["files"])}')
        finally:
            if base:base.close()
    return run
