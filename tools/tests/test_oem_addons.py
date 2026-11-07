"""Independent selection, ownership boundaries and destructive-failure regression checks."""
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile
import sys
import shlex

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import oem_addons as addon
import oem_native


class OemAddonsTests(unittest.TestCase):
    def setUp(self):
        self.scratch = addon.ROOT / 'tmp/oem-addons-tests'
        self.scratch.mkdir(parents=True, exist_ok=True)
        self.profile = dict(json.loads(addon.CONFIG.read_text())['profiles']['camera'], id='camera')
        self.profile.pop('policy', None)  # Legacy installer behavior remains supported.
        data = bytearray(64)
        data[:6] = b'\x7fELF\x02\x01'
        data[18:20] = (183).to_bytes(2, 'little')
        apk = io.BytesIO()
        with zipfile.ZipFile(apk, 'w') as z:
            z.writestr('lib/arm64-v8a/libc++_shared.so', bytes(data))
        self.files = addon.payload(self.profile, apk.getvalue())
        self.archive = addon.archive_bytes(self.profile, self.files, 'install', b'certificate')

    def test_profiles_are_separate_and_native_files_are_staged(self):
        self.assertIn('product/app/RealmeStockCamera/lib/arm64/libc++_shared.so', self.files)
        xml = self.files['product/etc/sysconfig/realme-camera.xml']
        self.assertIn(b'com.oplus.camera', xml)
        self.assertNotIn(b'com.coloros.gallery3d', xml)
        self.assertTrue(all(addon.allowed(self.profile, p) for p in self.files))
        hook = self.files['system/addon.d/80-realme-camera.sh']
        self.assertIn(b'# ADDOND_VERSION=3', hook)
        self.assertIn(b'get_output_path', hook)
        self.assertNotIn(b'81-realme-photos', hook)

    def test_extra_native_pins_and_conflicts_preserve_profile_boundary(self):
        apk = io.BytesIO()
        data = bytearray(64)
        data[:6] = b'\x7fELF\x02\x01'
        data[18:20] = (183).to_bytes(2, 'little')
        with zipfile.ZipFile(apk, 'w') as z:
            z.writestr('lib/arm64-v8a/libexisting.so', bytes(data))
        spec = dict(name='libdecoder.so', sha256=addon.sha(data))
        files = addon.payload(self.profile, apk.getvalue(), extra_native=[(spec, bytes(data))])
        self.assertEqual(files['product/app/RealmeStockCamera/lib/arm64/libdecoder.so'], data)
        self.assertTrue(all(addon.allowed(self.profile, p) for p in files))
        self.assertFalse(any('RealmeStockPhotos' in p or p.startswith('vendor/') for p in files))
        for bad in (dict(spec, sha256='0'*64), dict(spec, name='../escape.so'),
                    dict(spec, name='libexisting.so')):
            with self.assertRaises(ValueError):
                addon.payload(self.profile, apk.getvalue(), extra_native=[(bad, bytes(data))])

    def test_camera_adapter_sdk_cannot_be_installed_with_photos(self):
        config = json.loads(addon.CONFIG.read_text())
        profile = dict(config['profiles']['photos'], id='photos')
        spec = dict(config['shared_sdk'], sha256=addon.sha(b'reviewed Camera SDK'),
                    compatible_profiles=['camera'])
        profile['shared_paths'] = [spec['jar_path'], spec['xml_path']]
        apk = io.BytesIO()
        with zipfile.ZipFile(apk, 'w'):
            pass
        with self.assertRaisesRegex(ValueError, 'not reviewed for this profile'):
            addon.payload(profile, apk.getvalue(), shared=(spec, b'reviewed Camera SDK'))

    def test_native_preparation_rejects_unknown_stock_before_creating_output(self):
        with tempfile.TemporaryDirectory(dir=self.scratch) as directory:
            root = Path(directory)
            decoder, codec, output = root/'decoder.so', root/'codec.so', root/'output'
            decoder.write_bytes(b'unreviewed decoder')
            codec.write_bytes(b'unreviewed codec')
            with self.assertRaises(ValueError):
                oem_native.prepare(decoder, codec, output)
            self.assertFalse(output.exists())
            with self.assertRaises(ValueError):
                oem_native.local_decoder(decoder.read_bytes())

    def test_recovery_requires_stat_not_missing_awk(self):
        with zipfile.ZipFile(io.BytesIO(self.archive)) as z:
            script = z.read('META-INF/com/google/android/update-binary').decode()
        self.assertNotIn('awk', script)
        self.assertIn('stat -f -c %a', script)
        self.assertIn('stat -f -c %S', script)
        self.assertLess(script.index('free_tmp=$(free_kib'), script.index('if [ "$ACTION" = check ]'))

    def test_large_scratch_units_and_unsupported_block_size(self):
        body = '''
stat() { case "$3" in %a) echo 902096 ;; %S) echo 4096 ;; esac; }
free_kib /unused
'''
        result = self.run_shell(body)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), '3608384')
        unsupported = self.run_shell('''
stat() { case "$3" in %a) echo 902096 ;; %S) echo 512 ;; esac; }
free_kib /unused
''')
        self.assertNotEqual(unsupported.returncode, 0)

    def test_path_escape_and_cross_profile_rejected(self):
        for path in ('../data/secret', '/system/x', 'product/app/../x', 'product/x y'):
            with self.assertRaises(ValueError):
                addon.safe_path(path)
        self.assertFalse(addon.allowed(self.profile, 'product/app/RealmeStockPhotos/base.apk'))
        self.assertFalse(addon.allowed(self.profile, 'system/addon.d/30-gapps.sh'))

    def test_removal_and_check_carry_no_app_payload(self):
        for action in ('remove', 'check'):
            with zipfile.ZipFile(io.BytesIO(addon.archive_bytes(
                    self.profile, self.files, action, b'certificate'))) as z:
                self.assertFalse(any(n.startswith('payload/') for n in z.namelist()))
                self.assertIn("ACTION='" + action + "'", z.read(
                    'META-INF/com/google/android/update-binary').decode())

    def test_tampered_payload_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=self.scratch) as tmp:
            path = Path(tmp) / 'bad.zip'
            with zipfile.ZipFile(io.BytesIO(self.archive)) as src, zipfile.ZipFile(path, 'w') as dst:
                for name in src.namelist():
                    data = src.read(name)
                    if name.endswith('/base.apk'):
                        data += b'tampered'
                    dst.writestr(name, data)
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                addon.inspect_archive(path, self.profile)

    def prefix(self):
        with zipfile.ZipFile(io.BytesIO(self.archive)) as z:
            return z.read('META-INF/com/google/android/update-binary').decode().split(
                '\nui "$TITLE:')[0]

    def run_shell(self, body):
        return subprocess.run(['sh', '-c', self.prefix() + '\n' + body,
                               'installer', '3', '1', 'unused.zip'], capture_output=True, text=True)

    def test_failed_unmount_never_recursively_deletes_live_mount(self):
        result = self.run_shell('''
ui() { :; }
umount() { return 1; }
rm() { echo UNSAFE_RM; }
WORK=/tmp/realme-oem-test-no-real-directory
PRODUCT=$WORK/product-mount
SYSTEM_MOUNT=$WORK/system-mount
MOUNTED_PRODUCT=1
MOUNTED_SYSTEM=1
true
cleanup
''')
        self.assertEqual(result.returncode, 1)
        self.assertNotIn('UNSAFE_RM', result.stdout)

    def test_interrupted_install_rollback_preserves_other_profile(self):
        with tempfile.TemporaryDirectory(dir=self.scratch) as tmp:
            work = Path(tmp)
            camera = work / 'product/app/RealmeStockCamera/base.apk'
            photos = work / 'product/app/RealmeStockPhotos/base.apk'
            for path in (camera, photos):
                path.parent.mkdir(parents=True)
                path.write_bytes(b'keep' if path == photos else b'new camera')
            (work / 'journal').write_text('product/app/RealmeStockCamera/base.apk\n'
                                           'product/app/RealmeStockPhotos/base.apk\n')
            result = self.run_shell('''
ui() { :; }
sync() { :; }
WORK=''' + str(work) + '''
PRODUCT=$WORK/product
SYSTEM=$WORK/system
APPDIR=RealmeStockCamera
WRITABLE=1
ACTION=install
true
cleanup
''')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(camera.exists())
            self.assertEqual(photos.read_bytes(), b'keep')

    def test_actual_product_property_layout_and_wrong_sdk(self):
        with tempfile.TemporaryDirectory(dir=self.scratch) as tmp:
            root = Path(tmp)
            system = root / 'system'
            product = root / 'product'
            system.mkdir()
            (product / 'etc').mkdir(parents=True)
            (system / 'build.prop').write_text('ro.build.version.sdk=36\n')
            (product / 'etc/build.prop').write_text('ro.lineage.version=23.2-test-ferrari\n')
            body = 'SYSTEM=' + str(system) + '\nPRODUCT=' + str(product) + '\nverify_rom\n'
            self.assertEqual(self.run_shell(body).returncode, 0)
            (system / 'build.prop').write_text('ro.build.version.sdk=35\n')
            self.assertNotEqual(self.run_shell(body).returncode, 0)

    def test_wrong_device_rejected_before_scratch_or_mount(self):
        with zipfile.ZipFile(io.BytesIO(self.archive)) as z:
            script = z.read('META-INF/com/google/android/update-binary').decode()
        functions = '''
getprop() {
  case "$1" in ro.bootmode) echo recovery ;; ro.product.model) echo OtherPhone ;; esac
}
mount() { echo UNSAFE_MOUNT; return 99; }
mktemp() { echo UNSAFE_SCRATCH; return 99; }
'''
        result = subprocess.run(['sh', '-c', functions + script, 'installer', '3', '1',
                                 'unused.zip'], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('RMX3301 required', result.stdout)
        self.assertNotIn('UNSAFE_', result.stdout)

    def test_interrupted_removal_restores_owned_file(self):
        with tempfile.TemporaryDirectory(dir=self.scratch) as tmp:
            work = Path(tmp)
            rel = 'product/app/RealmeStockCamera/base.apk'
            backup = work / 'rollback' / rel
            backup.parent.mkdir(parents=True)
            backup.write_bytes(b'original camera')
            (work / 'journal').write_text(rel + '\n')
            result = self.run_shell('''
ui() { :; }
sync() { :; }
chcon() { :; }
chown() { :; }
WORK=''' + str(work) + '''
PRODUCT=$WORK/product
SYSTEM=$WORK/system
APPDIR=RealmeStockCamera
WRITABLE=1
ACTION=remove
true
cleanup
''')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((work / rel).read_bytes(), b'original camera')

    def test_v3_hook_round_trip_preserves_only_selected_profile(self):
        with tempfile.TemporaryDirectory(dir=self.scratch) as tmp:
            root = Path(tmp)
            old = root / 'old/system'
            new = root / 'new/product'
            backup = root / 'backup'
            product_files = {p: data for p, data in self.files.items() if p.startswith('product/')}
            for rel, data in product_files.items():
                source = old / rel
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_bytes(data)
            other = new / 'app/RealmeStockPhotos/base.apk'
            other.parent.mkdir(parents=True)
            other.write_bytes(b'independent Photos')
            helper = root / 'functions.sh'
            # Test the documented V3 function ABI and target-product mapping, not OTA execution.
            helper.write_text('S=' + shlex.quote(str(old)) + '\nC=' + shlex.quote(str(backup)) +
                              '\nNEW=' + shlex.quote(str(new)) + '''
backup_file() { mkdir -p "${C}${1%/*}"; cp -p "$1" "${C}$1"; }
get_output_path() { printf '%s/%s\\n' "$NEW" "${1#"$S/product/"}"; }
restore_file() { dest=$(get_output_path "$1"); mkdir -p "${dest%/*}"; mv "${C}$1" "$dest"; }
chown() { :; }
chcon() { :; }
''')
            source = self.files['system/addon.d/80-realme-camera.sh'].decode()
            script = root / 'hook.sh'
            script.write_text(source.replace('. /tmp/backuptool.functions', '. ' + shlex.quote(str(helper))))
            for stage in ('backup', 'restore', 'post-restore'):
                result = subprocess.run(['sh', str(script), stage], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            for rel, data in product_files.items():
                self.assertEqual((new / rel[len('product/'):]).read_bytes(), data)
            self.assertEqual(other.read_bytes(), b'independent Photos')

    def test_shared_sdk_is_retained_for_other_app_and_removed_for_last_app(self):
        shared = json.loads(addon.CONFIG.read_text())['shared_sdk']
        sdk_data = b'fixture SDK'
        shared = dict(shared, sha256=addon.sha(sdk_data))
        self.profile['shared_paths'] = [shared['jar_path'], shared['xml_path']]
        old_apk = next(data for path, data in self.files.items() if path.endswith('/base.apk'))
        self.files = addon.payload(self.profile, old_apk, (shared, sdk_data))
        self.archive = addon.archive_bytes(self.profile, self.files, 'remove', b'certificate')
        for other_present in (True, False):
            with self.subTest(other_present=other_present), tempfile.TemporaryDirectory(dir=self.scratch) as tmp:
                root = Path(tmp)
                for rel, data in self.files.items():
                    path = root / rel
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
                other = root / 'product/app/RealmeStockPhotos/base.apk'
                if other_present:
                    other.parent.mkdir(parents=True, exist_ok=True)
                    other.write_bytes(b'Photos stays')
                lineage = root / 'product/app/Aperture/Aperture.apk'
                lineage.parent.mkdir(parents=True)
                lineage.write_bytes(b'Lineage stays')
                (root/'manifest.sha256').write_text(''.join(addon.sha(data)+'  '+rel+'\n'
                                                            for rel,data in sorted(self.files.items())))
                (root/'journal').write_text('')
                result = self.run_shell('''
ui() { :; }
WORK='''+str(root)+'''
PRODUCT=$WORK/product
SYSTEM=$WORK/system
ACTION=remove
select_shared_retention
apply_files
''')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse((root/'product/app/RealmeStockCamera/base.apk').exists())
                for path in self.profile['shared_paths']:
                    self.assertEqual((root/path).exists(), other_present)
                if other_present:
                    self.assertEqual(other.read_bytes(), b'Photos stays')
                self.assertEqual(lineage.read_bytes(), b'Lineage stays')


if __name__ == '__main__':
    unittest.main()
