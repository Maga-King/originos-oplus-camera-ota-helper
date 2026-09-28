"""Package the newly compiled EXE, not the bootstrap Release executable."""
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parent.parent
dist = root / 'tools/OriginOSCameraOTA/dist/OriginOSCameraOTA'
bootstrap = root / '.materials/unpacked/OriginOSCameraOTA'
out = root / 'out'
out.mkdir(exist_ok=True)
for name in ('adb.exe','AdbWinApi.dll','AdbWinUsbApi.dll','NOTICE.txt'):
    shutil.copy2(bootstrap / name, dist / name)
exe = dist / 'OriginOSCameraOTA.exe'
result = out / 'smoke.json'
subprocess.run([str(exe), '--smoke', '--result', str(result)], check=True, timeout=60)
data = json.loads(result.read_text())
assert data['status']=='ok' and data['components'] and data['compiler'], data
for relative in ('_internal/assets/components.zip','_internal/assets/baseline/cameraserver',
                 '_internal/toolchain/bin/clang.exe','_internal/toolchain/bin/ld.lld.exe'):
    assert (dist / relative).is_file(), relative
with zipfile.ZipFile(out / 'OriginOSCameraOTA-windows-complete.zip', 'w', zipfile.ZIP_DEFLATED, compresslevel=3) as z:
    for p in dist.rglob('*'):
        if p.is_file(): z.write(p, 'OriginOSCameraOTA/' + p.relative_to(dist).as_posix())
    z.write(root / 'README.md','README.md')
    z.write(result,'构建环境检查.json')
print('完整 Windows 工具已构建，包含运行时、原生工具链、组件和 ADB；未连接手机。')
