"""Restore large materials from an extracted official portable Release, offline."""
import argparse
import shutil
import sys
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

p = argparse.ArgumentParser(description='从已解压的便携 Release 恢复源码构建素材；不下载、不连接手机。')
p.add_argument('--portable', type=Path, required=True, help='包含 OriginOSCameraOTA.exe 的目录')
a = p.parse_args()
source = a.portable.resolve() / '_internal'
target = Path(__file__).resolve().parent / 'tools/OriginOSCameraOTA'
for name in ('assets', 'toolchain', 'licenses'):
    if not (source / name).is_dir():
        p.error(f'缺少 {source / name}，请完整解压 Release')
for name in ('assets', 'toolchain', 'licenses'):
    shutil.copytree(source / name, target / name, dirs_exist_ok=True)
    print('已恢复', name)
