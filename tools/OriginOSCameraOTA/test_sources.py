import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from sources import Sources,Inputs,Unsupported,DeviceReadError

class SourceTests(unittest.TestCase):
    def test_offline_never_touches_adb(self):
        with tempfile.TemporaryDirectory() as d,patch('sources.list_devices') as devices:
            s=Sources(Inputs(),Path(d))
            self.assertFalse(s.camera_evidence()['available']);devices.assert_not_called()
    def test_two_devices_need_choice(self):
        with tempfile.TemporaryDirectory() as d,patch('sources.list_devices',return_value=[
            {'serial':'one','state':'device'},{'serial':'two','state':'device'}]):
            with self.assertRaises(Unsupported):Sources(Inputs(adb_enabled=True),Path(d))
            self.assertEqual(Sources(Inputs(adb_enabled=True,adb_serial='two'),Path(d)).serial,'two')
    def test_no_stock_system_from_originos(self):
        with tempfile.TemporaryDirectory() as d,patch('sources.list_devices',return_value=[{'serial':'one','state':'device'}]),patch('sources.run_adb') as adb:
            s=Sources(Inputs(adb_enabled=True),Path(d))
            self.assertIsNone(s.resolve('stock','system/lib64/libexample.so',required=False))
            adb.assert_not_called()
    def test_local_first(self):
        with tempfile.TemporaryDirectory() as d,patch('sources.run_adb') as adb:
            root=Path(d);p=root/'system/lib64/libtest.so';p.parent.mkdir(parents=True);p.write_bytes(b'fixture')
            s=Sources(Inputs(target_rom=d),root/'cache')
            self.assertEqual(s.resolve('target','system/lib64/libtest.so'),p)
            adb.assert_not_called()
    def device_source(self,root):
        return Sources(Inputs(adb_enabled=True),root,lambda _:None)
    @patch('sources.list_devices',return_value=[{'serial':'one','state':'device'}])
    def test_optional_missing_is_not_permission_error(self,_):
        with tempfile.TemporaryDirectory() as d,patch('sources.run_adb',return_value=b'cat: /vendor/x.xml: No such file or directory\n'):
            s=self.device_source(Path(d))
            self.assertIsNone(s.resolve('stock','vendor/x.xml',required=False))
            self.assertEqual(s.records[-1]['reason'],'missing')
    @patch('sources.list_devices',return_value=[{'serial':'one','state':'device'}])
    def test_permission_failure_does_not_fall_back(self,_):
        with tempfile.TemporaryDirectory() as d,patch('sources.run_adb',return_value=b'cat: /vendor/x.xml: Permission denied\n'):
            with self.assertRaisesRegex(DeviceReadError,'设备拒绝读取'):
                self.device_source(Path(d)).resolve('stock','vendor/x.xml',required=False)
    @patch('sources.list_devices',return_value=[{'serial':'one','state':'device'}])
    def test_find_optional_missing_directory(self,_):
        for result in (b'find: /vendor/persist/display: No such file or directory\n',
                       Unsupported('find: /vendor/persist/display: No such file or directory')):
            with tempfile.TemporaryDirectory() as d,patch('sources.run_adb') as adb:
                if isinstance(result,Exception):adb.side_effect=result
                else:adb.return_value=result
                self.assertEqual(self.device_source(Path(d)).named_files('stock','vendor/persist/display','dolby_vision.cfg'),[])
    @patch('sources.list_devices',return_value=[{'serial':'one','state':'device'}])
    def test_find_permission_is_not_silently_empty(self,_):
        with tempfile.TemporaryDirectory() as d,patch('sources.run_adb',return_value=b'find: /odm/etc: Permission denied\n'):
            with self.assertRaisesRegex(DeviceReadError,'设备拒绝读取'):
                self.device_source(Path(d)).named_files('stock','odm/etc','dolby_vision.cfg')
    @patch('sources.list_devices',return_value=[{'serial':'one','state':'device'}])
    def test_disconnection_is_not_a_missing_file(self,_):
        with tempfile.TemporaryDirectory() as d,patch('sources.run_adb',side_effect=Unsupported('error: device offline')):
            with self.assertRaisesRegex(DeviceReadError,'设备读取失败'):
                self.device_source(Path(d)).resolve('stock','vendor/x.xml',required=False)

if __name__=='__main__':unittest.main()
