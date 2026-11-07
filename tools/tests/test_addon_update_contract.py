import io
from pathlib import Path
import sys
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from addon_update_contract import inspect


class AddonContractTests(unittest.TestCase):
    def fixture(self, *, trust=True, hook=True, build_type='userdebug'):
        certificate = (Path(__file__).resolve().parents[2] /
                       'certs/rmx3301-releasekey.x509.pem').read_bytes()
        trusted = io.BytesIO()
        with zipfile.ZipFile(trusted, 'w') as z:
            if trust:
                z.writestr('releasekey.x509.pem', certificate)
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as z:
            z.writestr('SYSTEM/build.prop', f'ro.build.type={build_type}\nro.debuggable=0\n')
            z.writestr('SYSTEM/etc/security/otacerts.zip', trusted.getvalue())
            z.writestr('SYSTEM/bin/update_engine',
                       b'Running backuptool scripts\0/postinstall/system/bin/backuptool_postinstall.sh\0Skipping backuptool scripts'
                       if hook else b'no compiled hook')
            z.writestr('SYSTEM/bin/backuptool_postinstall.sh', b'wrapper')
            z.writestr('SYSTEM/bin/backuptool_ab.sh', b'export ADDOND_VERSION=3')
            z.writestr('SYSTEM/bin/backuptool_ab.functions', b'functions')
        output.seek(0)
        return output, certificate

    def test_packaged_debuggable_zero_does_not_erase_compiled_hook(self):
        archive, certificate = self.fixture()
        result = inspect(archive, certificate)
        self.assertTrue(result['addon_update_prerequisites_pass'])
        self.assertEqual(result['packaged_ro_debuggable'], '0')

    def test_missing_release_trust_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'certificate missing'):
            inspect(*self.fixture(trust=False))

    def test_helper_presence_without_compiled_hook_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'marker missing'):
            inspect(*self.fixture(hook=False))

    def test_unreviewed_user_build_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'development build'):
            inspect(*self.fixture(build_type='user'))
