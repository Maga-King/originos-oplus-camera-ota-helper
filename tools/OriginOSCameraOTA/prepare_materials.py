"""Developer-only: publish reusable components, excluding system-specific patches."""
from pathlib import Path
import argparse,json,zipfile
from ai_components import GROUPS,XMLS
from media_patch import ROOT

GENERATED={
 'system/bin/cameraserver','system/lib64/libcamera_ori.so','system/lib64/libsatbridge.so',
 'system/lib64/libandroid.so','system/lib64/libnativewindow.so','system/lib64/libstagefright.so',
 'system/lib64/libstagefright_bufferqueue_helper.so','system.prop','module.prop',
}
def prepare(stage,legacy,dest):
    from materials import HARDWARE_EXCEPTIONS
    sat=json.loads((stage.parent/'stage4_sources.json').read_text('utf8'))
    paired={x['destination'] for x in sat}
    entries={};reasons=[]
    for p in sorted(stage.rglob('*')):
        if not p.is_file():continue
        rel=p.relative_to(stage).as_posix()
        if rel in GENERATED or rel.endswith('dolby_vision.cfg') or rel.startswith('system/vendor/etc/media_codecs') or rel=='system/odm/etc/camera/config/oplus_camera_config':continue
        if rel.startswith(('system/vendor/','system/odm/')) and rel not in HARDWARE_EXCEPTIONS:
            raise ValueError('未声明的硬件固定素材：'+rel)
        fixed=(rel in paired or rel in HARDWARE_EXCEPTIONS or not rel.endswith('.so') or
               '/priv-app/' in rel or '/app/' in rel or 'libApsFaceBeautyPreview' in rel)
        entries[rel]=p.read_bytes()
        reasons.append({'path':rel,'policy':'fixed-compatible' if fixed else 'stock-first-compatible-fallback',
                        'reason':HARDWARE_EXCEPTIONS.get(rel,'配套兼容素材，目标核心库另行动态生成'),
                        'origin':str(p),'bytes':p.stat().st_size})
    with zipfile.ZipFile(legacy) as z:
        for n in z.namelist():
            if n.endswith('/') or not (n in XMLS or any(n.startswith(prefix) for prefix,_ in GROUPS.values())):continue
            if n in entries:continue
            entries[n]=z.read(n);reasons.append({'path':n,'policy':'fixed-compatible','reason':'AI完整应用组及私有库','origin':str(legacy)+'!/'+n,'bytes':len(entries[n])})
    dest.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(dest,'w',compression=zipfile.ZIP_STORED) as z:
        for name,data in sorted(entries.items()):z.writestr(name,data)
    manifest={'kind':'originos-reusable-components-not-complete-module','files':reasons,
              'apk_xpan_preview':[[1920,864],[864,1920]],'ai_groups':list(GROUPS),
              'excluded':sorted(GENERATED),'hardware_exceptions':HARDWARE_EXCEPTIONS}
    dest.with_suffix('.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf8')
    return manifest

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--stage',type=Path,required=True);p.add_argument('--legacy',type=Path,required=True)
    p.add_argument('--output',type=Path,default=ROOT/'assets/components.zip');a=p.parse_args()
    print('素材文件数',len(prepare(a.stage,a.legacy,a.output)['files']))
