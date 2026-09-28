# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all
cap=collect_all('capstone')
a=Analysis(['entrypoint.py'],pathex=['../OplusCameraOTA'],
    binaries=cap[1],datas=[('assets','assets'),('toolchain','toolchain'),('licenses','licenses'),
        ('从零手动制作欧加相机_OriginOS.md','.'),('README.md','.')]+cap[0],
    hiddenimports=cap[2],hookspath=[],runtime_hooks=[],excludes=[],noarchive=False)
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='OriginOSCameraOTA',console=False,upx=False,
        disable_windowed_traceback=False)
coll=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='OriginOSCameraOTA')
