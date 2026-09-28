"""Read Qualcomm CHI tag declarations from donor ELF without executing it.

Declaration indices are NOT final Android tag IDs when section bases are zero.
No numeric base is inferred from a model, another ROM, or total section count.
"""
import re
import struct
from capstone import Cs,CS_ARCH_ARM64,CS_MODE_ARM
from native import Elf,Unsupported
from advanced import pointer_symbols

def relocations(image):
    pointers=pointer_symbols(image)
    for section in image.elf.iter_sections():
        if section.name!='.relr.dyn':continue
        cursor=None
        for (word,) in struct.iter_unpack('<Q',section.data()):
            if word&1:
                if cursor is None:raise Unsupported('RELR bitmap 前没有基址')
                for bit in range(1,64):
                    if word&(1<<bit):
                        addr=cursor+(bit-1)*8
                        pointers[addr]=('',struct.unpack('<Q',image.read(addr,8))[0])
                cursor+=63*8
            else:
                if word%8:raise Unsupported('RELR 地址未对齐')
                pointers[word]=('',struct.unpack('<Q',image.read(word,8))[0]);cursor=word+8
    return pointers

def query_info_address(image):
    names=['chi_hal_query_vendertag.cfi','chi_hal_query_vendertag']
    name=next((n for n in names if n in image.symbols),None)
    if not name:raise Unsupported('没有 CHI vendor-tag 查询入口符号')
    address,size=image.symbols[name]
    md=Cs(CS_ARCH_ARM64,CS_MODE_ARM);ins=list(md.disasm(image.read(address,size),address))
    # Resolve only ADRP + ADD materializations directly stored to output+8.
    # Unknown implementations remain unsupported instead of scanning strings.
    candidates=set()
    for a,b,c in zip(ins,ins[1:],ins[2:]):
        if a.mnemonic!='adrp' or b.mnemonic!='add' or c.mnemonic!='str':continue
        m=re.fullmatch(r'(x\d+), #0x([0-9a-f]+)',a.op_str)
        if not m:continue
        reg=m[1]
        add=re.fullmatch(reg+r', '+reg+r', #(0x[0-9a-f]+|\d+)',b.op_str)
        if add and c.op_str==reg+', [x0, #8]':
            candidates.add(int(m[2],16)+int(add[1],0))
    if len(candidates)!=1:raise Unsupported('CHI 查询入口布局未支持，不能确认声明表地址')
    return candidates.pop(),name

def decode_catalog(image,info_address,pointers):
    def ptr(address):
        if address not in pointers:raise Unsupported('缺少声明表指针重定位：'+hex(address))
        return pointers[address][1]
    def string(address):
        data=bytearray()
        for offset in range(256):
            value=image.read(address+offset,1)[0]
            if not value:
                result=data.decode('ascii')
                if not result or not re.fullmatch(r'[A-Za-z0-9_.-]+',result):raise Unsupported('非法 tag 名称')
                return result
            data.append(value)
        raise Unsupported('tag 名称缺终止符')
    start=ptr(info_address);count=struct.unpack('<I',image.read(info_address+8,4))[0]
    if not 0<count<=4096:raise Unsupported('CHI section 数量越界')
    sections=[];tags=[]
    for i in range(count):
        record=start+i*32;section=string(ptr(record))
        base,num=struct.unpack('<II',image.read(record+8,8))
        if num>65536:raise Unsupported('CHI tag 数量越界')
        if base and (base<0x80000000 or base&0xffff):raise Unsupported('不支持的 CHI section base')
        entries=ptr(record+16) if num else 0
        sections.append(dict(name=section,ordinal=i,declared_base=hex(base) if base else None,count=num))
        for j in range(num):
            entry=entries+j*32;tag=string(ptr(entry))
            kind=struct.unpack('<I',image.read(entry+8,4))[0]
            capacity=struct.unpack('<Q',image.read(entry+16,8))[0]
            if kind>5:raise Unsupported('未支持的 metadata 类型')
            tags.append(dict(name=section+'.'+tag,section=section,index=j,type=kind,
                count=capacity,tag=hex(base+j) if base else None))
    package=[t for t in tags if t['name']=='com.oplus.packageName']
    if len(package)!=1 or package[0]['type']!=0:raise Unsupported('CHI 没有唯一 byte 类型 packageName')
    return dict(sections=sections,tags=tags,package=package[0],section_count=count,
        numeric_ids_complete=all(t['tag'] is not None for t in tags),
        allowance_changed=False,
        note='只读取声明，不放行任何tag。declared_base为空表示运行时分配，段内index不能当完整编号。')

def inspect_rom(rom):
    source=rom.find('vendor/lib64/hw/com.qti.chi.override.so')
    if not source:raise Unsupported('原厂缺少 com.qti.chi.override.so')
    image=Elf(source.read_bytes());address,symbol=query_info_address(image)
    result=decode_catalog(image,address,relocations(image))
    result.update(source=str(source),query_symbol=symbol,info_address=hex(address),build_id=image.build_id)
    return result

if __name__=='__main__':
    import argparse,json
    from pathlib import Path
    from core import Rom
    p=argparse.ArgumentParser();p.add_argument('--rom',required=True);p.add_argument('--out',required=True)
    args=p.parse_args();result=inspect_rom(Rom(args.rom))
    Path(args.out).write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in ['tags','sections']},ensure_ascii=False,indent=2))
