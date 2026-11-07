"""Exercise real installer file transactions on isolated host fixtures, not mounts."""
import io
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import oem_addons as addon
import oem_policy as policy


class PolicyTransactionTests(unittest.TestCase):
    def setUp(self):
        self.scratch = addon.ROOT / 'tmp/oem-policy-tests'
        self.scratch.mkdir(exist_ok=True)
        self.profile = dict(json.loads(addon.CONFIG.read_text())['profiles']['camera'], id='camera')
        self.profile['policy'] = {'sha256': '0' * 64}
        self.before = {p: ('original ' + p).encode() for p in policy.PATCH_PATHS}
        self.after = {p: ('candidate ' + p).encode() for p in policy.PATCH_PATHS}
        support = {}
        for path in policy.PATCH_PATHS:
            support[policy.STORAGE + 'preimages/' + Path(path).name] = self.before[path]
            support[policy.STORAGE + 'postimages/' + Path(path).name] = self.after[path]
        support[policy.STORAGE + 'patches.tsv'] = ''.join(
            addon.sha(self.after[p]) + ' ' + addon.sha(self.before[p]) + ' ' + p + '\n'
            for p in sorted(policy.PATCH_PATHS)).encode()
        support[policy.STORAGE + 'inputs.sha256'] = b''
        self.support = support
        apk = io.BytesIO()
        with zipfile.ZipFile(apk, 'w'):
            pass
        self.files = addon.payload(self.profile, apk.getvalue(), policy=({}, {**support, **self.after}))
        archive = addon.archive_bytes(self.profile, self.files, 'install', b'fixture certificate')
        with zipfile.ZipFile(io.BytesIO(archive)) as z:
            self.prefix = z.read('META-INF/com/google/android/update-binary').decode().split('\nui "$TITLE:')[0]

    def stage(self, root, installed=False):
        for rel, data in self.files.items():
            source = root / 'payload' / rel
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(data)
            if installed:
                target = root / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                rollback = root / 'rollback' / rel
                rollback.parent.mkdir(parents=True, exist_ok=True)
                rollback.write_bytes(data)
        for rel, data in self.support.items():
            target = root / 'policy' / rel[len(policy.STORAGE):]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        if not installed:
            for rel, data in self.before.items():
                target = root / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
        (root / 'manifest.sha256').write_text(''.join(
            addon.sha(data) + '  ' + rel + '\n' for rel, data in sorted(self.files.items())))
        (root / 'journal').write_text('')
        sentinel = root / 'product/app/Aperture/base.apk'
        sentinel.parent.mkdir(parents=True)
        sentinel.write_bytes(b'Lineage Camera stays')

    def run_transaction(self, root, body):
        setup = '''
ui() { :; }
chown() { :; }
chcon() { printf '%s\\n' "$*" >> "$WORK/labels"; }
sync() { :; }
WORK=''' + shlex.quote(str(root)) + '''
PRODUCT=$WORK/product
SYSTEM=$WORK/system
WRITABLE=1
'''
        return subprocess.run(['sh', '-c', self.prefix + setup + body,
                               'installer', '3', '1', 'unused.zip'], capture_output=True, text=True)

    def test_install_replaces_only_reviewed_policy_and_preserves_labels(self):
        with tempfile.TemporaryDirectory(dir=self.scratch) as tmp:
            root = Path(tmp)
            self.stage(root)
            result = self.run_transaction(root, 'ACTION=install\napply_files\n')
            self.assertEqual(result.returncode, 0, result.stderr)
            for path, data in self.after.items():
                self.assertEqual((root / path).read_bytes(), data)
            self.assertIn('u:object_r:mac_perms_file:s0', (root / 'labels').read_text())
            self.assertIn('u:object_r:seapp_contexts_file:s0', (root / 'labels').read_text())
            self.assertEqual((root / 'product/app/Aperture/base.apk').read_bytes(), b'Lineage Camera stays')

    def test_remove_restores_preimages_even_after_installed_support_is_deleted(self):
        with tempfile.TemporaryDirectory(dir=self.scratch) as tmp:
            root = Path(tmp)
            self.stage(root, installed=True)
            result = self.run_transaction(root, 'ACTION=remove\napply_files\n')
            self.assertEqual(result.returncode, 0, result.stderr)
            for path, data in self.before.items():
                self.assertEqual((root / path).read_bytes(), data)
            self.assertFalse((root / 'product/app/RealmeStockCamera/base.apk').exists())
            self.assertFalse((root / (policy.STORAGE + 'preimages/product_sepolicy.cil')).exists())
            self.assertTrue((root / 'product/app/Aperture/base.apk').exists())

    def test_failed_install_restores_original_policy_and_removes_staged_files(self):
        with tempfile.TemporaryDirectory(dir=self.scratch) as tmp:
            root = Path(tmp)
            self.stage(root)
            result = self.run_transaction(root, '''
ACTION=install
mv() { case "$2" in */product_sepolicy.cil) return 1 ;; *) command mv "$@" ;; esac; }
trap cleanup EXIT
apply_files
''')
            self.assertNotEqual(result.returncode, 0)
            for path, data in self.before.items():
                self.assertEqual((root / path).read_bytes(), data)
            self.assertFalse((root / 'product/app/RealmeStockCamera/base.apk').exists())
            self.assertFalse(list(root.rglob('*.realme-new')))
            self.assertTrue((root / 'product/app/Aperture/base.apk').exists())

    def test_policy_hook_never_restores_stale_policy_or_apps_across_ota(self):
        source = self.files['system/addon.d/80-realme-camera.sh'].decode()
        self.assertNotIn('restore_file', source)
        self.assertNotIn('backup_file', source)
        self.assertNotIn('cp ', source)
        for stage in ('backup', 'restore', 'post-restore'):
            result = subprocess.run(['sh', '-c', source, 'hook', stage], capture_output=True)
            self.assertEqual(result.returncode, 0)

    def test_failed_remove_restores_installed_policy_and_cleans_partial_copy(self):
        with tempfile.TemporaryDirectory(dir=self.scratch) as tmp:
            root = Path(tmp)
            self.stage(root, installed=True)
            result = self.run_transaction(root, '''
ACTION=remove
cp() {
  case "$1" in
    */preimages/product_sepolicy_and_mapping.sha256) printf partial > "$2"; return 1 ;;
    *) command cp "$@" ;;
  esac
}
trap cleanup EXIT
apply_files
''')
            self.assertNotEqual(result.returncode, 0)
            for path, data in self.after.items():
                self.assertEqual((root / path).read_bytes(), data)
            self.assertEqual((root / 'product/app/RealmeStockCamera/base.apk').read_bytes(),
                             self.files['product/app/RealmeStockCamera/base.apk'])
            self.assertFalse(list(root.rglob('*.realme-new')))

    def test_photos_cannot_claim_camera_policy_paths(self):
        profile = dict(self.profile, id='photos')
        for path in policy.PATCH_PATHS:
            self.assertFalse(addon.allowed(profile, path))

    def test_failed_extra_unmount_retains_scratch(self):
        result = self.run_transaction(Path('/tmp/realme-oem-policy-no-real-directory'), '''
WRITABLE=0
MOUNTED_EXTRAS=vendor
umount() { return 1; }
rm() { echo UNSAFE_RM; }
true
cleanup
''')
        self.assertEqual(result.returncode, 1)
        self.assertNotIn('UNSAFE_RM', result.stdout)


if __name__ == '__main__':
    unittest.main()
