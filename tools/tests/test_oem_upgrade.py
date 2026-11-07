"""Run real predecessor recognition/replacement/rollback on private host fixtures."""
import json
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest
import zipfile
import io

import test_oem_policy as policy_tests
import oem_addons as addon
import oem_policy as policy


class CameraUpgradeTests(unittest.TestCase):
    def setUp(self):
        fixture = policy_tests.PolicyTransactionTests()
        fixture.setUp()
        self.scratch = fixture.scratch
        self.profile = dict(fixture.profile, shared_paths=['product/framework/camera-sdk.jar'])
        self.files = dict(fixture.files)
        self.sdk = 'product/framework/camera-sdk.jar'
        self.stale = 'product/app/RealmeStockCamera/lib/arm64/libobsolete.so'
        self.added = 'product/app/RealmeStockCamera/lib/arm64/libnew.so'
        self.files[self.sdk] = b'new SDK bytes'
        self.files[self.added] = b'new library bytes'
        self.profile['sha256'] = addon.sha(self.files['product/app/RealmeStockCamera/base.apk'])
        self.old = {p: data for p, data in self.files.items() if p != self.added}
        self.old[self.sdk] = b'old SDK bytes'
        self.old[self.stale] = b'obsolete dependency'
        for p in policy.PATCH_PATHS:
            self.old[p] = ('old installed ' + p).encode()
            self.old[policy.STORAGE + 'postimages/' + Path(p).name] = self.old[p]
        self.old_manifest = {p: addon.sha(data) for p, data in self.old.items()}
        self.history = {'beta7': self.old_manifest}
        self.archive = addon.archive_bytes(self.profile, self.files, 'install', b'certificate', self.history)
        with zipfile.ZipFile(io.BytesIO(self.archive)) as z:
            self.prefix = z.read('META-INF/com/google/android/update-binary').decode().split('\nui "$TITLE:')[0]
        self.originals = fixture.before

    def stage(self, root, fresh=False):
        (root / 'package.zip').write_bytes(self.archive)
        with zipfile.ZipFile(io.BytesIO(self.archive)) as z:
            for name in z.namelist():
                if name.startswith(('payload/', 'policy/', 'upgrades/')) or name == 'manifest.sha256':
                    target = root / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(z.read(name))
        values = self.originals if fresh else self.old
        for rel, data in values.items():
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        for rel in ['product/app/Aperture/base.apk', 'product/priv-app/GmsCore/base.apk',
                    'data/user/0/example/owner-marker']:
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'preserve unrelated contents')
        (root / 'journal').write_text('')

    def run_shell(self, root, body):
        setup = '''
ui() { :; }
chown() { :; }
chcon() { :; }
sync() { :; }
WORK=''' + shlex.quote(str(root)) + '''
PRODUCT=$WORK/product
SYSTEM=$WORK/system
'''
        return subprocess.run(['sh', '-c', self.prefix + setup + body, 'installer', '3', '1',
                               str(root / 'package.zip')], capture_output=True, text=True)

    def assert_contents(self, root, files):
        for rel, data in files.items():
            self.assertEqual((root / rel).read_bytes(), data, rel)
        for rel in ['product/app/Aperture/base.apk', 'product/priv-app/GmsCore/base.apk',
                    'data/user/0/example/owner-marker']:
            self.assertEqual((root / rel).read_bytes(), b'preserve unrelated contents')

    def test_upgrade_replaces_old_policy_sdk_and_removes_only_stale_dependency(self):
        with tempfile.TemporaryDirectory(dir=self.scratch) as directory:
            root = Path(directory)
            self.stage(root)
            result = self.run_shell(root, '''
select_upgrade
[ "$UPGRADING" = 1 ]
stage_upgrade_rollback
WRITABLE=1
remove_upgrade_stale
apply_files
''')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assert_contents(root, self.files)
            self.assertFalse((root / self.stale).exists())

    def test_readonly_preflight_recognizes_old_without_removing_any_file(self):
        with tempfile.TemporaryDirectory(dir=self.scratch) as directory:
            root = Path(directory)
            self.stage(root)
            result = self.run_shell(root, 'ACTION=check\nselect_upgrade\n[ "$UPGRADING" = 1 ]\n')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assert_contents(root, self.old)
            self.assertFalse((root / self.added).exists())

    def test_exact_current_install_is_recognized_without_upgrade_or_cleanup(self):
        with tempfile.TemporaryDirectory(dir=self.scratch) as directory:
            root = Path(directory)
            self.stage(root, fresh=True)
            for rel, data in self.files.items():
                target = root / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            result = self.run_shell(root, 'select_upgrade\n[ "$ALREADY_INSTALLED" = 1 ]\n[ "$UPGRADING" = 0 ]\n')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assert_contents(root, self.files)

    def test_failed_upgrade_restores_old_policy_sdk_stale_files_and_receipt(self):
        with tempfile.TemporaryDirectory(dir=self.scratch) as directory:
            root = Path(directory)
            self.stage(root)
            result = self.run_shell(root, '''
select_upgrade
stage_upgrade_rollback
WRITABLE=1
trap cleanup EXIT
remove_upgrade_stale
cp() {
 case "$2" in */camera-sdk.jar.realme-new) printf partial > "$2"; return 1 ;;
 *) command cp "$@" ;; esac
}
apply_files
''')
            self.assertNotEqual(result.returncode, 0)
            self.assert_contents(root, self.old)
            self.assertFalse((root / self.added).exists())
            self.assertFalse(list(root.rglob('*.realme-new')))

    def test_unknown_owned_file_aborts_without_cleanup_guess(self):
        with tempfile.TemporaryDirectory(dir=self.scratch) as directory:
            root = Path(directory)
            self.stage(root)
            unknown = root / 'product/app/RealmeStockCamera/lib/arm64/unknown.so'
            unknown.write_bytes(b'unrecognized owner modification')
            result = self.run_shell(root, 'select_upgrade\n')
            self.assertNotEqual(result.returncode, 0)
            self.assert_contents(root, self.old)
            self.assertEqual(unknown.read_bytes(), b'unrecognized owner modification')

    def test_mixed_or_corrupt_install_aborts_before_any_removal(self):
        for corrupt in (False, True):
            with self.subTest(corrupt=corrupt), tempfile.TemporaryDirectory(dir=self.scratch) as directory:
                root = Path(directory)
                self.stage(root)
                rel = self.sdk if corrupt else self.added
                (root / rel).write_bytes(b'mixed or edited bytes')
                expected = dict(self.old, **{rel: b'mixed or edited bytes'})
                result = self.run_shell(root, 'select_upgrade\n')
                self.assertNotEqual(result.returncode, 0)
                self.assert_contents(root, expected)

    def test_fresh_install_still_works_with_upgrade_history(self):
        with tempfile.TemporaryDirectory(dir=self.scratch) as directory:
            root = Path(directory)
            self.stage(root, fresh=True)
            result = self.run_shell(root, '''
select_upgrade
[ "$UPGRADING" = 0 ]
WRITABLE=1
apply_files
''')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assert_contents(root, self.files)

    def test_foreign_policy_baseline_or_apk_rejected_by_builder(self):
        for path in ['product/app/RealmeStockCamera/base.apk',
                     policy.STORAGE + 'preimages/product_sepolicy.cil']:
            bad = dict(self.old_manifest, **{path: '0' * 64})
            with self.subTest(path=path), self.assertRaises(ValueError):
                addon.archive_bytes(self.profile, self.files, 'install', b'certificate', {'beta7': bad})

    def test_matching_remover_does_not_accept_predecessor_upgrades(self):
        archive = addon.archive_bytes(self.profile, self.files, 'remove', b'certificate', self.history)
        with zipfile.ZipFile(io.BytesIO(archive)) as z:
            self.assertFalse(any(name.startswith('upgrades/') for name in z.namelist()))
            self.assertIn(b"HAS_UPGRADES='0'", z.read('META-INF/com/google/android/update-binary'))

    def test_known_upgrade_budget_keeps_full_staging_but_skips_unchanged_apk_copy(self):
        app = 'product/app/RealmeStockCamera/base.apk'
        self.files[app] = self.old[app] = b'unchanged-large-app' * 100000
        self.profile['sha256'] = addon.sha(self.files[app])
        self.old_manifest = {p: addon.sha(data) for p, data in self.old.items()}
        self.archive = addon.archive_bytes(self.profile, self.files, 'install', b'certificate', {'beta7': self.old_manifest})
        with zipfile.ZipFile(io.BytesIO(self.archive)) as z:
            self.prefix = z.read('META-INF/com/google/android/update-binary').decode().split('\nui "$TITLE:')[0]
        with tempfile.TemporaryDirectory(dir=self.scratch) as directory:
            root = Path(directory)
            self.stage(root)
            result = self.run_shell(root, '''
full=$FULL_PAYLOAD_KIB; original_product=$PRODUCT_KIB
select_upgrade
[ "$UPGRADING" = 1 ] && [ "$FULL_PAYLOAD_KIB" = "$full" ]
[ "$PRODUCT_KIB" -lt "$original_product" ]
free_product=$((PRODUCT_KIB + 16))
[ "$free_product" -ge "$PRODUCT_KIB" ] && [ "$free_product" -lt "$original_product" ]
staging=$(scratch_kib)
[ "$staging" -gt "$((full + 32768))" ]
stage_upgrade_rollback
cp() {
 case "$1" in */payload/product/app/RealmeStockCamera/base.apk) return 1 ;; esac
 command cp "$@"
}
WRITABLE=1
remove_upgrade_stale
apply_files
''')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assert_contents(root, self.files)

    def test_bad_upgrade_budget_rejected_before_changes(self):
        for bad in [b'not numbers\n', b'-1 16384\n', b'99999999 16384\n']:
            with self.subTest(bad=bad), tempfile.TemporaryDirectory(dir=self.scratch) as directory:
                original = self.archive
                try:
                    with zipfile.ZipFile(io.BytesIO(original)) as z:
                        members = {n: z.read(n) for n in z.namelist()}
                    members['upgrades/beta7.space'] = bad
                    buffer = io.BytesIO()
                    with zipfile.ZipFile(buffer, 'w') as z:
                        for name, value in members.items():
                            z.writestr(name, value)
                    self.archive = buffer.getvalue()
                    root = Path(directory)
                    self.stage(root)
                    result = self.run_shell(root, 'select_upgrade\n')
                    self.assertNotEqual(result.returncode, 0)
                    self.assert_contents(root, self.old)
                finally:
                    self.archive = original

    def test_inspection_rejects_understated_delta_budget(self):
        with zipfile.ZipFile(io.BytesIO(self.archive)) as z:
            members = {n: z.read(n) for n in z.namelist()}
        members['upgrades/beta7.space'] = b'4096 16384\n'
        with tempfile.TemporaryDirectory(dir=self.scratch) as directory:
            path = Path(directory)/'bad-budget.zip'
            with zipfile.ZipFile(path, 'w') as z:
                for name, value in members.items():
                    z.writestr(name, value)
            with self.assertRaisesRegex(ValueError, 'budget does not match'):
                addon.inspect_archive(path, self.profile)

    def test_delta_budget_reserves_full_new_files_without_reclaim_credit(self):
        files = {'system/framework/sdk.jar': b'n' * 3072,
                 'system/app/large.apk': b'unchanged' * 1024,
                 'product/etc/receipt.json': b'r' * 1024}
        previous = {p: addon.sha(value) for p, value in files.items()}
        previous['system/framework/sdk.jar'] = addon.sha(b'old')
        self.assertEqual(addon.install_budget(files, previous), {'system': 4099, 'product': 16384})

    def test_archive_inspection_checks_signed_predecessor_metadata(self):
        with tempfile.TemporaryDirectory(dir=self.scratch) as directory:
            path = Path(directory) / 'install.zip'
            path.write_bytes(self.archive)
            self.assertTrue(addon.inspect_archive(path, self.profile)['payload_present'])
            with zipfile.ZipFile(io.BytesIO(self.archive)) as z:
                members = {n: z.read(n) for n in z.namelist()}
            members['upgrades/beta7.sha256'] += b'corrupt\n'
            with zipfile.ZipFile(path, 'w') as z:
                for name, data in members.items():
                    z.writestr(name, data)
            with self.assertRaisesRegex(ValueError, 'predecessor manifest hash mismatch'):
                addon.inspect_archive(path, self.profile)


if __name__ == '__main__':
    unittest.main()
