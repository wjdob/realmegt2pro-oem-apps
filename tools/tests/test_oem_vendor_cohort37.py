"""Require the measured API37 vendor cohort without widening legacy profiles."""
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import oem_addons as addon
import oem_platform
import oem_runtime_policy as runtime


class VendorCohort37Tests(unittest.TestCase):
    def profile(self, name):
        path = addon.ROOT/'configs/oem-policy-native22-24.0.json'
        vendor = json.loads(addon.VENDOR_FILTER_CONTRACT37.read_text())
        profile = dict(id=name, platform=dict(oem_platform.ANDROID17),
            app_partition='system' if name == 'camera' else 'product',
            policy={'kind': runtime.KIND, 'base_context': {
                'source': 'configs/'+path.name, 'sha256': addon.sha(path.read_bytes())}},
            vendor_filter_contract={'source': 'configs/'+addon.VENDOR_FILTER_CONTRACT37.name,
                                    'sha256': addon.sha(addon.VENDOR_FILTER_CONTRACT37.read_bytes())},
            native_requirements={
                'system/lib64/libnativewindow.so': 'cbdce4b9b05b9a4bfbcbc050a39598f577a8f6b5cc437489278eb093ac840fc8',
                'odm/lib64/libAlgoInterface.so': vendor['aps']['sha256'],
                'odm/lib64/libmsnativefilter.so': vendor['provider']['sha256']})
        if name == 'photos':
            profile['peer_history'] = {'source': 'unused-by-this-validator'}
            profile['native_requirements'][runtime.EDITOR_BACKEND_PATH] = vendor['editor']['sha256']
        return profile

    def test_both_profiles_require_the_actual_api37_backend_versions(self):
        for name in ('camera', 'photos'):
            with self.subTest(name=name):
                profile = self.profile(name)
                self.assertIn(b'19518f07', addon.native_requirements(profile))

    def test_api36_vendor_contract_cannot_be_relabelled(self):
        profile = self.profile('camera')
        profile['vendor_filter_contract'] = {
            'source': 'configs/'+addon.VENDOR_FILTER_CONTRACT.name,
            'sha256': addon.sha(addon.VENDOR_FILTER_CONTRACT.read_bytes())}
        with self.assertRaisesRegex(ValueError, 'unreviewed vendor filter'):
            addon.native_requirements(profile)

    def test_old_aps_and_gallery_backends_are_refused(self):
        for name, path, digest in (
                ('camera', 'odm/lib64/libAlgoInterface.so', '96d9b0e608958221fec259d0a1959073efc7a7b7fd185de1eda64bc0f20c8d47'),
                ('photos', runtime.EDITOR_BACKEND_PATH, runtime.EDITOR_BACKEND_SHA)):
            profile = self.profile(name)
            profile['native_requirements'][path] = digest
            with self.subTest(name=name), self.assertRaises(ValueError):
                addon.native_requirements(profile)

    def test_missing_vendor_or_policy_context_is_refused(self):
        for field in ('vendor_filter_contract', 'base_context'):
            profile = self.profile('camera')
            if field == 'base_context': profile['policy'].pop(field)
            else: profile.pop(field)
            with self.subTest(field=field), self.assertRaises(ValueError):
                addon.native_requirements(profile)


if __name__ == '__main__':
    unittest.main()
