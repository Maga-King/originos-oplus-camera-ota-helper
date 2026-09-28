"""Optional read-only ADB capture; build consumes a saved file, never ADB."""
import datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from native import Unsupported

def read_text(path):
    raw=Path(path).read_bytes()
    return raw.decode('utf-16') if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else raw.decode('utf-8-sig')

def parse_capture(text):
    from configuration import parse_package_tag
    tag=parse_package_tag(text)
    header=r'^== Camera HAL device (\S+) \([^\r\n]+\) static information: ==\s*$'
    matches=list(re.finditer(header,text,re.M));cameras=[]
    fields=['android.lens.facing','android.lens.info.availableFocalLengths',
        'android.logicalMultiCamera.physicalIds','android.control.zoomRatioRange',
        'android.sensor.info.physicalSize','android.sensor.info.pixelArraySize',
        'android.sensor.orientation','android.sensor.info.activeArraySize']
    for index,match in enumerate(matches):
        body=text[match.end():matches[index+1].start() if index+1<len(matches) else len(text)]
        camera=dict(device=match[1],id=match[1].rsplit('/',1)[-1],characteristics={})
        for field in fields:
            value=re.search(r'^\s*'+re.escape(field)+r' \([^\r\n]+\): [^\r\n]+\r?\n((?:[ \t]+\[[^\r\n]*\]\r?\n?)+)',body,re.M)
            if value:camera['characteristics'][field]=re.findall(r'\[([^\]]*)\]',value[1])
        cameras.append(camera)
    return dict(package_tag=hex(tag),package_type='byte',cameras=cameras,
        source_kind='runtime-camera-service-export',allowance_changed=False,
        note='保存HAL原始字段；不把列表顺序当物理ID，不补出不存在的镜头。导出可含应用名等信息，请勿公开原文。')

def adb_executable():
    path=shutil.which('adb')
    if path:return path
    # Portable placement alongside EXE or source, without bundling an unknown ADB.
    import sys
    root=Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).parent
    for path in [root/'platform-tools/adb.exe',root/'adb.exe']:
        if path.is_file():return str(path)
    raise Unsupported('可选ADB读取需要 platform-tools：将adb加入PATH，或把完整platform-tools目录放在工具旁；离线导入TXT不需要ADB。')

def run_adb(args,timeout=60):
    result=subprocess.run([adb_executable(),*args],capture_output=True,timeout=timeout,
        creationflags=0x08000000 if os.name=='nt' else 0)
    if result.returncode:raise Unsupported(result.stderr.decode('utf-8',errors='replace') or 'ADB读取失败')
    return result.stdout

def list_devices():
    text=run_adb(['devices','-l'],15).decode('utf-8',errors='replace')
    return [dict(serial=m[1],state=m[2],details=m[3].strip()) for line in text.splitlines()
        if (m:=re.match(r'^(\S+)\s+(device|offline|unauthorized)\b(.*)$',line))]

def choose_device(rows,serial=''):
    online=[r['serial'] for r in rows if r['state']=='device']
    if not serial:
        if len(online)!=1:raise Unsupported('没有唯一在线设备；请授权USB调试，或在多台设备中选择读取对象。')
        serial=online[0]
    if serial not in online or not re.fullmatch(r'[A-Za-z0-9_.:\-]+',serial):raise Unsupported('所选设备未在线/未授权')
    return serial

def collect(output,serial='',log=print):
    serial=choose_device(list_devices(),serial)
    folder=Path(output).resolve()/('HAL读取_'+datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    folder.mkdir(parents=True,exist_ok=False)
    log('只读 dumpsys media.camera；不清日志、不切SELinux、不杀相机、不安装、不重启。')
    raw=run_adb(['-s',serial,'exec-out','dumpsys','media.camera'])
    path=folder/'camera_vendor_tags.txt';path.write_bytes(raw)
    report=parse_capture(read_text(path))
    props=run_adb(['-s',serial,'exec-out','getprop']).decode('utf-8',errors='replace')
    wanted=['ro.product.vendor.model','ro.board.platform','ro.build.fingerprint','ro.build.version.oplusrom']
    report['observed_properties']={key:m[1] for key in wanted
        if (m:=re.search(r'^\['+re.escape(key)+r'\]: \[(.*)\]$',props,re.M))}
    report.update(source=str(path),serial=serial,device_modified=False,
        warning='请确认读取的是原厂ColorOS，且vendor/odm对应所选原厂ROM；属性仅记录，不设置机型/序列号锁。')
    (folder/'镜头与tag摘要.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
    log('已保存，可断开手机后离线构建：'+str(path))
    return folder,report
