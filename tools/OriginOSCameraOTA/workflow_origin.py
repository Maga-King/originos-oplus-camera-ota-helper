"""One offline build pipeline shared by CLI and GUI. Never installs/reboots."""
from __future__ import annotations
import argparse,datetime,json,shutil,traceback,zipfile,xml.etree.ElementTree as ET
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from media_patch import ROOT,Unsupported,patch as patch_media
from sources import Sources,Inputs,TARGET_FILES
from native_rules import patch_server,patch_yuv,patch_video,build_sat
from configuration_origin import config_patch,resolve_tag
from ai_components import GROUPS
from dolby_config import build_dolby
from module_output import normalize_author,module_properties

def module_rom_path(path):
    if path.startswith(('system/system_ext/','system/product/','system/vendor/','system/odm/')):
        return path.removeprefix('system/')
    return path

def build(inputs:Inputs,output:Path,toolchain:Path,log=print,*,author=''):
    author=normalize_author(author)
    output=output.resolve();toolchain=toolchain.resolve()
    folder=output/('OriginOS适配_'+datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    folder.mkdir(parents=True,exist_ok=False);module=folder/'module';module.mkdir()
    report={'state':'running','inputs':asdict(inputs),'stages':[],'files':[],'warnings':[],
            'runtime_validated':False,'auto_installed':False,'ai_runtime_validated':False,'module_author':author}
    def checkpoint():
        (folder/'构建报告.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    def put(rel,data,source):
        p=module/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
        report['files'].append({'path':rel,'source':source,'bytes':len(data)})
    def stage(name,func):
        log('开始：'+name);checkpoint()
        result=func();report['stages'].append({'name':name,'state':'complete'});checkpoint();log('完成：'+name)
        return result
    try:
        sources=Sources(inputs,folder/'取材缓存',log)
        bundle=ROOT/'assets/components.zip';catalog=json.loads(bundle.with_suffix('.json').read_text('utf8'))
        def take_materials():
            with zipfile.ZipFile(bundle) as z:
                for item in catalog['files']:
                    rel=item['path'];stock=None
                    if item['policy']=='stock-first-compatible-fallback':
                        stock=sources.resolve('stock',module_rom_path(rel),required=False)
                    put(rel,stock.read_bytes() if stock else z.read(rel),str(stock) if stock else '兼容素材:'+rel)
            return len(catalog['files'])
        stage('完整应用、AI组件、独立JAR与配套素材',take_materials)
        raw=sources.resolve('stock','odm/etc/camera/config/oplus_camera_config').read_bytes()
        config,sizes,config_report=config_patch(raw,catalog['apk_xpan_preview'])
        put('system/odm/etc/camera/config/oplus_camera_config',config,'本机原厂配置：保留原记录，SDK明文容器')
        report['camera_config']=config_report
        tag,tag_report=stage('本机vendor tag与镜头证据',lambda:resolve_tag(sources));report['hardware']=tag_report
        # Preserve lens configuration files whenever supplied; never invent camera IDs.
        for rel in ('odm/etc/camera/CameraHWConfiguration.config','odm/etc/camera/config/camera_unit_config'):
            found=sources.resolve('stock',rel,required=False)
            if found:
                report.setdefault('hardware_evidence_files',[]).append(str(found))
                # These are HAL-owned originals; record, don't overwrite hardware partitions.
            else:report['warnings'].append('缺少镜头辅助资料：'+rel+'；不猜Camera2物理ID')
        def server():
            data,audit=patch_server(sources.resolve('target','system/bin/cameraserver').read_bytes(),tag,sizes,toolchain,folder/'native/server')
            put('system/bin/cameraserver',data,'目标原件动态patch');report['server']=audit
        stage('相机服务：包名、RAW/XPAN、统计、时间戳',server)
        def bridge():
            private,sat=build_sat(sources.resolve('target','system/lib64/libcamera_client.so').read_bytes(),toolchain,folder/'native/bridge')
            put('system/lib64/libcamera_ori.so',private,'目标client重命名SONAME')
            put('system/lib64/libsatbridge.so',sat,'从源码重编译私有事务桥')
        stage('SAT桥与当前系统client',bridge)
        def yuv():
            for name in ('libandroid.so','libnativewindow.so'):
                data,audit=patch_yuv(sources.resolve('target','system/lib64/'+name).read_bytes(),name,toolchain,folder/'native'/name)
                put('system/lib64/'+name,data,'目标原件动态YUV补丁');report[name]=audit
        stage('YUV两份提供者',yuv)
        def video():
            name='libstagefright_bufferqueue_helper.so'
            data,audit=patch_video(sources.resolve('target','system/lib64/'+name).read_bytes(),
                sources.resolve('target','system/lib64/libgui.so').read_bytes(),toolchain,folder/'native/gbs')
            put('system/lib64/'+name,data,'目标原件动态视频缓冲补丁');report['video']=audit
        stage('60fps/慢动作消费者缓冲',video)
        def media():
            dest=folder/'native/media'
            audit=patch_media(sources.resolve('target','system/lib64/libstagefright.so'),dest,toolchain)
            put('system/lib64/libstagefright.so',(dest/'libstagefright.so').read_bytes(),'目标原件动态RPU和缩略图补丁')
            report['media']=audit
        stage('杜比封装和vivo缩略图',media)
        def dolby():
            report['dolby']=build_dolby(sources,put,log)
            report['warnings'].extend(report['dolby']['warnings'])
        stage('本机独立杜比配置',dolby)
        # Product JNI selection: use stock only as a matched contract, otherwise report fallback.
        beauty=None
        for rel in ('system_ext/lib64/libApsFaceBeautyPreviewProductJni.so','product/lib64/libApsFaceBeautyPreviewProductJni.so'):
            beauty=sources.resolve('stock',rel,required=False)
            if beauty:break
        if beauty:
            from native import Elf
            data=beauty.read_bytes();Elf(data)
            for rel in ('system/lib64/libApsFaceBeautyPreviewJni.so','system/system_ext/lib64/libApsFaceBeautyPreviewJni.so',
                        'system/system_ext/priv-app/OplusCamera/lib/arm64/libApsFaceBeautyPreviewJni.so'):put(rel,data,str(beauty))
        else:report['warnings'].append('缺本机Product美颜JNI，暂用素材内验证版本；跨机型必须检查ODM ABI')
        if inputs.target_rom and inputs.stock_rom:
            from policy_chain import broaden_offline
            policy,audit=broaden_offline(b'',SimpleNamespace(os4=inputs.target_rom,donor=inputs.stock_rom))
            put('sepolicy.rule',policy,'所选ROM具名相机链路CIL增量');report['policy']=audit
            if not audit['cil_files']:report['warnings'].append('未找到CIL；不能确认严格SELinux可用')
        else:report['warnings'].append('未提供完整双ROM目录，SELinux离线合并尚未执行；不改变手机enforcing状态')
        _finish_scripts(module,put,author)
        for p in module.rglob('*.xml'):ET.parse(p)
        for _,apk in GROUPS.values():
            if not list(module.rglob(apk)):raise Unsupported('AI APK漏包：'+apk)
        sources.save_manifest(folder/'取材来源.json')
        archive=folder/'欧加相机_OriginOS_生成模块.zip'
        with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_STORED) as z:
            for p in sorted(module.rglob('*')):
                if not p.is_file():continue
                name=p.relative_to(module).as_posix();info=zipfile.ZipInfo(name);info.create_system=3
                mode=0o100755 if name.endswith('.sh') or name=='system/bin/cameraserver' else 0o100644
                info.external_attr=mode<<16;z.writestr(info,p.read_bytes())
        shutil.copyfile(ROOT/'从零手动制作欧加相机_OriginOS.md',folder/'从零手动制作欧加相机_OriginOS.md')
        report.update(state='built-needs-device-validation',archive=str(archive));checkpoint();log('已生成（未自动安装）：'+str(archive))
        return archive,report
    except Exception as exc:
        report.update(state='failed',error=str(exc),trace=traceback.format_exc());checkpoint()
        raise


def _finish_scripts(module,put,author=''):
    put('module.prop',module_properties(author),'工具生成：用户作者/默认作者')
    put('system.prop',('debug.config.media.video.dolby_vision_suports=true\nro.vendor.camera.res.fmq.size=4194304\n'
        'ro.camera.res.fmq.size=4194304\npersist.sys.feature.hdr_vision_app=1\npersist.sys.feature.localhdr_version=2\n'
        'persist.sys.feature.uhdr.support=true\nro.vendor.display.picture_hdr.support=true\n').encode(),'明确的相机/相册门控；不冒充完整EDR')
    grants=module/'grant-camera-permissions.sh'
    text=grants.read_text('utf8').replace('for PKG in com.oplus.camera com.oneplus.gallery;',
        'for PKG in com.oplus.camera com.oneplus.gallery com.oplus.aiunit com.oplus.stdid com.coloros.video com.oplus.appplatform;')
    # PackageManager itself rejects undeclared permissions; no global grant.
    put('grant-camera-permissions.sh',text.encode(),'原脚本 + 完整AI应用组')
    service=module/'service.sh';text=service.read_text('utf8')
    text+='\n# Append, never replace the existing auxiliary-camera app lists.\nfor KEY in persist.camera.privapp.list persist.vendor.camera.privapp.list; do\n  VALUE=$(getprop "$KEY")\n  case ",$VALUE," in *,com.oplus.camera,*) ;; *)\n    NEW="${VALUE:+$VALUE,}com.oplus.camera"\n    if command -v resetprop >/dev/null 2>&1; then resetprop "$KEY" "$NEW"; else setprop "$KEY" "$NEW"; fi\n  esac\ndone\n'
    put('service.sh',text.encode(),'启动一次性权限标签及包名名单合并')

if __name__=='__main__':
    p=argparse.ArgumentParser(description='欧加相机OriginOS离线适配（开发版）')
    p.add_argument('--target',default='');p.add_argument('--stock',default='');p.add_argument('--dump',default='')
    p.add_argument('--output',type=Path,required=True);p.add_argument('--toolchain',type=Path,required=True)
    p.add_argument('--author',default='',help='module.prop 作者，留空默认科比')
    p.add_argument('--adb',action='store_true');p.add_argument('--serial',default='');p.add_argument('--adb-target',action='store_true')
    a=p.parse_args();build(Inputs(a.target,a.stock,a.adb,a.serial,a.adb_target,True,a.dump),a.output,a.toolchain,author=a.author)
