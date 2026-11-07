"""Check generation-bound archive descriptors and destination prerequisites."""
import copy
import json
import sys
from pathlib import Path
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import oem_ota_compat as gate
import oem_platform as platform
import oem_retention as retention
import oem_coinstall as ownership
import test_oem_ota_compat as fixture


def entries(stream):
    with zipfile.ZipFile(stream) as archive:
        return {n: archive.read(n) for n in archive.namelist()}


def manifest(files):
    return ''.join(fixture.sha(data) + '  ' + name.removeprefix('payload/') + '\n'
                   for name, data in sorted(files.items()) if name.startswith('payload/')).encode()


class Api37TargetTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.OemOtaCompatibilityTests()
        self.f.setUp()
        self.f.base['system/framework/oplus-fwk.jar'] = b'reviewed API37 OEM framework'
        self.f.base.update({p.replace('202504', '202604'): data
                            for p, data in list(self.f.base.items()) if p in gate.COMPILER_INPUTS})
        self.f.base.update({p: b'202604\n' for p in gate.VERSION_INPUTS37})

    def target(self, *, sdk=37, lineage='24.0-fixture', product=False, extra_sdk=''):
        files = entries(self.f.target(sdk=sdk))
        if product:
            files['PRODUCT/etc/build.prop'] = ('ro.lineage.version=' + lineage + '\n').encode()
        elif lineage:
            files['SYSTEM/build.prop'] += ('ro.lineage.version=' + lineage + '\n').encode()
        if extra_sdk:
            files['SYSTEM/build.prop'] += ('ro.build.version.sdk=' + extra_sdk + '\n').encode()
        return fixture.archive(list(files.items()))

    def source(self, profile, *, with_retention=False):
        files = entries(self.f.source(profile))
        old_root = 'payload/product/app/' + profile
        new_root = 'payload/' + ownership.APP_ROOTS[profile]
        files = {(new_root + name[len(old_root):] if name.startswith(old_root + '/')
                  else name): data for name, data in files.items()}
        files['payload/product/framework/realme-camera-unit-sdk.jar'] = files.pop(
            'payload/product/framework/oem-shared.jar')
        path = 'payload/product/etc/realme-oem-addons/' + profile + '.json'
        receipt = json.loads(files[path])
        receipt['platform'] = dict(platform.ANDROID17)
        policy_inputs = ''.join(fixture.sha(self.f.base[p])+'  '+p+'\n'
                                for p in sorted(gate.policy_inputs_for(receipt))).encode()
        files['policy/inputs.sha256'] = policy_inputs
        files['payload/'+gate.STORAGE+'inputs.sha256'] = policy_inputs
        if with_retention:
            receipt['ota_retention'] = retention.expected_spec(receipt)
            receipt['platform_framework_sha256'] = fixture.sha(self.f.base['system/framework/oplus-fwk.jar'])
        files[path] = json.dumps(receipt).encode()
        files['manifest.sha256'] = manifest(files)
        if with_retention:
            # Build the producer-side context before adding the hook's own digest.
            descriptor = {
                'profile': profile, 'package': receipt['package'],
                'receipt_signer_sha256': receipt['certificate_sha256'],
                'manifest': gate.hashes(files['manifest.sha256']),
                'target_bindings': {p: fixture.sha(data) for p, data in self.f.base.items()
                                    if p in gate.policy_inputs_for(receipt) | gate.POLICY
                                    or p in receipt['native_requirements']
                                    or p == 'system/framework/oplus-fwk.jar'},
                'platform': dict(platform.ANDROID17),
            }
            hook = ownership.HOOKS[profile]
            files['payload/' + hook] = retention.render_unhooked(descriptor)
            files['manifest.sha256'] = manifest(files)
        return fixture.archive(list(files.items()))

    def selected(self, profile, **kwargs):
        with zipfile.ZipFile(self.source(profile, **kwargs)) as archive:
            return gate.bundle(archive, profile)

    def check(self, selected, **kwargs):
        with zipfile.ZipFile(self.target(**kwargs)) as target:
            return gate.inspect(target, selected, self.f.certificate)

    def test_exact_api37_descriptors_and_context_pass(self):
        result = self.check([self.selected('camera'), self.selected('photos')])
        self.assertEqual(result['platform'], platform.ANDROID17)
        self.assertTrue(result['oem_target_prerequisites_pass'])

    def test_api37_retention_archive_roundtrip_is_exact(self):
        descriptor = self.selected('photos', with_retention=True)
        self.assertEqual(descriptor['platform'], platform.ANDROID17)
        self.assertTrue(self.check([descriptor])['oem_target_prerequisites_pass'])
        changed = copy.deepcopy(descriptor)
        changed.pop('platform')
        with self.assertRaisesRegex(ValueError, 'incomplete retention compiler context'):
            retention.render(changed)

    def test_api37_archive_rejects_api36_mapping_scope(self):
        files = entries(self.source('photos'))
        old = ''.join(fixture.sha(self.f.base[p])+'  '+p+'\n'
                      for p in sorted(gate.COMPILER_INPUTS)).encode()
        files['policy/inputs.sha256'] = old
        files['payload/'+gate.STORAGE+'inputs.sha256'] = old
        files['manifest.sha256'] = manifest(files)
        with zipfile.ZipFile(fixture.archive(list(files.items()))) as archive:
            with self.assertRaisesRegex(ValueError, 'unsupported compiler inputs'):
                gate.bundle(archive, 'photos')

    def test_api37_destination_version_metadata_is_hash_bound(self):
        selected = self.selected('photos')
        for path in gate.VERSION_INPUTS37:
            files = entries(self.target())
            member = 'VENDOR/'+path.split('/', 1)[1]
            files[member] = b'202704\n'
            with self.subTest(path=path), zipfile.ZipFile(fixture.archive(list(files.items()))) as target:
                with self.assertRaisesRegex(ValueError, 'target dependency differs'):
                    gate.inspect(target, [selected], self.f.certificate)

    def test_legacy_archives_remain_api36_only(self):
        with zipfile.ZipFile(self.f.source('photos')) as archive:
            old = gate.bundle(archive, 'photos')
        with self.assertRaisesRegex(ValueError, 'Android16'):
            self.check([old])

    def test_api37_archive_refuses_api36_destination(self):
        with self.assertRaisesRegex(ValueError, 'Android17'):
            self.check([self.selected('photos')], sdk=36, lineage='23.2-fixture')

    def test_mixed_generations_are_refused(self):
        with zipfile.ZipFile(self.f.source('camera')) as archive:
            old = gate.bundle(archive, 'camera')
        with self.assertRaisesRegex(ValueError, 'mixed optional-app'):
            self.check([old, self.selected('photos')])

    def test_wrong_or_missing_lineage_identity_is_refused(self):
        for lineage in ('', '23.2-fixture', 'other'):
            with self.subTest(lineage=lineage):
                with self.assertRaisesRegex(ValueError, 'LineageOS24.0'):
                    self.check([self.selected('photos')], lineage=lineage)

    def test_product_lineage_property_is_supported(self):
        self.assertTrue(self.check([self.selected('photos')], product=True)['oem_target_prerequisites_pass'])

    def test_conflicting_sdk_property_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'conflicting Android17'):
            self.check([self.selected('photos')], extra_sdk='36')

    def test_empty_selection_requires_explicit_api37_generation(self):
        with zipfile.ZipFile(self.target()) as target:
            with self.assertRaisesRegex(ValueError, 'Android16'):
                gate.inspect(target, [], self.f.certificate)
        with zipfile.ZipFile(self.target()) as target:
            result = gate.inspect(target, [], self.f.certificate, platform=platform.ANDROID17)
        self.assertEqual(result['platform'], platform.ANDROID17)

    def test_retention_kind_cannot_be_relabelled_as_api36(self):
        files = entries(self.source('photos', with_retention=True))
        path = 'payload/product/etc/realme-oem-addons/photos.json'
        receipt = json.loads(files[path]);receipt.pop('platform')
        old = ''.join(fixture.sha(self.f.base[p])+'  '+p+'\n'
                      for p in sorted(gate.COMPILER_INPUTS)).encode()
        files['policy/inputs.sha256'] = old
        files['payload/'+gate.STORAGE+'inputs.sha256'] = old
        files[path] = json.dumps(receipt).encode();files['manifest.sha256'] = manifest(files)
        with zipfile.ZipFile(fixture.archive(list(files.items()))) as archive:
            with self.assertRaisesRegex(ValueError, 'retention contract'):
                gate.bundle(archive, 'photos')


if __name__ == '__main__':
    unittest.main()
