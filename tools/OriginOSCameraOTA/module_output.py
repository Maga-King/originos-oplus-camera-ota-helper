"""User-facing module metadata and atomic ZIP export; no device operations."""
from pathlib import Path
import os,shutil,tempfile

VERSION='0.1.2-dev'
VERSION_CODE=103
DEFAULT_AUTHOR='科比'

def normalize_author(value=''):
    if not isinstance(value,str):raise ValueError('模块作者必须是文本')
    if any(ord(c)<32 or ord(c)==127 or c in '\u2028\u2029' for c in value):
        raise ValueError('模块作者只能填写一行，不能包含换行或控制字符')
    return value.strip() or DEFAULT_AUTHOR

def module_properties(author=''):
    author=normalize_author(author)
    return ('id=mio_oplus_originos_manual\nname=欧加相机 OriginOS 动态适配\n'
        f'version={VERSION}\nversionCode={VERSION_CODE}\nauthor={author}\n'
        'description=基于所选系统生成；含AI组件、独立杜比和缩略图修复。跨机型仍需实机验收。\n').encode('utf8')

def export_archive(source,destination):
    """Keep original output. Replace a user-selected destination only after copy succeeds."""
    source=Path(source).resolve();destination=Path(destination).resolve()
    if not source.is_file():raise FileNotFoundError('构建成品不存在：'+str(source))
    if source==destination or (destination.exists() and source.samefile(destination)):
        return destination
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(prefix='.mio-camera-',suffix='.tmp',dir=destination.parent,delete=False) as f:
            temporary=Path(f.name)
        shutil.copyfile(source,temporary)
        os.replace(temporary,destination)
        return destination
    finally:
        if temporary and temporary.exists():temporary.unlink()
