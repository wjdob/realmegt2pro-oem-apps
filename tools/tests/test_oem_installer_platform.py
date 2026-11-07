"""Generation isolation at real archive generation/inspection boundaries."""
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import oem_addons as addon
import oem_platform


class InstallerPlatformTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads(addon.CONFIG.read_text())
        self.profile = dict(self.config['profiles']['camera'], id='camera')
        self.profile.pop('policy', None)
        apk = io.BytesIO()
        with zipfile.ZipFile(apk, 'w'):
            pass
        self.apk = apk.getvalue()

    def archive(self, profile=None, action='install'):
        profile = profile or self.profile
        files = addon.payload(profile, self.apk)
        return addon.archive_bytes(profile, files, action, b'certificate')

    def inspect(self, data, profile):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'test.zip'
            path.write_bytes(data)
            return addon.inspect_archive(path, profile)

    def rewrite(self, data, member, transform):
        output = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(output, 'w') as target:
            for name in source.namelist():
                value = source.read(name)
                target.writestr(name, transform(value) if name == member else value)
        return output.getvalue()

    def test_legacy_profile_preserves_template_and_receipt_shape(self):
        self.assertEqual(addon.installer_template(self.profile), addon.TEMPLATE.read_bytes())
        with zipfile.ZipFile(io.BytesIO(self.archive())) as z:
            receipt = json.loads(z.read('payload/product/etc/realme-oem-addons/camera.json'))
            self.assertNotIn('platform', receipt)
        self.inspect(self.archive(), self.profile)

    def test_platform37_install_check_remove_guards_are_distinct(self):
        profile = dict(self.profile, platform=oem_platform.ANDROID17)
        for action in ('install', 'check', 'remove'):
            with self.subTest(action=action):
                archive = self.archive(profile, action)
                with zipfile.ZipFile(io.BytesIO(archive)) as z:
                    script = z.read('META-INF/com/google/android/update-binary')
                    self.assertIn(b'^ro.build.version.sdk=37$', script)
                    self.assertIn(br'^ro.lineage.version=24\.0-', script)
                    self.assertNotIn(b'^ro.build.version.sdk=36$', script)
                self.inspect(archive, profile)

    def test_platform37_payload_receipt_is_explicit(self):
        profile = dict(self.profile, platform=oem_platform.ANDROID17)
        with zipfile.ZipFile(io.BytesIO(self.archive(profile))) as z:
            receipt = json.loads(z.read('payload/product/etc/realme-oem-addons/camera.json'))
        self.assertEqual(receipt['platform'], oem_platform.ANDROID17)

    def test_inspector_rejects_opposite_generation_archive(self):
        profile = dict(self.profile, platform=oem_platform.ANDROID17)
        with self.assertRaisesRegex(ValueError, 'platform guards'):
            self.inspect(self.archive(), profile)
        with self.assertRaisesRegex(ValueError, 'platform guards'):
            self.inspect(self.archive(profile), self.profile)

    def test_inspector_rejects_duplicate_or_partial_sdk_guard(self):
        member = 'META-INF/com/google/android/update-binary'
        archive = self.archive()
        for transform in (lambda b: b.replace(b'^ro.build.version.sdk=36$', b'^ro.build.version.sdk=360$'),
                          lambda b: b+b'\n# ^ro.build.version.sdk=36$\n'):
            with self.subTest(transform=transform), self.assertRaisesRegex(ValueError, 'platform guards'):
                self.inspect(self.rewrite(archive, member, transform), self.profile)

    def test_inspector_rejects_receipt_from_other_generation(self):
        archive = self.archive()
        path = 'payload/product/etc/realme-oem-addons/camera.json'
        def changed(data):
            receipt = json.loads(data)
            receipt['platform'] = oem_platform.ANDROID17
            return json.dumps(receipt).encode()
        with self.assertRaisesRegex(ValueError, 'receipt platform'):
            self.inspect(self.rewrite(archive, path, changed), self.profile)

    def test_profile_inherits_only_explicit_supported_generation(self):
        config = dict(self.config, platform=oem_platform.ANDROID17, sdk=37, lineage='24.0')
        self.assertEqual(addon.checked_profile(config, 'camera')['platform'], oem_platform.ANDROID17)
        self.assertNotIn('platform', addon.checked_profile(self.config, 'camera'))

    def test_profile_cannot_override_config_generation(self):
        config = copy.deepcopy(self.config)
        config['profiles']['camera']['platform'] = oem_platform.ANDROID17
        with self.assertRaisesRegex(ValueError, 'profile/config'):
            addon.checked_profile(config, 'camera')
        config['platform'] = oem_platform.ANDROID17
        config.update(sdk=37, lineage='24.0')
        config['profiles']['camera']['platform'] = oem_platform.LEGACY
        with self.assertRaisesRegex(ValueError, 'profile/config'):
            addon.checked_profile(config, 'camera')

    def test_unknown_platform_is_rejected_before_archive_output(self):
        for platform in ({'sdk': 37, 'lineage': '23.2'}, {'sdk': 38, 'lineage': '24.0'}):
            with self.subTest(platform=platform), self.assertRaises(ValueError):
                self.archive(dict(self.profile, platform=platform))

    def test_config_fields_cannot_silently_default_to_legacy(self):
        for changed in (dict(self.config, sdk=37, lineage='24.0'),
                        dict(self.config, platform=oem_platform.ANDROID17)):
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, 'fields disagree'):
                addon.checked_profile(changed, 'camera')

    def test_histories_and_runtime_contracts_cannot_cross_android_major(self):
        profile = dict(self.profile, platform=oem_platform.ANDROID17)
        for contract in ({}, {'platform': oem_platform.LEGACY}):
            with self.subTest(contract=contract), self.assertRaisesRegex(ValueError, 'contract platform'):
                addon.check_contract_platform(profile, contract)
        addon.check_contract_platform(profile, {'platform': oem_platform.ANDROID17})
        addon.check_contract_platform(self.profile, {})

    def test_upgrade_history_rejects_api36_before_file_processing(self):
        profile = dict(self.profile, platform=oem_platform.ANDROID17)
        with self.assertRaisesRegex(ValueError, 'contract platform'):
            addon.checked_upgrade_history(profile, {}, {})


if __name__ == '__main__':
    unittest.main()
