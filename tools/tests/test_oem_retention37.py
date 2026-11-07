"""Run the accepted transaction cases under API37 generation guards.

Real shell backup/restore/hash/rollback logic executes in temporary filesystems.
Mounts, Android properties and ownership are mocked, as in the API36 fixtures.
"""
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import oem_platform as platform
import oem_retention as retention
import oem_coinstall as ownership
import oem_ota_compat as compat
import test_oem_retention as legacy


class Api37RetentionTests(legacy.OemRetentionTests):
    def setUp(self):
        super().setUp()
        self.base = {p.replace('202504', '202604'): data for p, data in self.base.items()}
        self.base.update({p: b'202604\n' for p in compat.VERSION_INPUTS37})
        for root in (self.source, self.target):
            # Parent setup has already installed the reviewed source postimages.
            # Add the new compiler/version paths without replacing those files.
            self.write(root, {p: data for p, data in self.base.items() if p not in compat.POLICY})
            self.write(root, {'system/build.prop': b'ro.build.version.sdk=37\nro.lineage.version=24.0-fixture\n'})
        for profile, descriptor in self.descriptors.items():
            descriptor['platform'] = dict(platform.ANDROID17)
            descriptor['target_bindings'] = {p: hashlib.sha256(data).hexdigest()
                                             for p, data in self.base.items()}
            hook = ownership.HOOKS[profile]
            data = retention.render(descriptor)
            descriptor['manifest'][hook] = hashlib.sha256(data).hexdigest()
            self.payloads[profile][hook] = data
            self.write(self.source, {hook: data})

    def test_unsupported_api_refuses_before_restore(self):
        self.backup(['photos'])
        (self.target / 'system/build.prop').write_bytes(b'ro.build.version.sdk=36\nro.lineage.version=23.2-fixture\n')
        result = self.stage('photos', 'restore')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Wrong target API', result.stderr)
        self.assertFalse((self.target / ownership.APP_ROOTS['photos']).exists())
        self.assert_markers()

    def test_mismatched_api36_peer_hook_refuses_backup(self):
        descriptor = dict(self.descriptors['photos'])
        descriptor.pop('platform')
        descriptor['target_bindings'] = {p.replace('202604', '202504'): digest
                                        for p, digest in descriptor['target_bindings'].items()
                                        if p not in compat.VERSION_INPUTS37}
        hook = ownership.HOOKS['photos']
        self.write(self.source, {hook: retention.render(descriptor)})
        result = self.stage('camera', 'backup')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('compatible retention bundle', result.stderr)
        self.assert_markers()
