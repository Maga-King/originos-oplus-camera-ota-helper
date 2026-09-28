"""Additive, named camera-chain policy. Never global permissive/allow appdomain."""
import re
import struct

# Kernel type datum: name length, value, properties, bounds then name.
# See SELinuxProject/libsepol/src/policydb.c type_read (POLICY_KERN).
# This is record evidence, not a complete libsepol compiler/neverallow checker.
def live_type_record(data,name):
    if data[:4]!=struct.pack('<I',0xf97cff8c):return False
    needle=name.encode('ascii');pos=0
    while True:
        at=data.find(needle,pos)
        if at<0:return False
        if at>=16:
            length,value,properties,bounds=struct.unpack_from('<IIII',data,at-16)
            if length==len(needle) and 0<value<1000000 and properties==1 and bounds<1000000:return True
        pos=at+len(needle)

SOURCES=['cameraserver','hal_camera_default','oppo_hal_oppoalgoprocessservice',
         'oppoalgo_daemon','remosaic_daemon','hal_extcamera_default',
         'mediaswcodec','mediacodec','mediaextractor','mediaprovider',
         'priv_app','priv_app_36','system_app','platform_app','platform_app_36']
FILES=['vendor_camera_data_file','vendor_camera_remote_dump_file','vendor_camera_update_data_file',
       'persist_camera_file','persist_camera_calibration_file','adsprpcd_file']
DEVICES=['vendor_qdsp_device','vendor_xdsp_device','camera_turbo_device']
SERVICES=['vendor_hal_offlinecamera_service','oppo_remosaic_service','oppoalgo_service',
          'hal_cameraextension_aidl_service','cameraserver_service']
HWSERVICES=['oppo_remosaic_hwservice','oppoalgo_daemon_hwservice',
            'hal_oppoalgoprocessservice_hwservice','hal_cameraextension_hwservice']
PROPS=['vendor_camera_prop','camera_config_prop']
PEERS=['audioserver','mediaserver','mediametrics','surfaceflinger','system_server',
       'servicemanager','hwservicemanager','hal_allocator_default','hal_graphics_allocator_default',
       'system_suspend','vendor_hal_qspmhal_default','vendor_adsprpcd','vendor_cdsprpcd']
BEGIN='# BEGIN OTA-HELPER CAMERA CHAIN v1'
END='# END OTA-HELPER CAMERA CHAIN v1'

def broaden(original,policy,classes,declared_types=None,stock_grants=()):
    text=original.decode('utf-8-sig')
    if BEGIN in text:
        if text.count(BEGIN)!=1 or text.count(END)!=1:raise ValueError('增强策略区块重复或不完整')
        text=re.sub(re.escape(BEGIN)+r'.*?'+re.escape(END)+r'\n?', '',text,flags=re.S)
    present=lambda n:n in declared_types if declared_types is not None else live_type_record(policy,n)
    candidates=list(SOURCES)
    if declared_types is not None:
        candidates+=sorted(n for n in declared_types if re.fullmatch(r'(?:priv_app|platform_app)_\d+',n) and n not in candidates)
    sources=[s for s in candidates if present(s)]
    lines=[]
    def allow(src,target,klass,permissions):
        if present(target) and klass in classes:
            allowed=set(classes[klass]);wanted=permissions.split()
            if not set(wanted)<=allowed:return
            line=f'allow {src} {target} {klass} {{ {permissions} }}'
            if line not in text.splitlines() and line not in lines:lines.append(line)
    for source in sources:
        for target in FILES:
            persistent=target.startswith('persist_')
            allow(source,target,'dir','read open search getattr' if persistent else 'read open search getattr write add_name remove_name create setattr')
            allow(source,target,'file','read open getattr map' if persistent else 'read open getattr write create setattr append map lock unlink rename')
        for target in DEVICES:allow(source,target,'chr_file','read write open getattr ioctl map')
        for target in SERVICES:allow(source,target,'service_manager','find')
        for target in HWSERVICES:allow(source,target,'hwservice_manager','find')
        for target in PROPS:allow(source,target,'file','read open getattr map')
        for target in sources:
            if source==target:continue
            allow(source,target,'binder','call transfer')
            allow(source,target,'fd','use')
        for target in PEERS:
            if present(target):
                allow(source,target,'binder','call transfer');allow(target,source,'binder','call transfer')
                allow(source,target,'fd','use');allow(target,source,'fd','use')
    # Preserve the known working recipe across Android's versioned privileged
    # app domains. Do NOT propagate permissive, MLS attributes or user-app rules.
    app_domains=[s for s in sources if re.fullmatch(r'(?:priv_app|platform_app)(?:_\d+)?|system_app',s)]
    original_grants=re.findall(r'(?m)^allow\s+(\w+)\s+(\w+)\s+(\w+)\s+\{\s*([^{}]+?)\s*\}\s*$',text)
    for src,target,klass,permissions in original_grants:
        if re.fullmatch(r'priv_app(?:_\d+)?',src):
            for dest in app_domains:allow(dest,target,klass,' '.join(permissions.split()))
    for src,target,klass,permissions in stock_grants:
        if src in sources:allow(src,target,klass,permissions)
    if any(re.search(r'\b(untrusted_app\w*|isolated_app\w*|appdomain|sdk_sandbox\w*)\b',l) for l in lines):
        raise ValueError('禁止新增普通用户应用/隔离域权限')
    block='\n'+BEGIN+'\n# Named system camera/algorithm domains only; no global permissive.\n'+'\n'.join(lines)+'\n'+END+'\n'
    output=text.rstrip()+'\n'+block
    return output.encode('utf-8'),dict(added=len(lines),sources=sources,
        missing_sources=[s for s in SOURCES if s not in sources],added_rules=lines,
        new_permissive_domains=[],original_rules_preserved=True,
        warning='priv_app/platform_app/system_app 及其版本域是共享系统域，原有宽容规则也影响同域应用；不能称为仅对一个包授权。')


def cil_declarations(text):
    """Concrete types and class permissions only; no mapping-reference matches.

    Android CIL is S-expression text. Permission sets may be inherited through
    classcommon, so collect declarations separately and resolve after merging
    partitions. Comments are not declarations.
    """
    text=re.sub(r';[^\n]*','',text)
    types=set(re.findall(r'\(type\s+([^\s()]+)\s*\)',text))
    classes={n:set(p.split()) for n,p in re.findall(r'\(class\s+([^\s()]+)\s+\(([^()]*)\)\s*\)',text)}
    commons={n:set(p.split()) for n,p in re.findall(r'\(common\s+([^\s()]+)\s+\(([^()]*)\)\s*\)',text)}
    parents=dict(re.findall(r'\(classcommon\s+([^\s()]+)\s+([^\s()]+)\s*\)',text))
    return types,classes,commons,parents


def broaden_offline(original,inputs):
    from core import Rom
    os4,donor=Rom(inputs.os4),Rom(inputs.donor)
    types=set();classes={};commons={};parents={};paths=[]
    for partition in ['system','system_ext','product','vendor','odm']:
        rom=donor if partition in ('vendor','odm') else os4
        label='plat' if partition=='system' else partition
        path=rom.find(f'{partition}/etc/selinux/{label}_sepolicy.cil')
        if not path:continue
        t,c,m,p=cil_declarations(path.read_text('utf-8',errors='strict'))
        types.update(t)
        for name,perms in c.items():classes.setdefault(name,set()).update(perms)
        for name,perms in m.items():commons.setdefault(name,set()).update(perms)
        parents.update(p);paths.append(str(path))
    for name,parent in parents.items():
        if name in classes:classes[name].update(commons.get(parent,set()))
    # Harvest concrete original-camera grants, not arbitrary appdomain or
    # permissive rules. Platform-wide type attributes are never expanded here.
    stock=[];stock_paths=[]
    camera_services={'cameraserver','hal_camera_default','oppo_hal_oppoalgoprocessservice',
                     'oppoalgo_daemon','remosaic_daemon','hal_extcamera_default'}
    camera_targets=set(FILES+DEVICES+SERVICES+HWSERVICES+PROPS)
    for partition in ['system','system_ext','product','vendor','odm']:
        label='plat' if partition=='system' else partition
        path=donor.find(f'{partition}/etc/selinux/{label}_sepolicy.cil')
        if not path:continue
        stock_paths.append(str(path));text=re.sub(r';[^\n]*','',path.read_text('utf-8',errors='strict'))
        for src,target,klass,perms in re.findall(r'\(allow\s+(\w+)\s+(\w+)\s+\((\w+)\s+\(([^()]*)\)\)\)',text):
            # Versioned compatibility attributes are not concrete domains.
            if src not in types or target not in types:continue
            if src in camera_services or (src in SOURCES and target in camera_targets):
                if target.startswith('persist_') and klass in ('dir','file'):
                    perms=' '.join(p for p in perms.split() if p in {'read','open','getattr','map','search'})
                if perms:stock.append((src,target,klass,' '.join(perms.split())))
    output,audit=broaden(original,b'',classes,types,stock)
    audit.update(source='selected-ROM-CIL',cil_files=paths,device_required=False,
                 target_kernel_compiled=False,stock_sources=stock_paths,stock_candidates=len(stock),
                 note='仅新增所选ROM中声明的具名类型/权限。保留全部旧规则；静态解析不等于目标内核策略编译验证。')
    return output,audit
