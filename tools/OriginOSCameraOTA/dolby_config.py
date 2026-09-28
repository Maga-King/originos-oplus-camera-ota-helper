"""Native Dolby declarations, not a required previously installed repair XML.

Keep hardware limits from the selected stock/vendor/odm. Never promote commented
codec examples, import another model's display profile, or replace whole roots.
"""
from __future__ import annotations
import copy,posixpath,re,xml.etree.ElementTree as ET
from native import Unsupported

INCLUDE='media_codecs_dolby_vision_playback.xml'
EXPECTED={'c2.qti.dv.decoder':'Decoders','c2.qti.dv.decoder.secure':'Decoders',
          'c2.qti.dv.encoder':'Encoders'}
REQUIRED={'c2.qti.dv.decoder','c2.qti.dv.encoder'}

def parse(data,path):
    try:tree=ET.fromstring(data)
    except ET.ParseError as exc:raise Unsupported(f'Dolby XML 格式异常：{path}：{exc}') from exc
    if tree.tag not in ('MediaCodecs','Included'):
        raise Unsupported('Dolby XML 根节点必须是 MediaCodecs / Included：'+path)
    return tree

def declarations(tree):
    result={}
    for section in ('Decoders','Encoders'):
        for node in tree.findall(section+'/MediaCodec'):
            name=node.get('name')
            if (name in EXPECTED and EXPECTED[name]==section and
                node.get('type')=='video/dolby-vision' and node.get('update')!='true'):
                result[name]=copy.deepcopy(node)
    return result

def included_xml(nodes):
    root=ET.Element('Included')
    for section in ('Decoders','Encoders'):
        selected=[node for name,node in nodes.items() if EXPECTED[name]==section]
        if selected:
            parent=ET.SubElement(root,section)
            parent.extend(copy.deepcopy(selected))
    ET.indent(root,space='    ')
    return ET.tostring(root,encoding='utf-8',xml_declaration=True)+b'\n'

def build_dolby(sources,put,log=print):
    props=sources.hardware_properties()
    platform=props.get('ro.board.platform','') or props.get('ro.vendor.qti.soc_name','')
    if not re.fullmatch(r'[A-Za-z0-9_-]+',platform):platform=''
    candidates=['vendor/etc/media_codecs.xml']
    if platform:candidates += [f'vendor/etc/media_codecs_{platform}.xml',f'vendor/etc/media_codecs_{platform}_vendor.xml']
    files={};absent=set();warnings=[];origins={};pool={}
    def read(rel):
        if rel in absent:return None
        if rel not in files:
            found=sources.resolve('stock',rel,required=False)
            if not found:absent.add(rel);return None
            data=found.read_bytes();files[rel]=(data,parse(data,rel),str(found))
        return files[rel]
    def collect(rel,seen=None):
        seen=set() if seen is None else seen
        if rel in seen:return {}
        seen.add(rel);file=read(rel)
        if not file:return {}
        result=declarations(file[1])
        for name in result:origins.setdefault(name,rel)
        for node in file[1].iter('Include'):
            href=node.get('href','')
            if not re.fullmatch(r'[A-Za-z0-9_./-]+',href) or href.startswith('/') or '..' in href.split('/'):
                warnings.append('未跟随非标准 codec Include：'+rel+' -> '+href);continue
            child=posixpath.join(posixpath.dirname(rel),href)
            for name,definition in collect(child,seen).items():result.setdefault(name,definition)
        return result
    # Standard OPlus ODM filename differs from the old generated vendor filename.
    # Read active declarations only; comments in sun_vendor.xml are NOT features.
    for rel in ('vendor/etc/'+INCLUDE,'odm/etc/media_codecs_dolby_vision.xml',
                'vendor/etc/media_codecs_dolby_vision.xml','odm/etc/'+INCLUDE):
        for name,node in collect(rel).items():pool.setdefault(name,node)
    roots=[];reachable={}
    for rel in candidates:
        file=read(rel)
        if not file or file[1].tag!='MediaCodecs':continue
        roots.append(rel);reachable[rel]=collect(rel)
        for name,node in reachable[rel].items():pool.setdefault(name,node)
    if not roots:raise Unsupported('没有找到本机 codec 根 XML；请补 vendor/etc/media_codecs*.xml，不能编造完整编解码配置')
    missing=REQUIRED-set(pool)
    if missing:
        raise Unsupported('本机原厂配置缺少有效 Dolby 声明：'+', '.join(sorted(missing))+
            '。已查 vendor/odm 标准文件与原生 Include 链；请补原厂 media_codecs_dolby_vision.xml，不能用注释样例或其他机型能力冒充。')
    additions=[]
    for rel in roots:
        data,tree,source=files[rel]
        needs={name:node for name,node in pool.items() if name not in reachable[rel]}
        if not needs:continue
        href=INCLUDE
        # Existing partial DV declarations must not be duplicated. Separate the
        # missing subset per root (decoder may be present, encoder absent).
        existing=files.get(posixpath.join(posixpath.dirname(rel),INCLUDE))
        if set(needs)!=set(pool) or (existing and set(declarations(existing[1]))!=set(needs)):
            href='media_codecs_mio_dolby_'+posixpath.basename(rel)
        include_rel=posixpath.join(posixpath.dirname(rel),href)
        definitions=included_xml(needs)
        put('system/'+include_rel,definitions,'本机 Dolby 声明增量：'+', '.join(sorted({origins[n] for n in needs})))
        if href!=INCLUDE and not existing:
            # Repair a stale generated include while preserving the inline codec.
            data=re.sub(rb"(<Include\b[^>]*\bhref\s*=\s*)([\"'])"+INCLUDE.encode()+rb"\2",
                        lambda m:m[1]+m[2]+href.encode()+m[2],data)
            tree=parse(data,rel)
        if not any(node.get('href')==href for node in tree.iter('Include')):
            if data.count(b'</MediaCodecs>')!=1:raise Unsupported('codec XML 根节点关闭标记异常：'+rel)
            data=data.replace(b'</MediaCodecs>',f'    <Include href="{href}" />\n</MediaCodecs>'.encode())
        parse(data,rel);put('system/'+rel,data,source+' + 本机 Dolby 缺失项')
        additions.append({'root':rel,'include':include_rel,'codecs':sorted(needs)})
    # Keep each actual hardware-specific path/value, no OnePlus13 cfg fallback.
    configs=[]
    for directory in ('system/etc','system_ext/etc','product/etc','vendor/etc','vendor/persist/display','odm/etc'):
        for rel,found in sources.named_files('stock',directory,'dolby_vision.cfg'):
            dest=rel if rel.startswith('system/') else 'system/'+rel
            put(dest,found.read_bytes(),str(found));configs.append(rel)
    if not configs:raise Unsupported('缺少本机 dolby_vision.cfg；请提供原厂显示配置，不使用其他机型文件替代')
    log('Dolby 取材：'+', '.join(sorted(set(origins.values()))))
    return {'roots':roots,'configs':configs,'definitions':{n:origins[n] for n in pool},
            'added':additions,'warnings':warnings,'external_brightness_module_required':False,
            'old_repair_xml_required':False,'runtime_validated':False}
