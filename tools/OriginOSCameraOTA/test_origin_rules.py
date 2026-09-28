import json,tempfile,unittest
from pathlib import Path
from elftools.elf.elffile import ELFFile
from native_rules import ASSETS,patch_server
from configuration_origin import config_patch

class ConfigTests(unittest.TestCase):
    def test_keeps_stock_and_uses_its_xpan_sizes(self):
        rows=[{'VendorTag':'com.oplus.feature.xpan.mode.support','Value':'1'},
              {'VendorTag':'com.oplus.xpan.main.picturesize','Value':'6000x2200'},
              {'VendorTag':'example.sensor.name','Value':'not-dodge'},
              {'VendorTag':'com.oplus.feature.super.text.support.v2','Value':'1'}]
        output,sizes,report=config_patch(json.dumps(rows).encode(),[(1920,864)])
        self.assertEqual(json.loads(output)[:len(rows)],rows)
        self.assertIn((6000,2200),sizes);self.assertNotIn((7872,2912),sizes)
        self.assertEqual(len(report['added']),1)
    def test_repeat_config_is_idempotent(self):
        rows=[{'VendorTag':'com.oplus.feature.super.text.support.v2','Value':'1'}]
        first,_,_=config_patch(json.dumps(rows).encode(),[])
        second,_,r=config_patch(first,[])
        self.assertEqual(first,second);self.assertEqual(r['added'],[])

CC=ASSETS.parent/'toolchain/bin'
@unittest.skipUnless((CC/'clang.exe').exists(),'编译器尚未准备')
class ServerTests(unittest.TestCase):
    def test_tag_literal_and_sizes_are_generated(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d);source=(ASSETS/'baseline/cameraserver').read_bytes()
            result,report=patch_server(source,0x8119002e,[(6000,2200),(2200,6000)],CC,out)
            with (out/'package_inject.elf').open('rb') as f:
                e=ELFFile(f);section=e.get_section_by_name('.text')
                symbols={s.name:s['st_value'] for s in e.get_section_by_name('.symtab').iter_symbols()}
                offset=symbols['package_tag']-section['sh_addr']
                self.assertEqual(section.data()[offset:offset+4],bytes.fromhex('2e001981'))
            self.assertEqual(report['package_tag'],'0x8119002e')
            self.assertEqual(report['xpan_sizes'],[(2200,6000),(6000,2200)])
            self.assertEqual(len(result),len(source))

if __name__=='__main__':unittest.main()
