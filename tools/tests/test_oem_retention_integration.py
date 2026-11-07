"""Packager/inspector integration with complete synthetic runtime inputs.

Actual payload, archive, runtime-contract and retention code runs. APK signature,
native compatibility, mounts and Android updates remain separate live/source gates.
"""
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import oem_addons as addon
import oem_coinstall as co
import oem_ota_compat as compat
import oem_retention as retention
import oem_runtime_policy as policy


class RetentionPackagingTests(unittest.TestCase):
    def setUp(self):
        parent = addon.ROOT / 'tmp/oem-coinstall-transactions'
        parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'configs').mkdir()
        inputs = {p: ('base:' + p).encode() for p in compat.COMPILER_INPUTS | compat.POLICY}
        self.support = {}
        for rel in compat.POLICY:
            name = Path(rel).name
            self.support[compat.STORAGE + 'preimages/' + name] = inputs[rel]
            self.support[compat.STORAGE + 'postimages/' + name] = b'patched:' + inputs[rel]
        self.support[compat.STORAGE + 'inputs.sha256'] = ''.join(
            addon.sha(inputs[p]) + '  ' + p + '\n' for p in sorted(compat.COMPILER_INPUTS)).encode()
        self.support[compat.STORAGE + 'patches.tsv'] = ''.join(
            addon.sha(b'patched:' + inputs[p]) + ' ' + addon.sha(inputs[p]) + ' ' + p + '\n'
            for p in sorted(compat.POLICY)).encode()
        policy_files = dict(self.support)
        policy_files.update({p: b'patched:' + inputs[p] for p in compat.POLICY})
        actual_assets = json.loads(addon.FILTER_ASSET_CONTRACT.read_text())
        assets = {p: ('asset:' + p).encode() for p in co.lut_paths()}
        asset_contract = dict(actual_assets, files={Path(p).name: {'sha256': addon.sha(data)} for p, data in assets.items()})
        self.asset_path = self.root / 'assets.json'
        self.asset_path.write_text(json.dumps(asset_contract))
        for target, name, value in [(addon, 'ROOT', self.root), (addon, 'FILTER_ASSET_CONTRACT', self.asset_path)]:
            mock = patch.object(target, name, value)
            mock.start()
            self.addCleanup(mock.stop)
        apk = io.BytesIO()
        with zipfile.ZipFile(apk, 'w'):
            pass
        self.apk = apk.getvalue()
        elf = bytearray(64)
        elf[:6] = b'\x7fELF\x02\x01'
        elf[18:20] = (183).to_bytes(2, 'little')
        elf = bytes(elf)
        self.native = [(dict(name='libfixture.so', source='unused', sha256=addon.sha(elf)), elf)]
        sdk = b'synthetic shared SDK'
        self.sdk = (dict(compatible_profiles=['camera', 'photos'], library='com.oplus.camera.unit.sdk',
                         jar_path='product/framework/realme-camera-unit-sdk.jar',
                         xml_path='product/etc/permissions/realme-camera-unit-sdk.xml', sha256=addon.sha(sdk)), sdk)
        self.asset_inputs = [(dict(path=p, source='unused', sha256=addon.sha(data)), data) for p, data in assets.items()]
        self.policy = ({'kind': policy.KIND}, policy_files)
        self.profiles = {}
        self.files = {}
        for name in ('camera', 'photos'):
            package, cert = compat.PACKAGES[name]
            profile = dict(id=name, title=name, package=package, certificate_sha256=cert,
                           version='fixture', source='unused.apk', sha256=addon.sha(self.apk),
                           directory='RealmeStockCamera' if name == 'camera' else 'RealmeStockPhotos',
                           other_directory='RealmeStockPhotos' if name == 'camera' else 'RealmeStockCamera',
                           hook=Path(co.HOOKS[name]).name, addon_release='retention-fixture',
                           app_partition='system' if name == 'camera' else 'product',
                           app_class='priv-app' if name == 'camera' else 'app',
                           peer_history={'source': 'configs/peers.json', 'sha256': 'pending'},
                           policy={'kind': policy.KIND, 'sha256': '1' * 64},
                           platform_framework_sha256=addon.sha(b'framework fixture'),
                           native_requirements={p: '2' * 64 for p in addon.NATIVE_REQUIREMENT_PATHS},
                           extra_assets=[s for s, _ in self.asset_inputs],
                           extra_native=[s for s, _ in self.native],
                           shared_paths=sorted(co.SDK_PATHS | policy.shared_paths()),
                           ota_retention={'kind': retention.KIND, 'template_sha256': addon.sha(retention.TEMPLATE.read_bytes())})
            if name == 'camera':
                profile['privileged_permissions'] = {'grant': sorted(addon.CAMERA_PRIVILEGED_GRANTS),
                                                    'deny': sorted(addon.CAMERA_PRIVILEGED_REQUESTS - addon.CAMERA_PRIVILEGED_GRANTS)}
            else:
                profile['native_requirements'][policy.EDITOR_BACKEND_PATH] = policy.EDITOR_BACKEND_SHA
                contract = dict(schema=1, app={k: profile[k] for k in ('package', 'version', 'sha256', 'certificate_sha256')},
                                sdk=self.sdk[0], extra_native={'libfixture.so': addon.sha(elf)},
                                native_requirements=profile['native_requirements'],
                                platform_framework_sha256=profile['platform_framework_sha256'],
                                filter_asset_contract_sha256=addon.sha(self.asset_path.read_bytes()),
                                shared_policy_manifest_sha256=profile['policy']['sha256'])
                path = self.root / 'configs/runtime.json'
                path.write_text(json.dumps(contract))
                profile['runtime_contract'] = {'source': 'configs/runtime.json', 'sha256': addon.sha(path.read_bytes())}
            self.profiles[name] = profile
            self.files[name] = addon.payload(profile, self.apk, self.sdk, self.native, self.policy,
                                              extra_assets=self.asset_inputs)
        peers = dict(schema=1, profiles={name: {other + '-retention': dict(profile=other,
                     files={p: addon.sha(data) for p, data in self.files[other].items()})}
                     for name, other in [('camera', 'photos'), ('photos', 'camera')]})
        path = self.root / 'configs/peers.json'
        path.write_text(json.dumps(peers))
        for profile in self.profiles.values():
            profile['peer_history']['sha256'] = addon.sha(path.read_bytes())

    def inspect(self, profile, data):
        path = self.root / (profile + '.zip')
        path.write_bytes(data)
        return addon.inspect_archive(path, self.profiles[profile])

    def test_all_six_archives_bind_the_generated_hook_and_receipt(self):
        for name in ('camera', 'photos'):
            profile = self.profiles[name]
            receipt = json.loads(self.files[name]['product/etc/realme-oem-addons/' + name + '.json'])
            self.assertEqual(receipt['ota_retention'], profile['ota_retention'])
            hook = self.files[name][co.HOOKS[name]]
            self.assertIn(profile['platform_framework_sha256'].encode() + b'  system/framework/oplus-fwk.jar', hook)
            for action in ('install', 'remove', 'check'):
                with self.subTest(profile=name, action=action):
                    data = addon.archive_bytes(profile, self.files[name], action, b'fixture certificate')
                    result = self.inspect(name, data)
                    self.assertEqual(result['payload_present'], action == 'install')
                    if action == 'install':
                        with zipfile.ZipFile(io.BytesIO(data)) as archive:
                            selection = compat.bundle(archive, name)
                        self.assertEqual(selection['target_bindings']['system/framework/oplus-fwk.jar'],
                                         profile['platform_framework_sha256'])

    def test_builder_rejects_changed_hook_before_archiving(self):
        files = dict(self.files['camera'])
        files[co.HOOKS['camera']] += b'\n# unreviewed change\n'
        with self.assertRaisesRegex(ValueError, 'hook differs'):
            addon.archive_bytes(self.profiles['camera'], files, 'install', b'fixture certificate')

    def test_hash_only_remove_archive_rejects_rehashed_wrong_hook(self):
        profile = self.profiles['camera']
        data = addon.archive_bytes(profile, self.files['camera'], 'remove', b'fixture certificate')
        output = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(data)) as before, zipfile.ZipFile(output, 'w') as after:
            for name in before.namelist():
                content = before.read(name)
                if name == 'manifest.sha256':
                    content = content.replace(addon.sha(self.files['camera'][co.HOOKS['camera']]).encode(), b'0' * 64)
                after.writestr(name, content)
        with self.assertRaisesRegex(ValueError, 'hook differs'):
            self.inspect('camera', output.getvalue())

    def test_conflicting_or_changed_template_contract_is_refused(self):
        for mode in ('legacy', 'template'):
            profile = copy.deepcopy(self.profiles['photos'])
            if mode == 'legacy':
                profile['explicit_reinstall_after_ota'] = True
            else:
                profile['ota_retention']['template_sha256'] = '0' * 64
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, 'retention contract'):
                addon.payload(profile, self.apk, self.sdk, self.native, self.policy,
                              extra_assets=self.asset_inputs)
