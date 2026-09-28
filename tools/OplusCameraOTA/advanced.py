"""Explicit compatibility-bound native generators, all outputs experimental."""
import struct
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
from native import Elf, Rebaser, Unsupported, branch, allocate_rx, relocate_branch, patch_basic, masked_word


def equivalent_leaf_wrapper(old,new,name):
    """Narrow proof for the 7-instruction surfaceToSurfaceType wrapper only."""
    if name!='_ZN7android9flagtools20surfaceToSurfaceTypeERKNS_2spINS_7SurfaceEEE':
        raise Unsupported('非已支持的兼容桥 wrapper')
    oa,on=old.symbols[name];na,nn=new.symbols[name]
    if on!=28 or nn!=28:raise Unsupported('surface wrapper 长度变化')
    md=Cs(CS_ARCH_ARM64,CS_MODE_ARM);md.detail=True
    expected=[('bti','c'),('ldr','x0, [x0]'),('str','x0, [x8]'),
              ('cbz',None),('mov','x1, x8'),('b',None),('ret','')]
    callees=[]
    for e,start in [(old,oa),(new,na)]:
        ins=list(md.disasm(e.read(start,28),start))
        if len(ins)!=7:raise Unsupported('wrapper 解码不完整')
        for i,(mnemonic,op) in enumerate(expected):
            if ins[i].mnemonic!=mnemonic or (op is not None and ins[i].op_str!=op):raise Unsupported('wrapper 数据/参数流变化')
        if ins[3].op_str.split(',')[0]!='x0' or ins[3].operands[-1].imm!=start+24:
            raise Unsupported('wrapper 空指针路径变化')
        callee=e.plt.get(ins[5].operands[-1].imm)
        if callee!='_ZNK7android7RefBase9incStrongEPKv':raise Unsupported('wrapper 尾调用不再是 RefBase::incStrong')
        callees.append(callee)
    return dict(check='7 条完整指令及实参/返回对象/内部跳转/PLT 符号匹配',callee=callees[0])


def sleb(data,pos):
    value=0;shift=0
    for _ in range(10):
        if pos>=len(data):raise Unsupported('截断的 APS2')
        byte=data[pos];pos+=1;value|=(byte&127)<<shift;shift+=7
        if not byte&128:
            if byte&64:value-=1<<shift
            return value,pos
    raise Unsupported('无效 APS2 整数')


def packed_relocations(data):
    if data[:4]!=b'APS2':raise Unsupported('未知 packed relocation 格式')
    pos=4;count,pos=sleb(data,pos);offset,pos=sleb(data,pos);addend=0;done=0
    if not 0<=count<=2000000:raise Unsupported('relocation 数量越界')
    while done<count:
        size,pos=sleb(data,pos);flags,pos=sleb(data,pos)
        if size<=0 or size>count-done or flags&~15:raise Unsupported('无效 APS2 分组')
        grouped_info=flags&1;grouped_offset=flags&2;grouped_addend=flags&4;has_addend=flags&8
        if grouped_offset:offset_delta,pos=sleb(data,pos)
        if grouped_info:info,pos=sleb(data,pos)
        if has_addend and grouped_addend:addend_delta,pos=sleb(data,pos)
        for _ in range(size):
            if not grouped_offset:offset_delta,pos=sleb(data,pos)
            offset+=offset_delta
            if not grouped_info:info,pos=sleb(data,pos)
            if has_addend:
                if not grouped_addend:addend_delta,pos=sleb(data,pos)
                addend+=addend_delta
            else:addend=0
            yield offset,info>>32,info&0xffffffff,addend
        done+=size


def pointer_symbols(image):
    values={}
    for section in image.elf.iter_sections():
        records=[]
        if section['sh_type']=='SHT_RELA':
            records=((r['r_offset'],r['r_info_sym'],r['r_info_type'],r['r_addend']) for r in section.iter_relocations())
        elif section['sh_type']==0x60000002 or section.name in ('.android.rela.dyn','.android.rela'):
            records=packed_relocations(section.data())
        else:continue
        table=image.elf.get_section(section['sh_link']) if section['sh_link'] else image.elf.get_section_by_name('.dynsym')
        for offset,index,kind,addend in records:
            if index:
                symbol=table.get_symbol(index)
                values[offset]=(symbol.name,int(symbol['st_value'])+addend)
            elif kind==1027:values[offset]=('',addend)
    return values


def vtable_slot(image,vtable,slot):
    table=image.elf.get_section_by_name('.dynsym')
    matches=[s for s in table.iter_symbols() if s.name==vtable and s['st_shndx']!='SHN_UNDEF']
    if len(matches)!=1:raise Unsupported('无法唯一识别虚表 '+vtable)
    symbol=matches[0]
    if slot+24>symbol['st_size']:raise Unsupported('虚表槽越界')
    return pointer_symbols(image).get(symbol['st_value']+16+slot,('',0))


def replace_dynamic_string(data,old,new,tag_kind):
    image=Elf(data);dynamic=image.elf.get_section_by_name('.dynamic')
    strings=image.elf.get_section(dynamic['sh_link'])
    tags=[]
    for tag in dynamic.iter_tags():
        if tag.entry.d_tag==tag_kind:
            text=tag.needed if tag_kind=='DT_NEEDED' else tag.soname
            if text==old:tags.append(tag.entry.d_val)
    if len(tags)!=1:raise Unsupported(f'{tag_kind}: 无法唯一识别 {old}')
    if len(new)>len(old):raise Unsupported('动态字符串空间不足，需 ELF 重建')
    for tag in dynamic.iter_tags():
        if tag.entry.d_tag in ('DT_NEEDED','DT_SONAME','DT_RPATH','DT_RUNPATH'):
            value=tag.entry.d_val
            if tags[0]<value<=tags[0]+len(old):raise Unsupported('动态条目共享待改名字符串的后缀')
    offset=strings['sh_offset']+tags[0]
    if data[offset:offset+len(old)+1]!=old.encode()+b'\0':raise Unsupported('动态字符串原件不一致')
    # No symbol/other string may share the rewritten suffix.
    dynsym=image.elf.get_section_by_name('.dynsym')
    if any(tags[0]<=s['st_name']<=tags[0]+len(old) for s in dynsym.iter_symbols() if s.name):
        raise Unsupported('SONAME/NEEDED 与动态符号共享字符串，拒绝原位覆盖')
    result=bytearray(data);result[offset:offset+len(old)+1]=new.encode()+b'\0'*(len(old)+1-len(new))
    return bytes(result)


def relocate_helper(blob,old_base,new_base,code_size,mapper,overrides=None):
    overrides=overrides or {};out=bytearray(blob)
    md=Cs(CS_ARCH_ARM64,CS_MODE_ARM);md.detail=True
    records=[]
    for offset in range(0,code_size,4):
        raw=blob[offset:offset+4];word=struct.unpack('<I',raw)[0]
        if raw==b'\0'*4:continue
        ins=next(md.disasm(raw,old_base+offset),None)
        if ins is None:raise Unsupported('跳板解码失败')
        isbranch=ins.mnemonic in ('b','bl','cbz','cbnz','tbz','tbnz') or ins.mnemonic.startswith('b.')
        if isbranch or ins.mnemonic=='adr':
            target=ins.operands[-1].imm
            if old_base<=target<old_base+len(blob):mapped=new_base+target-old_base
            elif target in overrides:mapped=overrides[target]
            else:mapped=mapper.locate(target)
            if ins.mnemonic=='adr':
                delta=mapped-(new_base+offset)
                if not -(1<<20)<=delta<(1<<20):raise Unsupported('ADR 越界')
                bits=delta&0x1fffff
                replacement=struct.pack('<I',(word&0x9f00001f)|((bits&3)<<29)|((bits>>2)<<5))
            else:replacement=relocate_branch(word,old_base+offset,new_base+offset,mapped)
            out[offset:offset+4]=replacement
            records.append(dict(at=hex(new_base+offset),kind=ins.mnemonic,target=hex(mapped)))
        elif ins.mnemonic=='adrp' or (word&0x3b000000)==0x18000000:
            raise Unsupported('跳板存在尚未支持的字面量/页地址重定位')
    return bytes(out),records


def configure_site(mapper):
    old,new=mapper.old,mapper.new
    name,start,size=old.containing(0x2132c4)
    if name not in new.symbols:raise Unsupported('缺少同签名 configureStreamsLocked')
    ns,nz=new.symbols[name];address=ns+(0x2132c4-start)
    # Validate actual parameter origins, branch structure, and the displaced
    # load. Ignore unrelated stack-allocation size and later register choices.
    expected={0x24:0x2a0303f7,0x28:0xaa0203fc,0x2c:0x2a0103f4,
              0x30:0xaa0003f3,0x3c:0xf9417e68,0x40:0xb5000068,0x44:0xaa1c03e0}
    for offset,word in expected.items():
        if new.read(ns+offset,4)!=struct.pack('<I',word):raise Unsupported('configure 参数/寄存器布局变化')
    md=Cs(CS_ARCH_ARM64,CS_MODE_ARM);md.detail=True
    call=next(md.disasm(new.read(ns+0x34,4),ns+0x34))
    if call.mnemonic!='bl' or new.plt.get(call.operands[-1].imm)!='atrace_get_enabled_tags':
        raise Unsupported('configure 跟踪入口变化')
    inst=next(md.disasm(new.read(ns+0x38,4),ns+0x38))
    if inst.mnemonic!='tbnz' or inst.op_str.split(',')[:2]!=['w0',' #0xa']:
        raise Unsupported('configure trace 分支变化')
    mapper.mapping.append(dict(symbol=name,old=hex(0x2132c4),new=hex(address),guarded=True,
        method='精确核对 ABI 参数来源、覆盖指令与 atrace 调用，允许栈局部大小变化'))
    return address


def patch_server(target,baseline,sat,xpan,package_tag):
    if not isinstance(package_tag,int) or not 0x80000000<=package_tag<=0xffffffff:
        raise Unsupported('需要明确验证的 com.oplus.packageName vendor tag，不允许猜编号')
    initial,basic=patch_basic(target,baseline,'cameraserver')
    old,new=Elf(baseline),Elf(target);mapper=Rebaser(old,new)
    c1=mapper.locate(0x11031c);c2=mapper.locate(0x11e650);cfg=configure_site(mapper)
    # Displaced instructions and the parameter string offsets are part of the
    # confirmed calling convention, not blindly carried from a random ROM.
    for addr,word in [(c1,0xb900afe5),(c2,0xb9009fe3),(cfg,0xf9417e68)]:
        if new.read(addr,4)!=struct.pack('<I',word):raise Unsupported('SAT 覆盖指令变化')
    gate=mapper.locate(0x2d084c);normal=mapper.locate(0x2d0850);preserve=mapper.locate(0x2d08f0,before=6,after=9)
    data=bytearray(initial)
    # Reserve both helpers in a single allocation so they cannot overlap.
    xpan_offset=(len(sat)+31)&~31
    file_base,base=allocate_rx(new,data,xpan_offset+len(xpan))
    relocated,sat_edges=relocate_helper(sat,0x387200,base,0x138,mapper,
        {0x110320:c1+4,0x11e654:c2+4,0x2132c8:cfg+4})
    relocated=bytearray(relocated)
    words=struct.unpack_from('<II',sat,0xc8)
    if words!=(0x528005c1,0x72b022e1):raise Unsupported('SAT vendor tag 指令模板不符')
    struct.pack_into('<I',relocated,0xc8,0x52800001|((package_tag&0xffff)<<5))
    struct.pack_into('<I',relocated,0xcc,0x72a00001|((package_tag>>16)<<5))
    xp,xpan_edges=relocate_helper(xpan,0x387400,base+xpan_offset,len(xpan),mapper,
        {0x2d0850:normal,0x2d08f0:preserve})
    data[file_base:file_base+len(sat)]=relocated
    data[file_base+xpan_offset:file_base+xpan_offset+len(xp)]=xp
    hooks=[(c1,base),(c2,base+0x40),(cfg,base+0x80),(gate,base+xpan_offset)]
    for address,to in hooks:
        off=new.offset(address,4);data[off:off+4]=branch(address,to)
    result=replace_dynamic_string(bytes(data),'libcamera_client.so','libsatbridge.so','DT_NEEDED')
    checked=Elf(result)
    if 'libsatbridge.so' not in checked.needed or 'libcamera_client.so' in checked.needed:raise Unsupported('NEEDED 替换验证失败')
    return result,dict(basic=basic,mapping=mapper.mapping,hook_edges=[dict(at=hex(a),target=hex(b)) for a,b in hooks],
        helper_edges=sat_edges+xpan_edges,package_tag=hex(package_tag),runtime_tested=False,
        note='以新系统服务为底，不包含旧 CameraImpl 虚表；仍需真机验证')


def patch_gbs(target,baseline,gui):
    old,new=Elf(baseline),Elf(target);mapper=Rebaser(old,new)
    symbol,address=vtable_slot(Elf(gui),'_ZTVN7android19BufferQueueConsumerE',0x78)
    if symbol!='_ZN7android19BufferQueueConsumer19allowUnlimitedSlotsEb':
        raise Unsupported('当前 libgui 的 consumer +0x78 不是 allowUnlimitedSlots，拒绝旧虚表偏移')
    hook=mapper.locate(0xd840);entry=mapper.locate(0xda6c)
    resume=mapper.locate(0xda14,guard=False);finish=mapper.locate(0xda70)
    # This block includes moved C strings. Resolve ADRP+ADD pairs and compare
    # their pointed-to content instead of masking every ADD immediate.
    validate_string_block(old,new,0xd9f4,resume-(0xda14-0xd9f4),0x44)
    cave=hook+4
    # All instance-field accesses in the constructor must remain unchanged.
    name,oa,sz=old.containing(0xd840);na,ns=new.symbols[name]
    if sz!=ns:raise Unsupported('GraphicBufferSource 构造函数大小变化，需核实对象布局')
    md=Cs(CS_ARCH_ARM64,CS_MODE_ARM)
    def fields(image,a,z):
        return [(i.address-a,i.mnemonic,i.op_str) for i in md.disasm(image.read(a,z),a)
                if '[x19' in i.op_str and i.mnemonic.startswith(('ldr','str','ldp','stp'))]
    if fields(old,oa,sz)!=fields(new,na,ns):raise Unsupported('GraphicBufferSource 成员访问布局变化')
    data=bytearray(target)
    payload=b''.join(struct.pack('<I',w) for w in [0xf9407a60,0x52800021,0xf9400008,0xf9403d08,0xd63f0100,0x52800500])
    payload+=branch(cave+24,finish)
    data[new.offset(hook,4):new.offset(hook,4)+4]=branch(hook,resume)
    data[new.offset(entry,4):new.offset(entry,4)+4]=branch(entry,cave)
    data[new.offset(cave,len(payload)):new.offset(cave,len(payload))+len(payload)]=payload
    return bytes(data),dict(rule='N006',mapping=mapper.mapping,consumer_slot=symbol,runtime_tested=False,
        note='保留历史缓冲修复语义；该路径对共享编码入口生效，不宣称仅影响欧加')


def validate_string_block(old,new,a,b,size):
    md=Cs(CS_ARCH_ARM64,CS_MODE_ARM);md.detail=True
    def string_pair(image,address):
        ins=list(md.disasm(image.read(address,8),address))
        if len(ins)!=2 or ins[0].mnemonic!='adrp' or ins[1].mnemonic!='add':raise Unsupported('非地址构造对')
        if ins[0].operands[0].reg!=ins[1].operands[0].reg or ins[1].operands[0].reg!=ins[1].operands[1].reg:
            raise Unsupported('地址构造寄存器变化')
        target=ins[0].operands[1].imm+ins[1].operands[2].imm
        offset=image.offset(target);end=image.data.find(b'\0',offset,offset+512)
        if end<0:raise Unsupported('地址不是已识别的短字符串')
        return image.data[offset:end]
    for offset in range(0,size,4):
        x=struct.unpack('<I',old.read(a+offset,4))[0];y=struct.unpack('<I',new.read(b+offset,4))[0]
        if masked_word(x)==masked_word(y):continue
        if offset>=4 and (x&0xffc00000)==0x91000000 and (x&0xffc003ff)==(y&0xffc003ff):
            if string_pair(old,a+offset-4)==string_pair(new,b+offset-4):continue
        raise Unsupported(f'指令/字符串语义变化：0x{a+offset:x} → 0x{b+offset:x}')
