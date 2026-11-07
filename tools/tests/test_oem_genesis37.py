"""Qualify the actual API37 genesis metadata without loading OEM applications."""
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'tools'))
import oem_addons as addons
import oem_platform as platform
import oem_runtime_policy as policy


class Genesis37Tests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT/'configs/oem-addons-alpha1-24.0.json').read_text())

    def profile(self, name):
        profile = addons.checked_profile(self.config, name)
        sdk = self.config['shared_sdk']
        profile['shared_paths'] = [sdk['jar_path'], sdk['xml_path']]
        profile['shared_paths'] += [s['path'] for s in profile.get('platform_native', ())]
        profile['shared_paths'] += sorted(policy.shared_paths())
        return profile

    def test_native_helpers_and_policy_match_the_reviewed_native22_cohort(self):
        camera = self.profile('camera')
        buffer = json.loads((ROOT/'configs/oem-camera-native37-review.json').read_text())
        bridge = json.loads((ROOT/'configs/oem-camera-bridge37-review.json').read_text())
        helpers = {s['path'].split('/')[-1]: s['sha256'] for s in camera['platform_native']}
        self.assertEqual(helpers['libNativeWinBuffExchange.so'], buffer['output_sha256'])
        self.assertEqual(helpers['libHeifWinBufExchg-jni.so'], bridge['output_sha256'])
        self.assertEqual(helpers['libHeifEncoderWrapper.so'], bridge['helpers']['libHeifEncoderWrapper.so'])
        context = policy.context_for_profile(camera)
        self.assertEqual(context.config['target_files_sha256'], buffer['target_files_sha256'])
        for name in ('camera', 'photos'):
            profile = self.profile(name)
            self.assertEqual(platform.checked(profile), platform.ANDROID17)
            self.assertEqual(profile['policy'], camera['policy'])
            self.assertTrue(addons.native_requirements(profile))
            self.assertNotIn('upgrade_history', profile)

    def test_shared_app_payloads_preserve_the_accepted_apks_and_signers(self):
        for name, file in [('camera', 'oem-addons-camera15-gallery15-23.2.json'),
                           ('photos', 'oem-addons-gallery-alpha15-23.2.json')]:
            old = json.loads((ROOT/'configs'/file).read_text())
            self.assertEqual(self.config['shared_sdk'], old['shared_sdk'])
            for key in ('package', 'version', 'sha256', 'certificate_sha256'):
                self.assertEqual(self.profile(name)[key], old['profiles'][name][key])
        photos = self.profile('photos')
        compat = addons.checked_app_compatibility(photos)
        self.assertEqual(compat['predecessors'], {'alpha1-api37': compat['prepared_app']})
        self.assertNotIn('legacy_gallery_apk_sha256', photos)

    def test_exact_genesis_peers_coexist_and_gallery_runtime_is_complete(self):
        history = json.loads((ROOT/'configs/oem-runtime-peers-alpha1-24.0.json').read_text())
        own = {name: next(iter(history['profiles'][other].values()))['files']
               for name, other in [('camera', 'photos'), ('photos', 'camera')]}
        for name in ('camera', 'photos'):
            profile = self.profile(name)
            peers = addons.load_peer_history(profile)
            self.assertEqual(len(peers), 1)
            result = addons.oem_coinstall.validate(name, own[name], peers)
            self.assertEqual(len(result), 1)
        addons.validate_gallery_runtime(self.profile('photos'), own['photos'])
        changed = copy.deepcopy(own['photos'])
        del changed['product/framework/realme-camera-unit-sdk.jar']
        with self.assertRaises(ValueError):
            addons.validate_gallery_runtime(self.profile('photos'), changed)

    def test_generation_and_old_policy_context_cannot_be_mixed(self):
        changed = copy.deepcopy(self.config)
        changed['sdk'] = 36
        with self.assertRaises(ValueError):
            addons.checked_profile(changed, 'camera')
        for name in ('camera', 'photos'):
            changed = self.profile(name)
            changed['platform'] = dict(platform.LEGACY)
            with self.assertRaisesRegex(ValueError, 'API36 policy context'):
                policy.context_for_profile(changed)


if __name__ == '__main__':
    unittest.main()
