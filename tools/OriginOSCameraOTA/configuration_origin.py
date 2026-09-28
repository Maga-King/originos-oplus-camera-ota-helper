"""Camera config preserves stock lens records; no model-derived tag numbers."""
import json,re
from media_patch import Unsupported
from configuration import decode_camera
from vendor_catalog import inspect_rom

def rows_from(data):
    if data.lstrip().startswith(b'['):rows=json.loads(data.decode('utf8'))
    else:rows=decode_camera(data)
    if not isinstance(rows,list) or not all(isinstance(x,dict) and 'VendorTag' in x for x in rows):
        raise Unsupported('不是已支持的相机配置数组')
    return rows

def config_patch(data,preview_sizes):
    rows=rows_from(data);original=list(rows);sizes=set(tuple(s) for s in preview_sizes)
    enabled=[r for r in rows if r['VendorTag']=='com.oplus.feature.xpan.mode.support']
    if not enabled or all(str(r.get('Value'))!='1' for r in enabled):sizes=set()
    for r in rows:
        if 'xpan' in r['VendorTag'].lower() and r['VendorTag'].endswith('.picturesize'):
            text=str(r.get('Value',''))
            parsed=re.fullmatch(r'\s*(\d+)\s*[xX,]\s*(\d+)\s*',text)
            if not parsed:raise Unsupported('无法解析原厂XPAN尺寸：'+r['VendorTag'])
            w,h=map(int,parsed.groups());sizes.update([(w,h),(h,w)])
    key='com.oplus.feature.export.super.text.support'
    if not any(x['VendorTag']==key for x in rows) and any(x['VendorTag']=='com.oplus.feature.super.text.support.v2' and str(x.get('Value'))=='1' for x in rows):
        rows.append({'VendorTag':key,'Type':'Byte','Count':'1','Value':'1'})
    return json.dumps(rows,ensure_ascii=False,indent=2).encode(),sorted(sizes),{
        'container':'plain-json-supported-by-bundled-sdk','original_records_preserved':rows[:len(original)]==original,
        'added':rows[len(original):],'xpan_sizes':sorted(sizes)}

class StockView:
    def __init__(self,sources):self.sources=sources
    def find(self,relative):return self.sources.resolve('stock',relative,required=False)

def resolve_tag(sources):
    runtime=sources.camera_evidence();catalog=None;warning=None
    try:catalog=inspect_rom(StockView(sources))
    except (Unsupported,ValueError,OSError) as exc:warning=str(exc)
    if runtime.get('parsed'):
        value=int(runtime['package_tag'],0)
        if catalog:
            p=catalog['package']
            if (value&65535)!=p['index'] or (p['tag'] and value!=int(p['tag'],0)):
                raise Unsupported('运行时tag与所选原厂HAL声明冲突，请核对取材来源')
        return value,{'runtime':runtime,'catalog':catalog,'offline_warning':warning}
    if catalog and catalog['package'].get('tag'):
        return int(catalog['package']['tag'],0),{'catalog':catalog,'runtime':runtime}
    raise Unsupported('原厂HAL未提供可靠的最终packageName编号；可导入原厂dumpsys/开启ADB只读取材。'+(warning or '声明中的运行时分配索引不能当完整tag。'))
