"""Exercise system-owned Camera files using the real recovery transaction functions."""
import io
from pathlib import Path
import tempfile
import zipfile
import unittest

import oem_addons as addon
import test_oem_upgrade as upgrade


class SystemCameraPlacementTests(unittest.TestCase):
    setUp = upgrade.CameraUpgradeTests.setUp
    stage = upgrade.CameraUpgradeTests.stage
    run_shell = upgrade.CameraUpgradeTests.run_shell
    assert_contents = upgrade.CameraUpgradeTests.assert_contents
    def prepare_system(self, framework_sha=''):
        self.profile = dict(self.profile, app_partition='system',
                            shared_paths=['system/framework/camera-sdk.jar'],
                            platform_framework_sha256=framework_sha)
        self.profile.pop('upgrade_history', None)
        moved = {}
        for p, data in self.files.items():
            if p.startswith('product/app/RealmeStockCamera/'):
                p = p.replace('product/app/', 'system/app/', 1)
            if p == self.sdk:
                p = 'system/framework/camera-sdk.jar'
            moved[p] = data
        self.files = moved
        self.archive = addon.archive_bytes(self.profile, self.files, 'install', b'certificate')
        with zipfile.ZipFile(io.BytesIO(self.archive)) as z:
            self.prefix = z.read('META-INF/com/google/android/update-binary').decode().split('\nui "$TITLE:')[0]

    def test_system_install_readback_and_remove_preserve_unrelated_files(self):
        self.prepare_system()
        with tempfile.TemporaryDirectory(dir=self.scratch) as directory:
            root = Path(directory)
            self.stage(root, fresh=True)
            result = self.run_shell(root, '''
WRITABLE=1
apply_files
known_install_matches "$WORK/manifest.sha256"
''')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assert_contents(root, self.files)
            self.assertFalse((root/'product/app/RealmeStockCamera').exists())
            result = self.run_shell(root, 'ACTION=remove\nWRITABLE=1\napply_files\nprune\n')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((root/'system/app/RealmeStockCamera/base.apk').exists())
            self.assertFalse((root/'system/framework/camera-sdk.jar').exists())
            self.assert_contents(root, self.originals)

    def test_unknown_system_camera_file_is_rejected(self):
        self.prepare_system()
        with tempfile.TemporaryDirectory(dir=self.scratch) as directory:
            root = Path(directory)
            self.stage(root, fresh=True)
            result = self.run_shell(root, 'WRITABLE=1\napply_files\n')
            self.assertEqual(result.returncode, 0, result.stderr)
            (root/'system/app/RealmeStockCamera/unknown.so').write_bytes(b'unknown owner')
            result = self.run_shell(root, 'known_install_matches "$WORK/manifest.sha256"')
            self.assertNotEqual(result.returncode, 0)

    def test_framework_mismatch_aborts_before_any_app_write(self):
        self.prepare_system('0'*64)
        with tempfile.TemporaryDirectory(dir=self.scratch) as directory:
            root = Path(directory)
            self.stage(root, fresh=True)
            p = root/'system/framework/oplus-fwk.jar'
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b'older framework')
            result = self.run_shell(root, 'verify_platform_framework\nWRITABLE=1\napply_files\n')
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((root/'system/app/RealmeStockCamera/base.apk').exists())

    def test_unreviewed_partition_or_photos_system_placement_rejected(self):
        for changed in [dict(app_partition='vendor'), dict(app_partition='system', id='photos')]:
            with self.assertRaises(ValueError):
                addon.allowed(dict(self.profile, **changed), 'system/app/RealmeStockCamera/base.apk')

    def test_platform_native_ownership_hash_and_label(self):
        self.prepare_system()
        data = bytearray(64)
        data[:6] = b'\x7fELF\x02\x01'
        data[18:20] = (183).to_bytes(2, 'little')
        path = 'system/lib64/libHeifEncoderWrapper.so'
        spec = dict(path=path, sha256=addon.sha(data))
        p = dict(self.profile, shared_paths=[path])
        p.pop('policy')
        apk = io.BytesIO()
        with zipfile.ZipFile(apk, 'w'):
            pass
        files = addon.payload(p, apk.getvalue(), platform_native=[(spec, data)])
        self.assertEqual(files[path], data)
        for changed in [dict(spec, sha256='0'*64),
                        dict(spec, path='vendor/lib64/libHeifEncoderWrapper.so')]:
            with self.assertRaises(ValueError):
                addon.payload(p, apk.getvalue(), platform_native=[(changed, data)])
        with tempfile.TemporaryDirectory(dir=self.scratch) as directory:
            root = Path(directory)
            self.stage(root, fresh=True)
            result = self.run_shell(root, 'file_context system/lib64/libHeifEncoderWrapper.so')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), 'u:object_r:system_lib_file:s0')

    def test_staged_platform_library_uses_library_label(self):
        self.prepare_system()
        rel = 'system/lib64/libHeifEncoderWrapper.so'
        self.files[rel] = b'reviewed platform-library fixture'
        self.profile['shared_paths'].append(rel)
        self.archive = addon.archive_bytes(self.profile, self.files, 'install', b'certificate')
        with zipfile.ZipFile(io.BytesIO(self.archive)) as z:
            self.prefix = z.read('META-INF/com/google/android/update-binary').decode().split('\nui "$TITLE:')[0]
        with tempfile.TemporaryDirectory(dir=self.scratch) as directory:
            root = Path(directory)
            self.stage(root, fresh=True)
            result = self.run_shell(root, '''
chcon() { printf '%s %s\\n' "$1" "${2#$WORK/}" >> "$WORK/applied-labels"; }
WRITABLE=1
apply_files
''')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((root/rel).read_bytes(), self.files[rel])
            labels = (root/'applied-labels').read_text().splitlines()
            self.assertIn('u:object_r:system_lib_file:s0 ' + rel + '.realme-new', labels)
            self.assertIn('u:object_r:system_file:s0 system/app/RealmeStockCamera/base.apk.realme-new', labels)
