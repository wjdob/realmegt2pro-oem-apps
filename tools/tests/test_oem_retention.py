"""Execute generated retention stages on synthetic private host filesystems.

Mounts, device properties and chown are mocked; hashing, backup/copy, preflight,
journaling and rollback execute the generated shell. This is not an Android OTA.
"""
import copy
import hashlib
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import oem_coinstall as ownership
import oem_ota_compat as compat
import oem_retention as retention


def sha(data):
    return hashlib.sha256(data).hexdigest()


class OemRetentionTests(unittest.TestCase):
    def setUp(self):
        scratch = retention.ROOT / 'tmp/oem-coinstall-transactions'
        scratch.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='retention-', dir=scratch)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.target = self.root / 'target'
        self.cache = self.root / 'cache'
        self.cache.mkdir()
        self.base = {p: ('base:' + p).encode() for p in compat.COMPILER_INPUTS | compat.POLICY}
        self.base['odm/lib64/libAlgoProcess.so'] = b'unchanged native backend'
        for root in (self.source, self.target):
            self.write(root, self.base)
            self.write(root, {'system/build.prop': b'ro.build.version.sdk=36\nro.lineage.version=23.2-fixture\n'})
        (self.source / 'system/product').symlink_to(self.source / 'product', target_is_directory=True)
        self.payloads = {}
        self.descriptors = {}
        for profile in ('camera', 'photos'):
            app = ownership.APP_ROOTS[profile]
            apk = ('apk:' + profile).encode()
            files = {
                app + '/base.apk': apk,
                app + '/lib/arm64/libfixture.so': ('native:' + profile).encode(),
                'product/framework/realme-camera-unit-sdk.jar': b'identical shared SDK',
                'product/etc/realme-oem-addons/' + profile + '.json': ('receipt:' + profile).encode(),
                ownership.HOOKS[profile]: b'old no-op hook',
            }
            files.update({p: b'patched:' + self.base[p] for p in compat.POLICY})
            manifest = {p: sha(data) for p, data in files.items()}
            package, signer = compat.PACKAGES[profile]
            descriptor = {
                'profile': profile, 'package': package, 'receipt_signer_sha256': signer,
                'apk_sha256': sha(apk), 'manifest': manifest,
                'target_bindings': {p: sha(data) for p, data in self.base.items()},
            }
            self.payloads[profile] = files
            self.descriptors[profile] = descriptor
            self.write(self.source, files)
        for profile in ('camera', 'photos'):
            hook = ownership.HOOKS[profile]
            data = retention.render(self.descriptors[profile])
            self.descriptors[profile]['manifest'][hook] = sha(data)
            self.payloads[profile][hook] = data
            self.write(self.source, {hook: data})
        self.markers = {
            'product/priv-app/GmsCore/GmsCore.apk': b'separately selected Google app',
            'data/user/0/com.oplus.camera/preferences': b'camera user preference',
            'data/user/0/com.coloros.gallery3d/preferences': b'gallery user preference',
            'data/system/locksettings.db': b'credential marker',
            'persist/calibration': b'calibration marker',
            'dev/block/by-name/abl_a': b'protected firmware marker',
        }
        self.write(self.source, self.markers)
        self.write(self.target, self.markers)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def write(root, files):
        for rel, data in files.items():
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

    def stage(self, profile, stage, *, fail_copy='', free_kib=None):
        script = retention.render(self.descriptors[profile]).decode()
        prefix = script.split('# OEM_RETENTION_ENTRY\n')[0]
        # Only environment/mount/ownership capabilities are replaced. Actual
        # source-path/hash, target mapping, backup and transaction logic run.
        driver = prefix + '\n' + '\n'.join([
            'S=' + shlex.quote(str(self.source / 'system')),
            'POST=' + shlex.quote(str(self.target)),
            'C=' + shlex.quote(str(self.cache)),
            'SOURCE=' + shlex.quote(str(self.source)),
            'export ADDOND_VERSION=3',
            'require_environment() { no_links "$C" && no_links "$POST/system" && no_links "$POST/product" || fail fixture; }',
            'source_path() { printf "%s/%s\\n" "$SOURCE" "$1"; }',
            'mountpoint() { test -d "$2"; }',
            'ensure_readonly() { case "$1" in odm) ODM_ROOT="$POST/odm" ;; vendor) VENDOR_ROOT="$POST/vendor" ;; *) return 1 ;; esac; }',
            'chown() { test "$1" = 0:0; }',
            'copy_file() { if [ -n "${FAIL_COPY_SUFFIX:-}" ]; then case "$2" in *"$FAIL_COPY_SUFFIX") return 1 ;; esac; fi; mkdir -p "${2%/*}" && cp -a "$1" "$2"; }',
            'backup_file() { copy_file "$1" "$C/$1"; }',
            'get_output_path() { output_rel=${1#"$S"/}; case "$output_rel" in product/*) printf "%s/%s\\n" "$POST" "$output_rel" ;; *) printf "%s/system/%s\\n" "$POST" "$output_rel" ;; esac; }',
        ])
        if free_kib is not None:
            driver += '\nfree_kib() { printf "%s\\n" ' + shlex.quote(str(free_kib)) + '; }'
        driver += '\nrun_stage ' + shlex.quote(stage)
        return subprocess.run(
            ['sh', '-s'], input=driver, text=True, capture_output=True, timeout=30,
            env=dict(os.environ, FAIL_COPY_SUFFIX=fail_copy),
        )

    def succeed(self, profile, stage, **options):
        result = self.stage(profile, stage, **options)
        self.assertEqual(result.returncode, 0, result.stderr)

    def backup(self, profiles):
        for profile in profiles:
            for stage in ('pre-backup', 'backup', 'post-backup'):
                self.succeed(profile, stage)

    def restore(self, profiles):
        for profile in profiles:
            for stage in ('pre-restore', 'restore', 'post-restore'):
                self.succeed(profile, stage)

    def assert_markers(self):
        for root in (self.source, self.target):
            for rel, data in self.markers.items():
                self.assertEqual((root / rel).read_bytes(), data)

    def assert_payload(self, profiles):
        for profile in profiles:
            for path, data in self.payloads[profile].items():
                if path == ownership.HOOKS[profile]:
                    continue  # preserved separately by the standard parent tool
                self.assertEqual((self.target / path).read_bytes(), data)
        self.assert_markers()

    def test_camera_only_retains_selected_files_and_data_markers(self):
        self.backup(['camera'])
        self.restore(['camera'])
        self.assert_payload(['camera'])
        self.assertFalse((self.target / ownership.APP_ROOTS['photos']).exists())

    def test_gallery_only_does_not_install_camera(self):
        self.backup(['photos'])
        self.restore(['photos'])
        self.assert_payload(['photos'])
        self.assertFalse((self.target / ownership.APP_ROOTS['camera']).exists())

    def test_both_share_identical_policy_and_sdk(self):
        self.backup(['camera', 'photos'])
        self.restore(['camera', 'photos'])
        self.assert_payload(['camera', 'photos'])

    def test_reverse_hook_order_and_idempotent_restore(self):
        self.backup(['photos', 'camera'])
        self.restore(['photos', 'camera'])
        self.restore(['camera', 'photos'])
        self.assert_payload(['camera', 'photos'])

    def test_changed_installed_apk_refuses_backup(self):
        (self.source / ownership.APP_ROOTS['camera'] / 'base.apk').write_bytes(b'unknown app')
        result = self.stage('camera', 'backup')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Installed bytes differ', result.stderr)
        self.assertFalse((self.target / ownership.APP_ROOTS['camera']).exists())
        self.assert_markers()

    def test_incompatible_policy_refuses_before_any_target_copy(self):
        self.backup(['camera'])
        rel = 'vendor/etc/selinux/vendor_sepolicy.cil'
        (self.target / rel).write_bytes(b'new incompatible target policy')
        result = self.stage('camera', 'restore')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Incompatible target', result.stderr)
        self.assertFalse((self.target / ownership.APP_ROOTS['camera']).exists())
        for path in compat.POLICY:
            self.assertEqual((self.target / path).read_bytes(), self.base[path])
        self.assert_markers()

    def test_unsupported_api_refuses_before_restore(self):
        self.backup(['photos'])
        (self.target / 'system/build.prop').write_bytes(b'ro.build.version.sdk=37\n')
        result = self.stage('photos', 'restore')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Wrong target API', result.stderr)
        self.assertFalse((self.target / ownership.APP_ROOTS['photos']).exists())

    def test_injected_copy_failure_rolls_back_files_and_policy(self):
        self.backup(['camera'])
        result = self.stage('camera', 'restore', fail_copy='libfixture.so.realme-ota-new')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Restore copy failed', result.stderr)
        for path, data in self.payloads['camera'].items():
            if path in compat.POLICY:
                self.assertEqual((self.target / path).read_bytes(), self.base[path])
            elif path != ownership.HOOKS['camera']:
                self.assertFalse((self.target / path).exists(), path)
        self.assertFalse(list(self.target.rglob('*.realme-ota-new')))
        self.assert_markers()

    def test_unowned_path_and_changed_signer_are_rejected(self):
        bad = copy.deepcopy(self.descriptors['camera'])
        bad['manifest']['system/bin/not-owned'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'ownership'):
            retention.render(bad)
        bad = copy.deepcopy(self.descriptors['photos'])
        bad['receipt_signer_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'identity'):
            retention.render(bad)

    def test_corrupted_backup_refuses_before_target_changes(self):
        self.backup(['camera'])
        apk = self.source / ownership.APP_ROOTS['camera'] / 'base.apk'
        (self.cache / str(apk).lstrip('/')).write_bytes(b'corrupted staged APK')
        result = self.stage('camera', 'restore')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Backup bytes differ', result.stderr)
        self.assertFalse((self.target / ownership.APP_ROOTS['camera']).exists())
        self.assert_markers()

    def test_target_collision_refuses_before_policy_changes(self):
        self.backup(['photos'])
        app = self.target / ownership.APP_ROOTS['photos'] / 'base.apk'
        self.write(self.target, {str(app.relative_to(self.target)): b'unknown target APK'})
        result = self.stage('photos', 'restore')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Target collision', result.stderr)
        self.assertEqual(app.read_bytes(), b'unknown target APK')
        for path in compat.POLICY:
            self.assertEqual((self.target / path).read_bytes(), self.base[path])

    def test_symlink_target_cannot_redirect_into_owner_data(self):
        self.backup(['camera'])
        app = self.target / ownership.APP_ROOTS['camera'] / 'base.apk'
        app.parent.mkdir(parents=True)
        owner = self.target / 'data/system/locksettings.db'
        app.symlink_to(owner)
        result = self.stage('camera', 'restore')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Linked target', result.stderr)
        self.assert_markers()

    def test_insufficient_space_refuses_before_copy(self):
        self.backup(['camera'])
        result = self.stage('camera', 'restore', free_kib=0)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Insufficient target filesystem space', result.stderr)
        self.assertFalse((self.target / ownership.APP_ROOTS['camera']).exists())
        self.assert_markers()

    def test_unknown_stage_does_not_load_helpers_or_touch_files(self):
        script = self.root / 'retention.sh'
        script.write_bytes(retention.render(self.descriptors['camera']))
        result = subprocess.run(['sh', str(script), '--unknown'], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 64)
        self.assertEqual(result.stderr, '')
        self.assertFalse(list(self.cache.iterdir()))

    def test_non_v3_parent_is_refused_before_device_queries(self):
        prefix = retention.render(self.descriptors['camera']).decode().split('# OEM_RETENTION_ENTRY\n')[0]
        result = subprocess.run(
            ['sh', '-s'], input=prefix + '\nADDOND_VERSION=2\nrequire_environment\n',
            capture_output=True, text=True, timeout=5,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Reviewed V3 parent required', result.stderr)
        self.assertNotIn('parameter not set', result.stderr)

    def test_canonical_hook_bytes_survive_reviewed_parent_rewrites(self):
        script = retention.render(self.descriptors['camera'])
        result = subprocess.run([
            'sed', '-e', r'0,/#!\/sbin\/sh/{s|#!/sbin/sh|#!/system/bin/sh|}',
            '-e', r's|. /tmp/backuptool.functions|. /postinstall/tmp/backuptool.functions|g',
        ], input=script, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, script)

    def test_selected_legacy_peer_blocks_backup(self):
        self.write(self.source, {ownership.HOOKS['photos']: b'#!/system/bin/sh\n# old no-op hook\n'})
        result = self.stage('camera', 'backup')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('compatible retention bundle', result.stderr)

    def test_absent_peer_does_not_block_independent_selection(self):
        for rel in self.payloads['photos'].keys() - self.payloads['camera'].keys():
            path = self.source / rel
            if path.is_file():
                path.unlink()
        self.succeed('camera', 'backup')

    def test_partial_peer_refuses_before_backup(self):
        (self.source / 'product/etc/realme-oem-addons/photos.json').unlink()
        result = self.stage('camera', 'backup')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Partial selected peer', result.stderr)
