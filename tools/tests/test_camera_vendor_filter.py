"""Reject incomplete or mismatched vendor-filter dependency reconstruction."""
import copy
import hashlib
import tempfile
from pathlib import Path
import sys
import unittest
import zipfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import camera_vendor_filter as f


class VendorFilterDependencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.provider = b'\x7fELF\x02\x01' + bytes(30)
        self.aps = b'original\0/odm/lib64/libmsnativefilter.so\0'
        self.contract = dict(provider=dict(bytes=len(self.provider), sha256=f.sha(self.provider), source='.local/build-inputs/filter/provider.so', target_files='ODM/lib64/libmsnativefilter.so'), aps=dict(sha256=f.sha(self.aps), provider_literal='/odm/lib64/libmsnativefilter.so', target_files='ODM/lib64/libAlgoInterface.so'))

    def archive(self, provider=True, aps=None, provider_data=None):
        p = self.root / 'target.zip'
        with zipfile.ZipFile(p, 'w') as z:
            if provider:
                z.writestr(self.contract['provider']['target_files'], self.provider if provider_data is None else provider_data)
            z.writestr(self.contract['aps']['target_files'], self.aps if aps is None else aps)
        return p

    def test_missing_deferred_dependency_rejected(self):
        with self.assertRaisesRegex(ValueError, 'missing or ambiguous'):
            f.check_package(self.archive(provider=False), self.contract)

    def test_present_provider_and_original_route_accepted(self):
        self.assertTrue(f.check_package(self.archive(), self.contract)['pass_all'])

    def test_changed_provider_rejected(self):
        with self.assertRaisesRegex(ValueError, 'provider differs'):
            f.check_package(self.archive(provider_data=self.provider+b'changed'), self.contract)

    def test_app_namespace_workaround_not_accepted_as_vendor_route(self):
        with self.assertRaisesRegex(ValueError, 'APS filter route differs'):
            f.check_package(self.archive(aps=b'libmsnativefilter.so\0'), self.contract)

    def test_prepare_preserves_existing_unknown_input(self):
        source=self.root/'stock.so';source.write_bytes(self.provider)
        dest=self.root/self.contract['provider']['source'];dest.parent.mkdir(parents=True);dest.write_bytes(b'unknown')
        with self.assertRaisesRegex(ValueError, 'preserve it'):
            f.prepare(source,self.root,self.contract)
        self.assertEqual(dest.read_bytes(),b'unknown')

    def test_preparation_rejects_private_path_escape(self):
        source=self.root/'stock.so';source.write_bytes(self.provider)
        contract=copy.deepcopy(self.contract);contract['provider']['source']='outside.so'
        with self.assertRaisesRegex(ValueError, 'remain private'):
            f.prepare(source,self.root,contract)
        self.assertFalse((self.root/'outside.so').exists())

    def test_preparation_rejects_destination_symlink(self):
        source=self.root/'stock.so';source.write_bytes(self.provider)
        dest=self.root/self.contract['provider']['source'];dest.parent.mkdir(parents=True);dest.symlink_to(source)
        with self.assertRaisesRegex(ValueError, 'remain private|symlink'):
            f.prepare(source,self.root,self.contract)


if __name__ == '__main__':
    unittest.main()
