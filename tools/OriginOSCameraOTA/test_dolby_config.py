import tempfile,unittest,xml.etree.ElementTree as ET
from pathlib import Path
from sources import Sources,Inputs,Unsupported
from dolby_config import build_dolby,declarations,INCLUDE

DEC='<Decoders><MediaCodec name="c2.qti.dv.decoder" type="video/dolby-vision"><Limit name="size" max="6000x4000"/></MediaCodec></Decoders>'
ENC='<Encoders><MediaCodec name="c2.qti.dv.encoder" type="video/dolby-vision"><Limit name="bitrate" range="1-90000000"/></MediaCodec></Encoders>'
DEFINITIONS='<Included>'+DEC+ENC+'</Included>'

class DolbyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.out={}
        self.write('vendor/build.prop','ro.board.platform=testchip\n')
        self.write('vendor/etc/media_codecs.xml','<MediaCodecs><Decoders><MediaCodec name="c2.qti.hevc.decoder" type="video/hevc"/></Decoders></MediaCodecs>')
        self.write('odm/etc/dolby/display/dolby_vision.cfg','native-test-profile')
    def write(self,rel,text):
        path=self.root/rel;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text,encoding='utf8')
    def build(self):
        s=Sources(Inputs(stock_rom=str(self.root)),self.root/'cache',lambda _:None)
        return build_dolby(s,lambda rel,data,source:self.out.__setitem__(rel,data),lambda _:None)
    def test_odm_original_without_old_repair_xml(self):
        self.write('odm/etc/media_codecs_dolby_vision.xml',DEFINITIONS)
        report=self.build()
        self.assertFalse(report['old_repair_xml_required'])
        data=self.out['system/vendor/etc/'+INCLUDE]
        self.assertIn(b'6000x4000',data);self.assertIn(b'90000000',data)
        self.assertNotIn(b'8192x8192',data)
        self.assertIn(b'c2.qti.hevc.decoder',self.out['system/vendor/etc/media_codecs.xml'])
        self.assertEqual(self.out['system/odm/etc/dolby/display/dolby_vision.cfg'],b'native-test-profile')
    def test_existing_legacy_definition_still_supported(self):
        self.write('vendor/etc/'+INCLUDE,DEFINITIONS)
        self.build();self.assertIn('system/vendor/etc/'+INCLUDE,self.out)
    def test_preserve_inline_decoder_add_only_encoder(self):
        self.write('odm/etc/media_codecs_dolby_vision.xml',DEFINITIONS)
        self.write('vendor/etc/media_codecs.xml','<MediaCodecs>'+DEC+'</MediaCodecs>')
        report=self.build()
        self.assertEqual(report['added'][0]['codecs'],['c2.qti.dv.encoder'])
        node=ET.fromstring(self.out['system/'+report['added'][0]['include']])
        self.assertEqual(set(declarations(node)),{'c2.qti.dv.encoder'})
    def test_nested_include_already_has_dolby_no_duplicate(self):
        self.write('vendor/etc/media_codecs.xml','<MediaCodecs><Include href="media_codecs_native.xml"/></MediaCodecs>')
        self.write('vendor/etc/media_codecs_native.xml',DEFINITIONS)
        report=self.build()
        self.assertEqual(report['added'],[])
        self.assertNotIn('system/vendor/etc/media_codecs.xml',self.out)
    def test_broken_generated_include_is_regenerated(self):
        self.write('vendor/etc/media_codecs.xml',f'<MediaCodecs><Include href="{INCLUDE}"/></MediaCodecs>')
        self.write('odm/etc/media_codecs_dolby_vision.xml',DEFINITIONS)
        self.build()
        self.assertEqual(self.out['system/vendor/etc/media_codecs.xml'].count(INCLUDE.encode()),1)
        self.assertIn('system/vendor/etc/'+INCLUDE,self.out)
    def test_commented_codecs_are_not_enabled(self):
        self.write('vendor/etc/media_codecs_testchip_vendor.xml','<MediaCodecs><!--'+DEC+ENC+'--></MediaCodecs>')
        with self.assertRaisesRegex(Unsupported,'缺少有效 Dolby'):self.build()
    def test_partial_existing_file_not_overwritten_for_another_root(self):
        self.write('odm/etc/media_codecs_dolby_vision.xml',DEFINITIONS)
        self.write('vendor/etc/'+INCLUDE,'<Included>'+DEC+'</Included>')
        self.write('vendor/etc/media_codecs.xml',f'<MediaCodecs><Include href="{INCLUDE}"/></MediaCodecs>')
        self.write('vendor/etc/media_codecs_testchip.xml','<MediaCodecs></MediaCodecs>')
        report=self.build()
        self.assertNotIn('system/vendor/etc/'+INCLUDE,self.out)
        self.assertEqual(report['added'][0]['codecs'],['c2.qti.dv.encoder'])
    def test_stale_include_with_inline_decoder_is_replaced(self):
        self.write('odm/etc/media_codecs_dolby_vision.xml',DEFINITIONS)
        self.write('vendor/etc/media_codecs.xml',f'<MediaCodecs>{DEC}<Include href="{INCLUDE}"/></MediaCodecs>')
        report=self.build()
        self.assertEqual(report['added'][0]['codecs'],['c2.qti.dv.encoder'])
        self.assertNotIn(INCLUDE.encode(),self.out['system/vendor/etc/media_codecs.xml'])
    def test_native_platform_root_selected_not_sun(self):
        self.write('odm/etc/media_codecs_dolby_vision.xml',DEFINITIONS)
        self.write('vendor/etc/media_codecs_testchip.xml','<MediaCodecs>\n</MediaCodecs>')
        report=self.build()
        self.assertIn('vendor/etc/media_codecs_testchip.xml',report['roots'])
        self.assertFalse(any('sun' in root for root in report['roots']))
    def test_missing_display_profile_not_replaced_with_other_device(self):
        self.write('odm/etc/media_codecs_dolby_vision.xml',DEFINITIONS)
        # Remove only this temporary test's own file.
        (self.root/'odm/etc/dolby/display/dolby_vision.cfg').unlink()
        with self.assertRaisesRegex(Unsupported,'缺少本机 dolby_vision.cfg'):self.build()

if __name__=='__main__':unittest.main()
