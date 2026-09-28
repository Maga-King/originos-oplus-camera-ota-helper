import unittest,sys,struct
from pathlib import Path
from media_patch import Elf,Unsupported,locate_writer,locate_configure,segment_slot,CONFIGURE

FIXTURE=Path(__file__).resolve().parents[2]/'analysis/originos17_oplus_camera_20260926/target/libstagefright.so'

@unittest.skipUnless(FIXTURE.exists(),'开发端原始媒体库不在发布素材中')
class NativeTests(unittest.TestCase):
    def setUp(self):self.data=FIXTURE.read_bytes();self.elf=Elf(self.data)
    def test_current_writer(self):self.assertEqual(locate_writer(self.elf)[0],0x17a13c)
    def test_current_configure(self):self.assertEqual(locate_configure(self.elf),(0x1d9144,0x170))
    def test_changed_stack(self):
        b=bytearray(self.data);pos=self.elf.offset(0x1d9144,4)
        word=struct.unpack_from('<I',b,pos)[0]
        struct.pack_into('<I',b,pos,(word&~(0xfff<<10))|(0x180<<10))
        self.assertEqual(locate_configure(Elf(bytes(b)))[1],0x180)
    def test_wrong_argument_layout_rejected(self):
        b=bytearray(self.data);pos=self.elf.offset(0x17a12c,4)
        b[pos:pos+4]=bytes.fromhex('1f2003d5')
        with self.assertRaises(Unsupported):locate_writer(Elf(bytes(b)))
    def test_already_patched_entry_rejected(self):
        b=bytearray(self.data);pos=self.elf.offset(0x1d9144,4)
        b[pos:pos+4]=bytes.fromhex('00000014')
        with self.assertRaises(Unsupported):locate_configure(Elf(bytes(b)))
    def test_duplicate_note_only(self):
        i=segment_slot(self.elf);s=list(self.elf.elf.iter_segments())[i]
        self.assertEqual(s['p_type'],'PT_NOTE')
        self.assertTrue(any(p['p_type']=='PT_GNU_PROPERTY' and p['p_offset']==s['p_offset'] for p in self.elf.elf.iter_segments()))

if __name__=='__main__':unittest.main()
