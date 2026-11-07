import hashlib
import io
import json
from pathlib import Path
import stat
import sys
import unittest
import warnings
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import oem_ota_compat as gate


def sha(data):
    return hashlib.sha256(data).hexdigest()


def archive(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as z:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            for path, data in entries:
                z.writestr(path, data)
    output.seek(0)
    return output


class OemOtaCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.certificate = (Path(__file__).resolve().parents[2] /
                            'certs/rmx3301-releasekey.x509.pem').read_bytes()
        self.base = {p: ('base:' + p).encode() for p in gate.COMPILER_INPUTS | gate.POLICY}
        self.base.update({'odm/lib64/libAlgoProcess.so': b'gallery-backend',
                          'vendor/lib64/libcamera_dep.so': b'camera-backend'})

    def target(self, overrides=None, extra=(), sdk=36):
        files = dict(self.base)
        files.update(overrides or {})
        entries = [(p.split('/', 1)[0].upper() + '/' + p.split('/', 1)[1], data)
                   for p, data in files.items() if data is not None]
        trust = archive([('releasekey.x509.pem', self.certificate)]).getvalue()
        entries.extend([
            ('SYSTEM/build.prop', f'ro.build.type=userdebug\nro.debuggable=0\nro.build.version.sdk={sdk}\n'.encode()),
            ('SYSTEM/etc/security/otacerts.zip', trust),
            ('SYSTEM/bin/update_engine', b'Running backuptool scripts\0/postinstall/system/bin/backuptool_postinstall.sh\0Skipping backuptool scripts'),
            ('SYSTEM/bin/backuptool_postinstall.sh', b'wrapper'),
            ('SYSTEM/bin/backuptool_ab.sh', b'export ADDOND_VERSION=3'),
            ('SYSTEM/bin/backuptool_ab.functions', b'functions'),
        ])
        return archive(entries + list(extra))

    def source(self, profile, *, shared=b'sdk', omit_input=None, corrupt_payload=False):
        apk = ('apk:' + profile).encode()
        payload = {f'product/app/{profile}/base.apk': apk,
                   'product/framework/oem-shared.jar': shared}
        package, signer = gate.PACKAGES[profile]
        native = {'camera': 'vendor/lib64/libcamera_dep.so',
                  'photos': 'odm/lib64/libAlgoProcess.so'}[profile]
        receipt = {'id': profile, 'package': package, 'certificate_sha256': signer,
                   'sha256': sha(apk), 'native_requirements': {native: sha(self.base[native])}}
        payload[f'product/etc/realme-oem-addons/{profile}.json'] = json.dumps(receipt).encode()
        inputs = ''.join(sha(self.base[p]) + '  ' + p + '\n'
                         for p in sorted(gate.COMPILER_INPUTS) if p != omit_input).encode()
        payload[gate.STORAGE + 'inputs.sha256'] = inputs
        support = {'policy/inputs.sha256': inputs,
                   'native-requirements.sha256': (sha(self.base[native]) + '  ' + native + '\n').encode()}
        rows = []
        for p in sorted(gate.POLICY):
            before = self.base[p]
            after = b'oem:' + before
            name = Path(p).name
            payload[p] = after
            for side, data in [('preimages', before), ('postimages', after)]:
                payload[gate.STORAGE + side + '/' + name] = data
                support['policy/' + side + '/' + name] = data
            rows.append(sha(after) + ' ' + sha(before) + ' ' + p + '\n')
        patches = ''.join(rows).encode()
        payload[gate.STORAGE + 'patches.tsv'] = patches
        support['policy/patches.tsv'] = patches
        manifest = ''.join(sha(v) + '  ' + p + '\n' for p, v in sorted(payload.items())).encode()
        if corrupt_payload:
            payload['product/framework/oem-shared.jar'] = b'tampered'
        return archive([('manifest.sha256', manifest)] + list(support.items()) +
                       [('payload/' + p, data) for p, data in payload.items()])

    def selected(self, profile, **options):
        with zipfile.ZipFile(self.source(profile, **options)) as z:
            return gate.bundle(z, profile)

    def check(self, selected, **options):
        with zipfile.ZipFile(self.target(**options)) as z:
            return gate.inspect(z, selected, self.certificate)

    def test_plain_profile_does_not_add_oem_files(self):
        result = self.check([])
        self.assertTrue(result['oem_target_prerequisites_pass'])
        self.assertEqual(result['owned_files'], 0)
        self.assertEqual(result['profiles'], [])

    def test_camera_and_gallery_independently_validate(self):
        for profile in gate.PACKAGES:
            with self.subTest(profile=profile):
                result = self.check([self.selected(profile)])
                self.assertEqual(result['profiles'][0]['profile'], profile)
                self.assertTrue(result['target_bindings'])

    def test_combined_selection_coalesces_identical_shared_paths(self):
        result = self.check([self.selected(p) for p in gate.PACKAGES])
        self.assertTrue(result['shared_paths'])
        self.assertTrue(all(set(v) == {'camera', 'photos'} for v in result['shared_paths'].values()))

    def test_conflicting_shared_sdk_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'conflicting shared payload'):
            self.check([self.selected('camera'), self.selected('photos', shared=b'other-sdk')])

    def test_changed_platform_policy_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'target dependency differs'):
            self.check([self.selected('camera')], overrides={'system/etc/selinux/plat_sepolicy.cil': b'new-policy'})

    def test_changed_odm_policy_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'target dependency differs'):
            self.check([self.selected('photos')], overrides={'odm/etc/selinux/odm_sepolicy.cil': b'new-policy'})

    def test_missing_native_backend_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'missing target dependency'):
            self.check([self.selected('photos')], overrides={'odm/lib64/libAlgoProcess.so': None})

    def test_unknown_destination_apk_collision_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'collides with base'):
            self.check([self.selected('camera')], extra=[('PRODUCT/app/camera/base.apk', b'other')])

    def test_duplicate_selected_profile_is_refused(self):
        camera = self.selected('camera')
        with self.assertRaisesRegex(ValueError, 'duplicate selected'):
            self.check([camera, camera])

    def test_incomplete_compiler_context_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'compiler inputs'):
            self.selected('camera', omit_input='odm/etc/selinux/odm_sepolicy.cil')

    def test_source_payload_tampering_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'payload hash differs'):
            self.selected('photos', corrupt_payload=True)

    def test_duplicate_archive_members_are_refused(self):
        with self.assertRaisesRegex(ValueError, 'duplicate ZIP'):
            self.check([], extra=[('SYSTEM/build.prop', b'ro.build.version.sdk=36')])

    def test_ordinary_uninspected_target_symlinks_are_allowed(self):
        link = zipfile.ZipInfo('SYSTEM/bin/example-link')
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        self.assertTrue(self.check([], extra=[(link, b'toybox')])['oem_target_prerequisites_pass'])

    def test_inspected_dependency_symlink_is_refused(self):
        link = zipfile.ZipInfo('ODM/lib64/libAlgoProcess.so')
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        with self.assertRaisesRegex(ValueError, 'dependency is a symlink'):
            self.check([self.selected('photos')], overrides={'odm/lib64/libAlgoProcess.so': None}, extra=[(link, b'other')])

    def test_wrong_android_generation_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'Android16'):
            self.check([], sdk=37)


if __name__ == '__main__':
    unittest.main()
