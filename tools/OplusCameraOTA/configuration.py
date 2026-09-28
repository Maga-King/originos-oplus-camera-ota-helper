import json
import re
import xml.etree.ElementTree as ET
from pathlib import PurePosixPath
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
from native import Unsupported, Elf

KEY=bytes.fromhex('6f2170406c247525535e4326412a4d28')


def decode_camera(data):
    if len(data)<24 or data[:2]!=b'\x01\x01' or (len(data)-8)%16:
        raise Unsupported('未知相机配置容器，不尝试强解密')
    rows=json.loads(unpad(AES.new(KEY,AES.MODE_ECB).decrypt(data[4:-4]),16))
    if not isinstance(rows,list) or not all(isinstance(r,dict) and 'VendorTag' in r for r in rows):
        raise Unsupported('相机配置不是已支持的记录数组')
    return rows


def patch_camera_config(data,allow_hdr=False):
    rows=decode_camera(data);old=list(rows);lookup={r['VendorTag']:r for r in rows}
    relevant={'com.oplus.feature.export.super.text.support','com.oplus.feature.super.text.support.v2','com.oplus.camera.capture.hdr.support'}
    if any(sum(r['VendorTag']==key for r in rows)>1 for key in relevant):
        raise Unsupported('拟修改/依赖的配置键重复，拒绝猜优先级')
    edits=[];warnings=[]
    keys=['com.oplus.feature.export.super.text.support']
    if allow_hdr:keys+=['com.oplus.camera.capture.hdr.support']
    for key in keys:
        if key in lookup:
            if lookup[key].get('Value')!='1':warnings.append(key+' 原厂显式关闭，未强制开启')
            continue
        if key=='com.oplus.feature.export.super.text.support' and lookup.get('com.oplus.feature.super.text.support.v2',{}).get('Value')!='1':
            warnings.append('原厂未声明 super.text.v2，不补文档入口');continue
        row=dict(VendorTag=key,Type='Byte',Count='1',Value='1')
        rows.append(row);edits.append(row)
    if not edits:return data,dict(added=[],warnings=warnings,original_entries=len(old))
    encoded=json.dumps(rows,ensure_ascii=False,indent=2).encode('utf-8')
    result=data[:4]+AES.new(KEY,AES.MODE_ECB).encrypt(pad(encoded,16))+data[-4:]
    actual=decode_camera(result)
    if actual[:len(old)]!=old or actual[len(old):]!=edits:raise Unsupported('配置往返校验失败')
    return result,dict(added=edits,warnings=warnings,original_entries=len(old),unchanged_original_entries=True)


def hardware_info(rom):
    path=rom.find('odm/etc/camera/CameraHWConfiguration.config')
    result=dict(sources={},sensor_names=[],camera_types=[],reported_logical_count=None,warnings=[])
    if path:
        result['sources']['CameraHWConfiguration']=str(path)
        text=path.read_text('utf-8-sig',errors='strict')
        section=re.search(r'(?ms)^\[CameraIdConfig\]\s*(.*?)(?=^\[|\Z)',text)
        if not section:raise Unsupported('缺少 CameraIdConfig 段')
        for key,value in re.findall(r'(?m)^\s*(\w+)\s*=\s*([^\r\n#]+)',section[1]):
            if key=='SensorNameList':result['sensor_names']=[x.strip() for x in value.split(';') if x.strip()]
            elif key=='CameraTypeList':result['camera_types']=[int(x.strip(),0) for x in value.split(';') if x.strip()]
            elif key=='NumLogicalCameras':result['reported_logical_count']=int(value.strip().rstrip(';'),0)
    else:result['warnings'].append('缺少 CameraHWConfiguration，不使用一加13镜头名兜底')
    unit=rom.find('odm/etc/camera/config/camera_unit_config')
    if unit:
        result['sources']['camera_unit_config']=str(unit)
        data=json.loads(unit.read_text('utf-8-sig'))
        result['unit_camera_ids']=data.get('camera_id_list',[])
        result['unit_platform']=data.get('device_info',{}).get('platform')
        result['mode_roles']=data.get('mode_type_list',{})
    else:result['warnings'].append('缺少 camera_unit_config，不推测物理镜头ID')
    config=rom.find('odm/etc/camera/config/oplus_camera_config')
    if config:
        result['sources']['oplus_camera_config']=str(config)
        # Diagnostic selection only. Payload keeps ALL original records,
        # including duplicates/order. Never infer Camera2 IDs from this list.
        result['lens_config_records']=[r for r in decode_camera(config.read_bytes()) if
            any(k in r['VendorTag'].lower() for k in ['zoom','sensor','camera.id','cameraid','physical','tele','wide','focal','picturesize','picture.size'])]
    result['mapping_note']='原厂列表原样记录；列表序号不擅自认定为 Camera2 physical ID。未覆写传感器标定。'
    return result


def match_profile(rom,profiles,capture=''):
    props=rom.properties();info=hardware_info(rom);errors=[]
    # Offline declaration extraction does not itself prove runtime-assigned
    # section bases. Retain both evidence and the exact unsupported reason.
    from vendor_catalog import inspect_rom
    try:
        catalog=inspect_rom(rom)
        info['vendor_catalog']=catalog
    except (Unsupported,ValueError,OSError) as error:
        catalog=None;info['vendor_catalog_error']=str(error)
    if capture:
        from camera_capture import read_text,parse_capture
        observed=parse_capture(read_text(capture))
        value=int(observed['package_tag'],0)
        if catalog and (value&0xffff)!=catalog['package']['index']:
            raise Unsupported('所选TXT与原厂CHI表的packageName段内序号不一致，请确认取材版本')
        if catalog and catalog['package']['tag'] and value!=int(catalog['package']['tag'],0):
            raise Unsupported('所选TXT与原厂CHI表显式编号冲突')
        info['runtime_capture']=observed
        return dict(id='selected-runtime-capture',package_tag=hex(value),
            allow_hdr_config=False,dolby_model_aliases=[],experimental=True,
            evidence=str(capture),note='采用用户选择的运行时导出；不改变tag放行范围，运行时镜头与ROM声明分别记录，不自动猜合并'),info
    if catalog and catalog['package']['tag'] is not None:
        return dict(id='donor-elf-explicit-package-tag',package_tag=catalog['package']['tag'],
            allow_hdr_config=False,dolby_model_aliases=[],experimental=True,
            evidence='本机 CHI ELF 显式 section base + 段内序号；不改变tag放行范围'),info
    for profile in profiles:
        mismatches=[]
        for key,expected in profile['required_properties'].items():
            if props.get(key)!=expected:mismatches.append(key)
        if info.get('sensor_names')!=profile['sensor_names']:mismatches.append('SensorNameList')
        for rel,expected in profile.get('vendor_build_ids',{}).items():
            path=rom.find(rel)
            if not path or Elf(path.read_bytes()).build_id!=expected:mismatches.append(rel+' build-id')
        if not mismatches:
            if catalog and (int(profile['package_tag'],0)&0xffff)!=catalog['package']['index']:
                raise Unsupported('已知规则与当前 CHI packageName 序号冲突，需重查运行时编号')
            return profile,info
        errors.append(dict(profile=profile['id'],mismatch=mismatches))
    # Exported from the actual donor hardware, not a numeric model-name guess.
    evidence=[]
    for name in ['camera_vendor_tags.txt','dumpsys_media_camera.txt']:
        path=rom.root/name
        if path.is_file():
            raw=path.read_bytes()
            text=raw.decode('utf-16') if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else raw.decode('utf-8-sig')
            evidence.append((str(path),parse_package_tag(text)))
    if evidence:
        values={tag for _,tag in evidence}
        if len(values)!=1:raise Unsupported('两份 vendor-tag 导出记录不一致，不猜优先级')
        value=next(iter(values))
        if catalog and (value&0xffff)!=catalog['package']['index']:
            raise Unsupported('导出与本机CHI表的packageName序号不一致，不能混用不同HAL的证据')
        props=rom.properties()
        return dict(id='donor-exported-vendor-tags',package_tag=hex(values.pop()),
            allow_hdr_config=False,dolby_model_aliases=[],experimental=True,
            evidence=evidence,model=props.get('ro.product.vendor.model',''),
            note='用户提供的本机 HAL 导出；未核验导出文件与此 ROM 的对应关系，不代表整条硬件链已验证'),info
    raise Unsupported('未取得本机 com.oplus.packageName 的 byte 类型编号。请在这台手机执行 adb shell dumpsys media.camera，将完整输出保存为本机原厂ROM根目录下 camera_vendor_tags.txt 后重试。不要使用一加13导出代替13T。原匹配差异：'+json.dumps(errors,ensure_ascii=False))


def parse_package_tag(text):
    rows=re.findall(r'0x([0-9a-fA-F]+)\s+\(packageName\)\s+with type\s+(\d+)\s+\(([^)]+)\)\s+defined in section\s+com\.oplus(?=\s|$)',text)
    values={(int(tag,16),int(kind),label.strip()) for tag,kind,label in rows}
    if len(values)!=1:raise Unsupported('导出中没有唯一 com.oplus.packageName 定义；需要完整 dumpsys media.camera 输出')
    tag,kind,label=values.pop()
    if not 0x80000000<=tag<=0xffffffff or kind!=0 or label!='byte':
        raise Unsupported('packageName 不是有效 vendor byte tag，不能用于当前桥')
    return tag


def check_jar_declarations(base):
    source='system/system_ext/etc/permissions/privapp-permissions-oplus.xml'
    tree=ET.fromstring(base.read(source))
    wanted={'oplus-fwk':'/system_ext/framework/oplus-fwk.jar',
        'com.oplus.camera.unit.sdk':'/system_ext/framework/com.oplus.camera.unit.sdk.jar',
        'com.oplus.camera.unit.sdk.adapter':'/system_ext/framework/com.oplus.camera.unit.sdk.adapter.jar'}
    found={n.attrib.get('name'):n.attrib for n in tree.findall('library')}
    for name,path in wanted.items():
        if found.get(name,{}).get('file')!=path or not base.exists('system'+path):
            raise Unsupported('JAR 声明/文件缺失：'+name)
    if found['com.oplus.camera.unit.sdk'].get('dependency')!='oplus-fwk':raise Unsupported('unit SDK 缺少 fwk 依赖')
    return dict(libraries=found,bootclasspath_modified=False,miui_jar_modified=False,
        runtime_loaded=False,note='沿用共享库 XML 方式；仅声明/文件校验，不等于验证运行时 ClassLoader')


def patch_codec_include(data,href):
    root=ET.fromstring(data)
    includes=[n for n in root.findall('Include') if n.get('href')==href]
    if len(includes)>1:raise Unsupported('杜比 Include 重复')
    if includes:return data
    # Retain every byte/comment of the parent config except the insertion.
    end=b'</'+root.tag.encode('ascii')+b'>'
    if data.count(end)!=1:raise Unsupported('不支持的 codec XML 根结构')
    return data.replace(end,('    <Include href="'+href+'" />\n').encode()+end)


def dolby_bundle(donor,profile):
    profile=profile or {}
    # Existing OP13 aliases are historical, tested paths, not a generic panel
    # rule. Unknown profiles stop before this stage.
    paths=['my_product/etc/dolby_vision.cfg','product/etc/dolby_vision.cfg']
    source=donor.find_any(paths) if hasattr(donor,'find_any') else next((p for rel in paths if (p:=donor.find(rel))),None)
    if not source:raise Unsupported('缺少当前硬件原厂公共杜比 cfg；不拿兜底机型的屏参')
    codec=donor.find('odm/etc/media_codecs_dolby_vision.xml')
    parent=donor.find('vendor/etc/media_codecs.xml')
    if not codec or not parent:raise Unsupported('缺少当前硬件的杜比 codec 定义或 vendor codec 主表')
    data=codec.read_bytes();tree=ET.fromstring(data)
    if tree.tag!='Included':raise Unsupported('杜比 codec 文件根结构不是 Included')
    entries=tree.findall('./Decoders/MediaCodec')+tree.findall('./Encoders/MediaCodec')
    names=[e.get('name') for e in entries]
    if len(names)!=len(set(names)):raise Unsupported('杜比 codec 定义重复')
    if not {'c2.qti.dv.decoder','c2.qti.dv.encoder'}<=set(names):raise Unsupported('原厂没有同时声明杜比解码/编码，不虚构能力')
    parent_data=parent.read_bytes();parent_tree=ET.fromstring(parent_data)
    if any(n.get('name') in names for n in parent_tree.findall('.//MediaCodec')):
        raise Unsupported('父 codec 表已经有杜比条目，需要目标化合并，不能重复声明')
    from core import load_rules
    # Keep generic aliases; model-directory aliases must come from this
    # hardware's own name (plus explicit aliases of the matched profile).
    props=donor.properties()
    models={props.get('ro.product.vendor.model','')} | set(profile.get('dolby_model_aliases',[]))
    models={m for m in models if re.fullmatch(r'[A-Za-z0-9_.-]+',m)}
    destinations={r['destination'] for r in load_rules()['files'] if r['destination'].endswith('dolby_vision.cfg')
                  and not re.search(r'/dolby/display/[^/]+/dolby_vision\.cfg$',r['destination'])}
    destinations.update('system/odm/etc/dolby/display/'+m+'/dolby_vision.cfg' for m in models)
    payload={rel:source.read_bytes() for rel in destinations}
    payload['system/vendor/etc/media_codecs_dolby_vision_playback.xml']=data
    codec_payload,codec_audit=codec_tree(donor,data)
    payload.update(codec_payload)
    return payload,dict(cfg_source=str(source),codec_source=str(codec),parent_source=str(parent),
        codec_names=names,limits_unmodified=True,cross_device_fallback=False,codec_tree=codec_audit,
        note='所有上限原样来自当前硬件官方定义；未验证运行时 codec 服务注册、递归 Include 去重或 HDR 显示')


def codec_tree(donor,dolby):
    """Patch every selected platform root, then copy its actual Include closure."""
    platform=donor.properties().get('ro.board.platform','')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+',platform):raise Unsupported('原厂缺少有效平台名，不能猜编解码配置名')
    roots=['media_codecs.xml','media_codecs_'+platform+'.xml','media_codecs_'+platform+'_vendor.xml']
    performance=['media_codecs_performance.xml','media_codecs_performance_'+platform+'.xml','media_codecs_performance_'+platform+'_vendor.xml']
    payload={};sources={};visiting=set();counts={};dv_names={n.get('name') for n in ET.fromstring(dolby).iter('MediaCodec')}
    def read(name):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+\.xml',name):raise Unsupported('不安全或未支持的 codec Include 路径：'+name)
        if name in visiting:raise Unsupported('codec Include 环：'+name)
        if name in counts:return counts[name]
        if name=='media_codecs_dolby_vision_playback.xml':return {n:1 for n in dv_names}
        path=donor.find('vendor/etc/'+name)
        if not path:raise Unsupported('原厂 codec Include 缺件：'+name)
        data=path.read_bytes();tree=ET.fromstring(data);seen={};visiting.add(name)
        for element in tree.iter('MediaCodec'):
            key=element.get('name')
            if key in dv_names:seen[key]=seen.get(key,0)+1
        for node in tree.iter('Include'):
            for key,n in read(node.get('href','')).items():seen[key]=seen.get(key,0)+n
        visiting.remove(name)
        if any(n>1 for n in seen.values()):raise Unsupported('codec 重复注册杜比组件：'+name)
        payload['system/vendor/etc/'+name]=data;sources[name]=str(path);counts[name]=seen
        return seen
    patched=[]
    for name in roots:
        if not donor.find('vendor/etc/'+name):continue
        found=read(name)
        if found and set(found)!=dv_names:raise Unsupported('codec 根仅含部分杜比组件，需定向合并：'+name)
        if not found:
            key='system/vendor/etc/'+name
            payload[key]=patch_codec_include(payload[key],'media_codecs_dolby_vision_playback.xml');patched.append(name)
            counts[name]={n:1 for n in dv_names}
    for name in performance:
        if donor.find('vendor/etc/'+name):read(name)
    return payload,dict(platform=platform,patched_roots=patched,sources=sources,include_closure=True)


def target_properties(original,os4,donor,profile):
    old=original.decode('utf-8-sig');dp=donor.properties();tp=os4.properties();edits=[]
    # Do not copy the old developer's watermark model into another handset.
    key='ro.vendor.oplus.market.name'
    value=dp.get(key) or dp.get('ro.product.marketname') or dp.get('ro.product.vendor.model')
    if not value:raise Unsupported('原厂缺少水印机型名及型号，不冒充一加13')
    replacements={key:value}
    for key in ['persist.vendor.camera.privapp.list','vendor.camera.aux.packagelist']:
        values=[]
        existing=re.search(r'(?m)^'+re.escape(key)+r'=(.*)$',old)
        for raw in [tp.get(key,''),dp.get(key,''),existing[1] if existing else '', 'com.oplus.camera']:
            for item in raw.strip().split(','):
                if item and item not in values:values.append(item)
        replacements[key]=','.join(values)
    for key,value in replacements.items():
        pattern=r'(?m)^'+re.escape(key)+r'=([^\r\n]*)'
        matches=re.findall(pattern,old)
        if len(matches)!=1:raise Unsupported('目标 prop 必须唯一存在：'+key)
        if matches[0]!=value:
            old=re.sub(pattern,lambda _:key+'='+value,old);edits.append(dict(key=key,old=matches[0],new=value))
    return old.replace('\r\n','\n').encode('utf-8'),dict(edits=edits,
        experimental=not profile or profile.get('experimental',False),
        note='机型名/辅助摄像头并集无需vendor-tag白名单；其余OP13属性配方沿用，未知硬件需实际验证')
