"""Offline semantic-location patches; no fixed target addresses or ADB access."""
from __future__ import annotations
import argparse, io, json, struct, subprocess, sys
from pathlib import Path
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
from capstone.arm64 import ARM64_OP_IMM, ARM64_OP_REG
from elftools.elf.elffile import ELFFile

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parent/'OplusCameraOTA'))
from native import Elf, Unsupported, branch

WRITER='_ZN7android11MPEG4Writer11addSample_lEPNS_11MediaBufferEbjPm'
WRITE='_ZN7android11MPEG4Writer16writeOrPostErrorEiPKvm'
CONFIGURE='_ZN7android10MediaCodec9configureERKNS_2spINS_8AMessageEEERKNS1_INS_7SurfaceEEERKNS1_INS_7ICryptoEEERKNS1_INS_8hardware3cas6native4V1_012IDescramblerEEEj'
CALLS={
 'find_int':'_ZNK7android8AMessage9findInt32EPKcPi',
 'set_int':'_ZN7android8AMessage8setInt32EPKci',
 'find_string':'_ZNK7android8AMessage10findStringEPKcPNS_7AStringE',
 'set_string':'_ZN7android8AMessage9setStringEPKcS2_l',
 'string_ctor':'_ZN7android7AStringC1Ev',
 'string_cstr':'_ZNK7android7AString5c_strEv',
 'string_dtor':'_ZN7android7AStringD1Ev',
 'string_compare':'strcmp',
 'codec_name':'_ZNK7android10MediaCodec7getNameEPNS_7AStringE',
}

def instructions(elf,name):
    if name not in elf.symbols:raise Unsupported('缺少函数符号：'+name)
    start,size=elf.symbols[name]
    cs=Cs(CS_ARCH_ARM64,CS_MODE_ARM);cs.detail=True
    return list(cs.disasm(elf.read(start,size),start))

def resolve(elf,name):
    slots=[a for a,n in elf.plt.items() if n==name]
    if len(slots)==1:return slots[0]
    if not slots and name in elf.symbols:return elf.symbols[name][0]
    raise Unsupported('无法唯一定位调用目标：'+name)

def regs(i):
    return [i.reg_name(o.reg) for o in i.operands if o.type==ARM64_OP_REG]

def locate_writer(elf):
    code=instructions(elf,WRITER);target=resolve(elf,WRITE);matches=[]
    for index,i in enumerate(code):
        if i.mnemonic!='bl' or i.operands[0].imm!=target or index<5:continue
        a,b,c,d=code[index-4:index]
        # raw sample write(this,fd,data+range_offset,range_length).
        if (a.mnemonic=='mov' and regs(a)==['x3','x0'] and
            b.mnemonic=='add' and len(regs(b))==3 and regs(b)[0]=='x2' and
            c.mnemonic=='mov' and len(regs(c))==2 and regs(c)[0]=='x0' and
            d.mnemonic=='mov' and len(regs(d))==2 and regs(d)[0]=='w1' and
            code[index-5].mnemonic in ('bl','blr')):
            matches.append(i.address)
    if len(matches)!=1:raise Unsupported(f'raw sample 写入点不唯一：{len(matches)}；需要新调用布局规则')
    return matches[0],target

def locate_configure(elf):
    code=instructions(elf,CONFIGURE)
    # Preserve BTI/PAC landing instructions. Do not assume a fixed stack size.
    for i in code[:4]:
        if i.mnemonic in ('paciasp','pacibsp','bti','nop'):continue
        if i.mnemonic=='sub' and regs(i)==['sp','sp']:
            imm=i.operands[2]
            if imm.type!=ARM64_OP_IMM or imm.shift.value or imm.imm<=0 or imm.imm%16:
                break
            return i.address,imm.imm
        break
    raise Unsupported('configure 入口不是已支持的 BTI/PAC + SUB 栈布局，拒绝猜偏移')

def align(value,n=4096):return (value+n-1)&~(n-1)

def segment_slot(elf):
    segments=list(elf.elf.iter_segments())
    props=[s for s in segments if s['p_type']=='PT_GNU_PROPERTY']
    slots=[i for i,s in enumerate(segments) if s['p_type']=='PT_NOTE' and
           any(s['p_offset']==p['p_offset'] and s['p_filesz']==p['p_filesz'] for p in props)]
    if len(slots)!=1:raise Unsupported('没有可复用的重复 GNU property NOTE 槽；需新增 ELF 布局规则')
    return slots[0]

def compile_helper(toolchain,source,out,base,definitions,defines=()):
    obj=out/(source.stem+'.o');linked=out/(source.stem+'.elf')
    subprocess.run([str(toolchain/'clang.exe'),'--target=aarch64-linux-android35',
                    *['-D'+x for x in defines],'-c',str(source),'-o',str(obj)],check=True,capture_output=True)
    subprocess.run([str(toolchain/'ld.lld.exe'),'-Ttext='+hex(base),'--entry=0',
                    *[f'--defsym={k}={hex(v)}' for k,v in definitions.items()],str(obj),'-o',str(linked)],check=True,capture_output=True)
    e=ELFFile(io.BytesIO(linked.read_bytes()))
    return e.get_section_by_name('.text').data()

def patch(source:Path,out:Path,toolchain:Path):
    elf=Elf(source.read_bytes())
    if elf.elf['e_type']!='ET_DYN':raise Unsupported('输入不是共享库')
    writer_site,write_target=locate_writer(elf)
    config_site,stack=locate_configure(elf)
    calls={k:resolve(elf,v) for k,v in CALLS.items()}
    slot=segment_slot(elf)
    page=max(16384,max(s['p_align'] for s in elf.elf.iter_segments() if s['p_type']=='PT_LOAD'))
    file_at=align(len(elf.data),page);va=align(max(s['p_vaddr']+s['p_memsz'] for s in elf.elf.iter_segments() if s['p_type']=='PT_LOAD'),page)
    # All payload addresses are link-time VAs, never assumed equal to file offsets.
    out.mkdir(parents=True,exist_ok=True)
    first=compile_helper(toolchain,ROOT/'assets/dolby_rpu_writer.S',out,va,{'origin_write':write_target})
    second_at=align(va+len(first),16)
    second=compile_helper(toolchain,ROOT/'assets/thumbnail_base_layer.S',out,second_at,
                          dict(calls,configure_continue=config_site+4),[f'MIO_STACK={stack}'])
    payload=first+bytes(second_at-va-len(first))+second
    data=bytearray(elf.data)
    for site,dest,opcode in [(writer_site,va,0x94000000),(config_site,second_at,0x14000000)]:
        off=elf.offset(site,4);data[off:off+4]=branch(site,dest,opcode)
    ph=elf.elf['e_phoff']+slot*elf.elf['e_phentsize']
    struct.pack_into('<IIQQQQQQ',data,ph,1,5,file_at,va,va,len(payload),len(payload),page)
    data.extend(bytes(file_at-len(data)));data.extend(payload)
    result=Elf(bytes(data))
    if result.needed!=elf.needed:raise Unsupported('输出依赖发生意外变化')
    report={'input':str(source.resolve()),'output':'libstagefright.so','status':'patched-not-device-validated',
            'writer_site':hex(writer_site),'configure_site':hex(config_site),'stack':stack,
            'payload_va':hex(va),'payload_file_offset':hex(file_at),'payload_size':len(payload),
            'apk_modified':False,'adb_required':False,'original_dependencies_preserved':True,
            'thumbnail_scope':'Vivo thumbnail=1 + nonencrypted DV8 compat4 + exact DV decoder/MIME; HEVC Main10 SDR base only',
            'calls':{k:hex(v) for k,v in calls.items()}}
    (out/'libstagefright.so').write_bytes(data)
    (out/'media_patch_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    return report

def main():
    p=argparse.ArgumentParser(description='OriginOS 杜比封装与 vivo 缩略图离线动态修补')
    p.add_argument('--input',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
    p.add_argument('--toolchain',required=True,type=Path);a=p.parse_args()
    try:print(json.dumps(patch(a.input,a.output,a.toolchain),ensure_ascii=False,indent=2))
    except (Unsupported,subprocess.CalledProcessError) as exc:
        p.exit(2,'不能生成：'+str(exc)+'\n'+getattr(exc,'stderr',b'').decode('utf8',errors='replace'))

if __name__=='__main__':main()
