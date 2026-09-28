"""OriginOS camera native rules: relocate within named functions, preserve ABI guards."""
from __future__ import annotations
import io,json,struct,subprocess
from pathlib import Path
from capstone import Cs,CS_ARCH_ARM64,CS_MODE_ARM
from elftools.elf.elffile import ELFFile
from media_patch import ROOT,Elf,Unsupported,branch,resolve
from native import allocate_rx,masked_word
from advanced import replace_dynamic_string,vtable_slot

ASSETS=ROOT/'assets'

class Mapper:
    def __init__(self,old,new):self.old=old;self.new=new;self.records=[]
    def at(self,address,before=6,after=8):
        if address in self.old.plt:return resolve(self.new,self.old.plt[address])
        name,start,size=self.old.containing(address)
        if name not in self.new.symbols:raise Unsupported('目标缺少函数：'+name)
        ns,nz=self.new.symbols[name];relative=address-start
        left=min(before,relative//4);right=min(after,(size-relative)//4)
        raw=self.old.read(address-left*4,(left+right)*4)
        expected=[masked_word(x[0]) for x in struct.iter_unpack('<I',raw)]
        body=self.new.read(ns,nz);matches=[]
        for off in range(0,len(body)-len(raw)+1,4):
            candidate=body[off:off+len(raw)]
            if [masked_word(x[0]) for x in struct.iter_unpack('<I',candidate)]==expected:
                matches.append(ns+off+left*4)
        if len(matches)!=1:raise Unsupported(f'函数上下文不唯一/寄存器变化：{name}+0x{relative:x}，匹配{len(matches)}处')
        result=matches[0]
        self.records.append({'function':name,'old':hex(address),'new':hex(result)})
        return result
    def exact_function(self,name):
        if name not in self.old.symbols or name not in self.new.symbols:raise Unsupported('缺少ABI证据函数：'+name)
        a,z=self.old.symbols[name];b,n=self.new.symbols[name]
        if z!=n or self.old.read(a,z)!=self.new.read(b,n):raise Unsupported('对象/调用ABI证据变化：'+name)

def assemble(name,toolchain,out,base,defs,macros=()):
    out.mkdir(parents=True,exist_ok=True);obj=out/(name+'.o');exe=out/(name+'.elf')
    subprocess.run([str(toolchain/'clang.exe'),'--target=aarch64-linux-android35',
        *['-D'+m for m in macros],'-I'+str(out),'-c',str(ASSETS/(name+'.S')),'-o',str(obj)],check=True,capture_output=True)
    subprocess.run([str(toolchain/'ld.lld.exe'),'-Ttext='+hex(base),'--entry=0',
        *[f'--defsym={k}={hex(v)}' for k,v in defs.items()],str(obj),'-o',str(exe)],check=True,capture_output=True)
    e=ELFFile(io.BytesIO(exe.read_bytes()))
    return e.get_section_by_name('.text').data(),{s.name:s['st_value'] for s in e.get_section_by_name('.symtab').iter_symbols()}

def payload(image,data,name,cc,out,defs,macros=()):
    # First link determines size; second link uses a verified RX tail allocation.
    provisional,_=assemble(name,cc,out,0x100000,defs,macros)
    offset,va=allocate_rx(image,data,len(provisional))
    code,symbols=assemble(name,cc,out,va,defs,macros)
    if len(code)!=len(provisional):raise Unsupported('链接布局改变，不能使用预分配空间')
    data[offset:offset+len(code)]=code
    return symbols

def edit(image,data,site,value):
    off=image.offset(site,len(value));data[off:off+len(value)]=value

def patch_server(target,package_tag,xpan_sizes,cc,out):
    if not isinstance(package_tag,int) or not 0x80000000<=package_tag<=0xffffffff:
        raise Unsupported('缺少可靠的 packageName vendor tag 编号')
    old=Elf((ASSETS/'baseline/cameraserver').read_bytes());new=Elf(target);m=Mapper(old,new)
    for getter in ['_ZNK7android13CameraService11BasicClient14getPackageNameEv',
                   '_ZNK7android13CameraService11BasicClient12getClientUidEv']:
        m.exact_function(getter)
    site=m.at(0x2cbd58);raw=m.at(0x3ed07c,before=0,after=12);fil=m.at(0x31b51c)
    for address,expected in [(site,'00013fd6'),(raw,'3f2303d5')]:
        if new.read(address,4)!=bytes.fromhex(expected):raise Unsupported('原始注入指令变化')
    definitions={k:m.at(v) for k,v in {
        'origin_memcmp':0x51f600,'origin_update_bytes':0x51ea48,'origin_log':0x51e568,
        'origin_round_resume':0x3ed080,'origin_filter_parameters':0x3fc688,'origin_find_const':0x5201a0}.items()}
    sizes=sorted(set(tuple(x) for x in xpan_sizes))
    if any(len(x)!=2 or not all(isinstance(v,int) and 0<v<65536 for v in x) for x in sizes):raise Unsupported('XPAN尺寸非法')
    out.mkdir(parents=True,exist_ok=True)
    (out/'xpan_sizes.inc').write_text('\n'.join(f'.word {w}, {h}' for w,h in sizes)+'\n',encoding='ascii')
    data=bytearray(target)
    syms=payload(new,data,'package_inject',cc,out,definitions,[f'MIO_PACKAGE_TAG={hex(package_tag)}',f'MIO_XPAN_COUNT={len(sizes)}'])
    for address,symbol,op in [(site,'origin_package_hook',0x94000000),(raw,'origin_raw_dimensions',0x14000000),(fil,'origin_filter_package',0x94000000)]:
        edit(new,data,address,branch(address,syms[symbol],op))
    stats=[0x3362c4,0x3362dc,0x336c40,0x336c50]
    for address in stats:
        mapped=m.at(address)
        # Only the known int32-range conditional checks, not unrelated UBSAN.
        decoded=next(Cs(CS_ARCH_ARM64,CS_MODE_ARM).disasm(new.read(mapped,4),mapped))
        if not decoded.mnemonic.startswith('b.'):raise Unsupported('流统计范围检查已改变')
        edit(new,data,mapped,bytes.fromhex('1f2003d5'))
    stamp=m.at(0x351fe4);dest=m.at(0x352034,before=0,after=6)
    ins=next(Cs(CS_ARCH_ARM64,CS_MODE_ARM).disasm(new.read(stamp,4),stamp))
    if ins.mnemonic!='cbz' or int(ins.op_str.split('#')[-1],16)!=dest:raise Unsupported('时间戳分支关系变化')
    edit(new,data,stamp,branch(stamp,dest))
    result=replace_dynamic_string(bytes(data),'libcamera_client.so','libsatbridge.so','DT_NEEDED')
    return result,{'mapping':m.records,'package_tag':hex(package_tag),'xpan_sizes':sizes,'runtime_tested':False}

def patch_yuv(target,name,cc,out):
    if name not in ('libandroid.so','libnativewindow.so'):raise ValueError(name)
    old=Elf((ASSETS/'baseline'/name).read_bytes());new=Elf(target);m=Mapper(old,new)
    delta=0x3a300 if name=='libandroid.so' else 0
    a=m.at(0x5864+delta);b=m.at(0x5990+delta)
    defs={k:m.at(v+delta) for k,v in {'ycbcr_resume':0x588c,'upper_resume':0x58b8,'lower_resume':0x5868,'classify_resume':0x5994}.items()}
    data=bytearray(target);syms=payload(new,data,'private_yuv',cc,out,defs)
    for address,symbol in [(a,'plane_hook'),(b,'classify_hook')]:edit(new,data,address,branch(address,syms[symbol]))
    return bytes(data),{'mapping':m.records,'provider':name,'runtime_tested':False}

def patch_video(target,gui,cc,out):
    old=Elf((ASSETS/'baseline/libstagefright_bufferqueue_helper.so').read_bytes());new=Elf(target);m=Mapper(old,new)
    symbol,_=vtable_slot(Elf(gui),'_ZTVN7android19BufferQueueConsumerE',0x78)
    if symbol!='_ZN7android19BufferQueueConsumer19allowUnlimitedSlotsEb':raise Unsupported('consumer虚表槽不是allowUnlimitedSlots')
    site=m.at(0xa8bc)
    name,old_start,old_size=old.containing(0xa8bc);new_start,new_size=new.symbols[name]
    def fields(e,a,z):
        return [(i.mnemonic,i.op_str) for i in Cs(CS_ARCH_ARM64,CS_MODE_ARM).disasm(e.read(a,z),a)
                if '[x19' in i.op_str and i.mnemonic.startswith(('ldr','str','ldp','stp'))]
    if fields(old,old_start,old_size)!=fields(new,new_start,new_size):raise Unsupported('GraphicBufferSource对象字段变化')
    if new.read(site,4)!=bytes.fromhex('00058052'):raise Unsupported('GBS被替换的分配尺寸指令变化')
    data=bytearray(target);syms=payload(new,data,'gbs_unlimited',cc,out,{})
    edit(new,data,site,branch(site,syms['origin_gbs_unlimited'],0x94000000))
    return bytes(data),{'mapping':m.records,'consumer_slot':symbol,'runtime_tested':False}

def build_sat(client,cc,out):
    out.mkdir(parents=True,exist_ok=True)
    private=replace_dynamic_string(client,'libcamera_client.so','libcamera_ori.so','DT_SONAME')
    p=out/'libcamera_ori.so';p.write_bytes(private)
    subprocess.run([str(cc/'clang.exe'),'--target=aarch64-linux-android35','-shared','-fPIC','-O2',
        '-fvisibility=hidden','-fno-stack-protector','-mno-outline-atomics','-nostdlib','-Wl,-soname,libsatbridge.so',
        '-Wl,--no-as-needed','-Wl,--allow-shlib-undefined','-Wl,-z,max-page-size=16384',
        '-o',str(out/'libsatbridge.so'),str(ASSETS/'sat_bridge.c'),str(p)],check=True,capture_output=True)
    return private,(out/'libsatbridge.so').read_bytes()
