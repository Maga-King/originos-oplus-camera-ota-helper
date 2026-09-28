"""Restore complete companion groups from the reusable distribution assets."""
from pathlib import Path, PurePosixPath
import argparse,json,zipfile

GROUPS={
 'AIUnit':('system/system_ext/priv-app/AIUnit/','AIUnit.apk'),
 'StdID':('system/system_ext/priv-app/StdID/','StdID.apk'),
 'VideoGallery':('system/system_ext/priv-app/VideoGallery/','VideoGallery.apk'),
 'GalleryPermissions':('system/system_ext/app/MioOplusGalleryPermissions/','MioOplusGalleryPermissions.apk'),
}
XMLS=[f'system/system_ext/etc/{folder}/{name}' for folder,name in [
 ('default-permissions','default-permissions-mio-op13-gallery-ai.xml'),
 ('permissions','privapp-permissions-mio-op13-gallery-ai.xml'),
 ('sysconfig','hiddenapi-mio-op13-gallery-ai.xml')]]

def supplement(bundle:Path,module:Path):
    with zipfile.ZipFile(bundle) as z:
        members=z.namelist()
        required=[prefix+apk for prefix,apk in GROUPS.values()]+XMLS
        missing=[n for n in required if n not in members]
        if missing:raise ValueError('素材缺少 AI 完整组件：'+', '.join(missing))
        picked=[n for n in members if not n.endswith('/') and
                (n in XMLS or any(n.startswith(prefix) for prefix,_ in GROUPS.values()))]
        if len(picked)!=len(set(picked)):raise ValueError('素材中存在重复目标路径')
        for n in picked:
            p=PurePosixPath(n)
            if p.is_absolute() or '..' in p.parts or '\\' in n:raise ValueError('非法素材路径：'+n)
        report=[]
        for n in picked:
            target=module/n;target.parent.mkdir(parents=True,exist_ok=True)
            # Keep OriginOS-specific authority/permission edits already present.
            if n in XMLS and target.exists():
                report.append({'path':n,'action':'preserve-existing-xml-needs-semantic-audit'})
                continue
            target.write_bytes(z.read(n))
            report.append({'path':n,'source':str(bundle.resolve())+'!/'+n,'action':'copied'})
    return {'components':list(GROUPS),'files':report,'runtime_status':'not-validated',
            'note':'完整搬入应用私有库；安装成功不等于联网模型服务可用。保留同名已有XML，须检查其声明覆盖。'}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--bundle',type=Path,required=True)
    p.add_argument('--module',type=Path,required=True);p.add_argument('--report',type=Path,required=True)
    a=p.parse_args();r=supplement(a.bundle,a.module)
    a.report.parent.mkdir(parents=True,exist_ok=True)
    a.report.write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf8')
    print('组件组',len(r['components']),'文件',len(r['files']))
