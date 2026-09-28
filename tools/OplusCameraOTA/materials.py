"""Explicit reusable payload catalogue; never a hidden complete old module."""
from pathlib import PurePosixPath

HARDWARE_EXCEPTIONS={
    'system/odm/lib64/android.hardware.graphics.common-V7-ndk.so':'crDroid APS 配套 graphics.common V7 接口库',
    'system/vendor/lib64/android.hardware.graphics.common-V7-ndk.so':'同一 V7 接口库的 vendor 命名空间副本',
    'system/odm/lib64/libAlgoInterface.so':'已验证 crDroid APS 接口兼容组合',
    'system/odm/lib64/libAlgoProcess.so':'已验证 crDroid APS 处理兼容组合，配套 V7',
    'system/odm/lib64/libmsnativefilter.so':'N007 已修厂商判定的滤镜库',
    'system/odm/lib64/libsharebuffer_impl.so':'依赖 libui-stock/libutils-stock 的私有 sharebuffer 组合',
    'system/vendor/lib64/libui-stock.so':'sharebuffer 私有依赖，不覆盖系统 libui',
    'system/vendor/lib64/libutils-stock.so':'sharebuffer 私有依赖，不覆盖系统 libutils',
}
GENERATED_NATIVE={'system/bin/cameraserver','system/lib64/libcamera_hyp.so',
    'system/lib64/libandroid.so','system/lib64/libnativewindow.so','system/lib64/libstagefright_bufferqueue_helper.so'}
RETIRED={'system/lib64/libocg.so'}

def hardware_path(path):return path.startswith(('system/vendor/','system/odm/'))

def generated_config(path):
    return path.endswith('dolby_vision.cfg') or path=='system/odm/etc/camera/config/oplus_camera_config' or path.startswith('system/vendor/etc/media_codecs')

def bundle_reason(rule):
    path=rule['destination']
    if path in RETIRED or path in GENERATED_NATIVE or generated_config(path):return None
    if hardware_path(path):return HARDWARE_EXCEPTIONS.get(path)
    if path=='system/lib64/libsatbridge.so':return '私有桥输入；目标 ABI 核实后复用'
    if rule.get('legacy_exception'):return '用户明确允许的历史库例外，见15条旧库说明'
    if rule['policy']=='fixed':return rule['policy_reason']
    if rule.get('crd_exception'):return '本机原厂缺件时才使用的明确 crDroid 公共兼容材料'
    return None

def is_builtin(base):
    from core import ASSETS
    from pathlib import Path
    return Path(base).resolve()==(ASSETS/'materials.zip').resolve()


def payload_plan(rows,generated,candidates,base):
    """The only input to the final ZIP: resolved rows plus declared generators.

    Never enumerate the reusable bundle as a complete payload; that would
    silently reintroduce old configuration and overwrite freshly sourced files.
    """
    from core import safe_rel
    from native import Unsupported
    from pathlib import Path
    output={};omitted=[]
    for item in generated:
        path=safe_rel(item['path']);source=candidates/path
        if not source.is_file():raise Unsupported('声明生成物不存在：'+path)
        output[path]=dict(path=path,source_kind='generated',source=str(source))
    for row in rows:
        path=safe_rel(row['destination'])
        if path in RETIRED:
            omitted.append(dict(path=path,reason='退役历史库'));continue
        if path in output:continue
        if generated_config(path):
            if path=='system/odm/etc/camera/config/oplus_camera_config':raise Unsupported('缺少新生成相机配置')
            omitted.append(dict(path=path,reason='旧机型配置路径由本机生成的杜比/codec树替代'));continue
        kind=row['source_kind']
        if kind=='base':
            if hardware_path(path) and path not in HARDWARE_EXCEPTIONS:raise Unsupported('禁止内置旧硬件文件兜底：'+path)
            if not bundle_reason(row):raise Unsupported('文件没有内置兼容例外依据：'+path)
            if not base.exists(path):raise Unsupported('工具兼容材料缺件：'+path)
            kind='builtin'
        elif kind in ('donor','fallback'):
            if not Path(row['source']).is_file():raise Unsupported('原厂取材文件不存在：'+path)
            kind='stock-rom'
        else:raise Unsupported('文件尚未完成取材或生成：'+path+' / '+kind)
        output[path]=dict(path=path,source_kind=kind,source=row['source'])
        if path in HARDWARE_EXCEPTIONS and kind=='builtin':output[path]['exception_reason']=HARDWARE_EXCEPTIONS[path]
    return output,omitted
