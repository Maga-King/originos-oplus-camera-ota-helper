"""Windows-native ELF analysis and guarded patch rebasing. No device operations."""
from __future__ import annotations
import io
import lzma
import struct
from pathlib import Path
from elftools.elf.elffile import ELFFile
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
from capstone.arm64 import ARM64_OP_IMM


class Unsupported(RuntimeError):
    pass


class Elf:
    def __init__(self, data: bytes):
        self.data = data
        self.elf = ELFFile(io.BytesIO(data))
        if self.elf.elfclass != 64 or self.elf['e_machine'] != 'EM_AARCH64':
            raise Unsupported('仅支持 ARM64 ELF；不能混入 32 位库')
        self.symbols = {}
        for table in (self.elf.get_section_by_name('.dynsym'), self.elf.get_section_by_name('.symtab')):
            if table:
                self._symbols(table)
        debug = self.elf.get_section_by_name('.gnu_debugdata')
        if debug:
            decoder = lzma.LZMADecompressor()
            unpacked = decoder.decompress(debug.data(), max_length=64 * 1024 * 1024)
            if not decoder.eof:
                raise Unsupported('mini debug 超过安全大小限制')
            mini = ELFFile(io.BytesIO(unpacked))
            self._symbols(mini.get_section_by_name('.symtab'))
        self.plt = {}
        plt = self.elf.get_section_by_name('.plt')
        reloc = self.elf.get_section_by_name('.rela.plt')
        if plt and reloc and reloc.num_relocations():
            stride, remain = divmod(plt['sh_size'] - 32, reloc.num_relocations())
            if remain or stride not in (16, 24):
                raise Unsupported('无法识别 PLT 布局')
            dyn = self.elf.get_section(reloc['sh_link'])
            for i, r in enumerate(reloc.iter_relocations()):
                self.plt[plt['sh_addr'] + 32 + i * stride] = dyn.get_symbol(r['r_info_sym']).name
        note = self.elf.get_section_by_name('.note.gnu.build-id')
        self.build_id = next(note.iter_notes())['n_desc'] if note else ''
        self.needed = []
        self.soname = None
        dynamic = self.elf.get_section_by_name('.dynamic')
        if dynamic:
            for tag in dynamic.iter_tags():
                if tag.entry.d_tag == 'DT_NEEDED':
                    self.needed.append(tag.needed)
                if tag.entry.d_tag == 'DT_SONAME':
                    self.soname = tag.soname

    def _symbols(self, table):
        if table:
            for s in table.iter_symbols():
                if s['st_info']['type'] == 'STT_FUNC' and s['st_size'] and s['st_shndx'] != 'SHN_UNDEF':
                    self.symbols[s.name] = (int(s['st_value']), int(s['st_size']))

    def offset(self, va, size=1):
        for p in self.elf.iter_segments():
            if p['p_type'] == 'PT_LOAD' and p['p_vaddr'] <= va and va + size <= p['p_vaddr'] + p['p_filesz']:
                return int(p['p_offset'] + va - p['p_vaddr'])
        raise Unsupported(f'地址 0x{va:x} 不在文件映射内')

    def read(self, va, size):
        o = self.offset(va, size)
        return self.data[o:o + size]

    def containing(self, va):
        items = [(n, a, z) for n, (a, z) in self.symbols.items() if a <= va < a + z]
        if not items:
            raise Unsupported(f'缺少 0x{va:x} 的函数符号，拒绝猜测')
        return min(items, key=lambda v: v[2])

    def describe(self):
        return dict(build_id=self.build_id, needed=self.needed, soname=self.soname,
                    functions=len(self.symbols), bytes=len(self.data))


def branch(pc, target, opcode=0x14000000):
    delta = target - pc
    if delta % 4 or not -(1 << 27) <= delta < (1 << 27):
        raise Unsupported('分支越界或未对齐')
    return struct.pack('<I', opcode | ((delta // 4) & 0x3ffffff))


def masked_word(word):
    if word & 0x7c000000 == 0x14000000:
        return word & 0xfc000000
    if word & 0x7e000000 == 0x34000000 or word & 0xff000010 == 0x54000000:
        return word & ~0x00ffffe0
    if word & 0x7e000000 == 0x36000000:
        return word & ~0x0007ffe0
    if word & 0x1f000000 == 0x10000000:
        return word & 0x9f00001f
    return word


class Rebaser:
    def __init__(self, old: Elf, new: Elf):
        self.old, self.new = old, new
        self.new_plt = {name: a for a, name in new.plt.items()}
        self.mapping = []

    def locate(self, va, guard=True, before=8, after=9):
        if va in self.old.plt:
            name = self.old.plt[va]
            if name not in self.new_plt:
                raise Unsupported('目标缺少 PLT 符号：' + name)
            return self.new_plt[name]
        name, start, size = self.old.containing(va)
        if name not in self.new.symbols:
            raise Unsupported('目标缺少同签名函数：' + name)
        new_start, new_size = self.new.symbols[name]
        relative = va - start
        if relative + 4 > new_size:
            raise Unsupported('函数缩短，原入口已越界：' + name)
        candidate = new_start + relative
        if guard:
            # Guards instruction/register context, not file hashes. A match is
            # necessary but NOT a proof of entire-function semantic equivalence.
            left = min(before, relative // 4)
            right = min(after, (size-relative)//4, (new_size-relative)//4)
            a = self.old.read(va - left*4, (left+right)*4)
            b = self.new.read(candidate - left*4, len(a))
            aa = [masked_word(x[0]) for x in struct.iter_unpack('<I', a)]
            bb = [masked_word(x[0]) for x in struct.iter_unpack('<I', b)]
            if aa != bb:
                raise Unsupported(f'函数上下文/寄存器变化，需新增规则：{name} +0x{relative:x}')
        self.mapping.append(dict(symbol=name, old=hex(va), new=hex(candidate), guarded=guard))
        return candidate


def patch_basic(target: bytes, baseline: bytes, kind: str):
    """Only the two bounded rules proven here; never returns a flashable module."""
    old, new = Elf(baseline), Elf(target)
    mapping = Rebaser(old, new)
    data = bytearray(target)
    edits = []
    if kind != 'cameraserver':
        raise Unsupported('该文件尚无可独立执行的规则')
    # RAW gate is a DRAFT intermediate. Full XPAN gate must supersede it later.
    for address, expected, replacement, title in [
        (0x2d084c, bytes.fromhex('d6030036'), bytes.fromhex('1f2003d5'), 'N002 RAW gate（待合入 XPAN）'),
        (0x2d08ac, bytes.fromhex('5f2d0071'), bytes.fromhex('5f0d0071'), 'N002 RAW capability'),
    ]:
        if old.read(address, 4) != expected:
            raise Unsupported('内置基线不符合规则')
        dest = mapping.locate(address)
        if new.read(dest, 4) != expected:
            # Branch PC-relative immediate may change, but this specific gate's
            # original branch target must still map to the same semantic site.
            raise Unsupported(title + ' 原指令变化，拒绝覆盖')
        offset = new.offset(dest, 4)
        data[offset:offset+4] = replacement
        edits.append(dict(rule=title, va=hex(dest), before=expected.hex(), after=replacement.hex()))
    # Do not include unrelated PC-relative CFI lookup-table addresses preceding
    # the timestamp block. Keep the actual compare/status branch context.
    dest = mapping.locate(0x23ea28, before=6, after=8)
    original = new.read(dest, 4)
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    md.detail = True
    ins = next(md.disasm(original, dest))
    if ins.mnemonic != 'cbz' or ins.op_str.split(',')[0] != 'x21':
        raise Unsupported('N001 时间戳分支结构变化')
    jump = ins.operands[-1].imm
    if jump != mapping.locate(0x23ea78, before=0, after=6):
        raise Unsupported('N001 分支目标不同')
    replacement = branch(dest, jump)
    off = new.offset(dest, 4)
    data[off:off+4] = replacement
    edits.append(dict(rule='N001 非递增时间戳', va=hex(dest), before=original.hex(), after=replacement.hex()))
    Elf(bytes(data))
    return bytes(data), dict(edits=edits, mapping=mapping.mapping, runtime_tested=False,
                            complete_camera_patch=False, warning='中间产物，缺少 SAT/XPAN，不可刷入')


def audit_native(target: bytes, baseline: bytes, sites):
    old, new = Elf(baseline), Elf(target)
    r = Rebaser(old, new)
    rows = []
    for name, address in sites:
        try:
            window = {'before':6,'after':8} if address == 0x23ea28 else {}
            mapped = r.locate(address, **window)
            rows.append(dict(rule=name, state='定位成功，待语义/ABI复核', old=hex(address), new=hex(mapped)))
        except Exception as e:
            rows.append(dict(rule=name, state='阻止自动写入', error=str(e)))
    return new.describe(), rows


def allocate_rx(image: Elf, data: bytearray, size: int):
    segments=list(image.elf.iter_segments())
    choices=[]
    for index, seg in enumerate(segments):
        if seg['p_type']!='PT_LOAD' or seg['p_flags']!=5 or seg['p_filesz']!=seg['p_memsz']:continue
        end=int(seg['p_offset']+seg['p_filesz']);start=(end+31)&~31
        va=int(seg['p_vaddr']+start-seg['p_offset'])
        stop=start+size
        if stop>len(data) or any(data[end:stop]):continue
        if any(s['p_type']=='PT_LOAD' and s is not seg and
               s['p_offset']<stop and s['p_offset']+s['p_filesz']>end for s in segments):continue
        if any(s['p_type']=='PT_LOAD' and s is not seg and s['p_vaddr']<va+size
               and s['p_vaddr']+s['p_memsz']>seg['p_vaddr']+seg['p_memsz'] for s in segments):continue
        # Zero bytes in a named section are not necessarily padding.
        if any(s['sh_type']!='SHT_NOBITS' and s['sh_size'] and s['sh_offset']<stop
               and s['sh_offset']+s['sh_size']>end for s in image.elf.iter_sections()):continue
        choices.append((index,seg,start,va,stop))
    if not choices:raise Unsupported('没有足够且无节区占用的 RX 尾部空洞')
    index,seg,start,va,stop=choices[0]
    header=image.elf['e_phoff']+index*image.elf['e_phentsize']
    length=stop-seg['p_offset']
    struct.pack_into('<Q',data,header+32,length)
    struct.pack_into('<Q',data,header+40,length)
    return start,va


def relocate_branch(word, old_pc, new_pc, target):
    delta=target-new_pc
    if word & 0x7c000000 == 0x14000000:
        return branch(new_pc,target,word&0xfc000000)
    if word & 0x7e000000 == 0x34000000 or word & 0xff000010 == 0x54000000:
        bits,mask=19,0x00ffffe0
    elif word & 0x7e000000 == 0x36000000:
        bits,mask=14,0x0007ffe0
    else:raise Unsupported(f'不支持的跳板重定位 0x{old_pc:x}')
    if delta%4 or not -(1<<(bits+1))<=delta<(1<<(bits+1)):raise Unsupported('条件分支越界')
    return struct.pack('<I',(word&~mask)|(((delta//4)&((1<<bits)-1))<<5))


def patch_yuv(target:bytes,baseline:bytes,helper:bytes,kind:str):
    specs={'libnativewindow.so':(0x8864,0x8990,0xac80),
           'libandroid.so':(0x40d20,0x40e4c,0x46600)}
    if kind not in specs:raise Unsupported('不是 YUV 提供者')
    hook,classifier,old_base=specs[kind]
    old,new=Elf(baseline),Elf(target);mapper=Rebaser(old,new)
    new_hook,new_classifier=mapper.locate(hook),mapper.locate(classifier)
    if old.read(hook,4)!=new.read(new_hook,4) or old.read(classifier,4)!=new.read(new_classifier,4):
        raise Unsupported('被覆盖指令不一致')
    data=bytearray(target)
    file_start,new_base=allocate_rx(new,data,len(helper))
    patched=bytearray(helper)
    md=Cs(CS_ARCH_ARM64,CS_MODE_ARM);md.detail=True
    for offset in range(0,len(helper),4):
        raw=helper[offset:offset+4]
        if raw==b'\0'*4:continue
        word=struct.unpack('<I',raw)[0]
        ins=next(md.disasm(raw,old_base+offset),None)
        if ins is None:raise Unsupported('跳板有无法解码的指令')
        isbranch=(ins.mnemonic in ('b','bl','cbz','cbnz','tbz','tbnz') or ins.mnemonic.startswith('b.'))
        if isbranch:
            target_old=ins.operands[-1].imm
            target_new=(new_base+target_old-old_base if old_base<=target_old<old_base+len(helper)
                        else mapper.locate(target_old))
            patched[offset:offset+4]=relocate_branch(word,old_base+offset,new_base+offset,target_new)
        elif ins.mnemonic in ('adr','adrp') or (word&0x3b000000)==0x18000000:
            raise Unsupported('跳板出现尚未实现的地址/字面量重定位')
    data[file_start:file_start+len(helper)]=patched
    edits=[]
    for va,dest in [(new_hook,new_base),(new_classifier,new_base+32)]:
        off=new.offset(va,4);replacement=branch(va,dest)
        edits.append(dict(va=hex(va),before=data[off:off+4].hex(),after=replacement.hex()))
        data[off:off+4]=replacement
    check=Elf(bytes(data))
    if check.read(new_base,len(helper))!=bytes(patched):raise Unsupported('输出跳板映射验证失败')
    return bytes(data),dict(rule='N004',provider=kind,helper_va=hex(new_base),
        mapping=mapper.mapping,edits=edits,runtime_tested=False,
        warning='仅本地静态验证；与其他补丁组合前不可单独刷入')
