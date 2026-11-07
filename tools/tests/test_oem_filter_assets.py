"""Optional LUTs must remain pinned, reversible, and outside vendor/ODM writes."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import struct

import oem_addons as addon
import oem_camera_filter_assets as assets_tool
import test_oem_system_placement as system_tests
import test_oem_upgrade as upgrade


class OptionalFilterAssetsTests(unittest.TestCase):
    setUp = upgrade.CameraUpgradeTests.setUp
    stage = upgrade.CameraUpgradeTests.stage
    run_shell = upgrade.CameraUpgradeTests.run_shell
    assert_contents = upgrade.CameraUpgradeTests.assert_contents
    prepare_system = system_tests.SystemCameraPlacementTests.prepare_system

    def prepare_assets(self):
        self.prepare_system()
        self.assets = {'system/etc/realme-lut/example.bin': b'known LUT',
                       'system/etc/realme-lut/example2.bin': b'another LUT'}
        self.contract_work = tempfile.TemporaryDirectory(dir=self.scratch)
        self.addCleanup(self.contract_work.cleanup)
        contract = Path(self.contract_work.name) / 'assets.json'
        contract.write_text(json.dumps(dict(asset_root=addon.FILTER_ASSET_ROOT,
            files={Path(p).name: dict(sha256=addon.sha(b)) for p, b in self.assets.items()})))
        guard = patch.object(addon, 'FILTER_ASSET_CONTRACT', contract)
        guard.start()
        self.addCleanup(guard.stop)
        self.profile['extra_assets'] = [dict(path=p, sha256=addon.sha(b))
                                         for p, b in self.assets.items()]
        self.profile['native_requirements']={p:'a'*64 for p in addon.NATIVE_REQUIREMENT_PATHS}
        self.files.update(self.assets)
        self.archive = addon.archive_bytes(self.profile, self.files, 'install', b'certificate')
        with zipfile.ZipFile(io.BytesIO(self.archive)) as z:
            self.prefix = z.read('META-INF/com/google/android/update-binary').decode().split('\nui "$TITLE:')[0]

    def test_actual_transaction_installs_and_removes_exact_assets(self):
        self.prepare_assets()
        with tempfile.TemporaryDirectory(dir=self.scratch) as directory:
            root = Path(directory)
            self.stage(root, fresh=True)
            result = self.run_shell(root, '''
chmod() { printf '%s\n' "$*" >> "$WORK/chmod.log"; command chmod "$@"; }
WRITABLE=1
apply_files
known_install_matches "$WORK/manifest.sha256"
''')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assert_contents(root, self.files)
            # DrvFS without metadata does not preserve POSIX mode observations.
            self.assertIn('0755 '+str(root/'system/etc/realme-lut'),
                          (root/'chmod.log').read_text())
            result = self.run_shell(root, 'ACTION=remove\nWRITABLE=1\napply_files\nprune\n')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((root/'system/etc/realme-lut').exists())
            self.assert_contents(root, self.originals)

    def test_unknown_asset_and_symlink_rejected_by_real_readback(self):
        self.prepare_assets()
        with tempfile.TemporaryDirectory(dir=self.scratch) as directory:
            root = Path(directory)
            self.stage(root, fresh=True)
            result = self.run_shell(root, 'WRITABLE=1\napply_files\n')
            self.assertEqual(result.returncode, 0, result.stderr)
            unknown=root/'system/etc/realme-lut/unknown.bin'
            unknown.write_bytes(b'foreign')
            result=self.run_shell(root, 'known_install_matches "$WORK/manifest.sha256"')
            self.assertNotEqual(result.returncode, 0)
            unknown.unlink()
            asset=root/next(iter(self.assets))
            asset.unlink()
            asset.symlink_to(root/'data/user/0/example/owner-marker')
            result=self.run_shell(root, 'known_install_matches "$WORK/manifest.sha256"')
            self.assertNotEqual(result.returncode, 0)

    def test_failure_after_asset_copy_rolls_back_assets_and_policy(self):
        self.prepare_assets()
        with tempfile.TemporaryDirectory(dir=self.scratch) as directory:
            root=Path(directory);self.stage(root,fresh=True)
            result=self.run_shell(root, '''
WRITABLE=1
trap cleanup EXIT
cp() {
 case "$2" in
  */camera-sdk.jar.realme-new)
   [ -f "$SYSTEM/etc/realme-lut/example.bin" ] && printf yes > "$WORK/reached-assets"
   printf partial > "$2"; return 1 ;;
  *) command cp "$@" ;;
 esac
}
apply_files
''')
            self.assertNotEqual(result.returncode,0)
            self.assertEqual((root/'reached-assets').read_text(),'yes')
            self.assertFalse((root/'system/etc/realme-lut').exists())
            self.assertFalse((root/'system/app/RealmeStockCamera/base.apk').exists())
            self.assertFalse(list(root.rglob('*.realme-new')))
            self.assert_contents(root,self.originals)

    def test_payload_requires_complete_authentic_inventory(self):
        self.prepare_assets()
        profile=dict(self.profile)
        profile.pop('policy')
        profile.pop('native_requirements')
        apk=io.BytesIO()
        with zipfile.ZipFile(apk,'w'): pass
        inputs=[(spec,self.assets[spec['path']]) for spec in profile['extra_assets']]
        files=addon.payload(profile,apk.getvalue(),extra_assets=inputs)
        self.assertTrue(all(files[p]==b for p,b in self.assets.items()))
        for bad in [inputs[:-1], [(inputs[0][0], b'changed'), inputs[1]], inputs+[inputs[0]]]:
            with self.assertRaises(ValueError): addon.payload(profile,apk.getvalue(),extra_assets=bad)

    def test_partition_and_profile_escapes_are_rejected(self):
        self.prepare_assets()
        for changed in [dict(id='photos'),dict(app_partition='product'),
                        dict(package='another.camera')]:
            with self.assertRaises(ValueError): addon.camera_assets(dict(self.profile,**changed))
        for path in ['odm/etc/camera/meishe_lut/example.bin','system/etc/realme-lut/../other',
                     'system/etc/realme-lut/unknown.bin']:
            with self.subTest(path=path):
                try: self.assertFalse(addon.allowed(self.profile,path))
                except ValueError: pass

    def test_archive_rejects_changed_asset_bytes(self):
        self.prepare_assets()
        files=dict(self.files)
        files[next(iter(self.assets))]=b'changed'
        with self.assertRaisesRegex(ValueError,'archive filter assets'):
            addon.archive_bytes(self.profile,files,'install',b'certificate')
        unbound=dict(self.profile)
        unbound.pop('native_requirements')
        with self.assertRaisesRegex(ValueError,'exact native runtime'):
            addon.archive_bytes(unbound,self.files,'install',b'certificate')

    def test_native_binding_rejects_older_rom_before_app_writes(self):
        self.prepare_assets()
        values={'system/lib64/libnativewindow.so':b'P010 plane contract',
                'odm/lib64/libAlgoInterface.so':b'optional LUT route'}
        self.profile['native_requirements']={p:addon.sha(b) for p,b in values.items()}
        self.archive=addon.archive_bytes(self.profile,self.files,'install',b'certificate')
        with zipfile.ZipFile(io.BytesIO(self.archive)) as z:
            self.prefix=z.read('META-INF/com/google/android/update-binary').decode().split('\nui "$TITLE:')[0]
        with tempfile.TemporaryDirectory(dir=self.scratch) as directory:
            root=Path(directory);self.stage(root,fresh=True)
            for path,data in values.items():
                actual=root/path.replace('odm/','odm-mount/',1)
                actual.parent.mkdir(parents=True,exist_ok=True);actual.write_bytes(data)
            result=self.run_shell(root,'verify_native_requirements')
            self.assertEqual(result.returncode,0,result.stderr)
            (root/'odm-mount/lib64/libAlgoInterface.so').write_bytes(b'older ROM')
            result=self.run_shell(root,'verify_native_requirements')
            self.assertNotEqual(result.returncode,0)
            self.assertFalse((root/'system/app/RealmeStockCamera/base.apk').exists())
            self.assert_contents(root,self.originals)

    def test_native_requirements_have_closed_paths_and_complete_hashes(self):
        self.prepare_assets()
        good={p:'a'*64 for p in addon.NATIVE_REQUIREMENT_PATHS}
        self.assertTrue(addon.native_requirements(dict(self.profile,native_requirements=good)))
        for bad in [{next(iter(good)):'a'*64}, dict(good,**{'vendor/lib64/another.so':'a'*64}),
                    {p:'wrong' for p in good}]:
            with self.assertRaises(ValueError):
                addon.native_requirements(dict(self.profile,native_requirements=bad))


class FilterNativeRouteTests(unittest.TestCase):
    def fixture(self, flags=4):
        data=bytearray(512)
        data[:6]=b'\x7fELF\x02\x01'
        struct.pack_into('<H',data,18,183)
        struct.pack_into('<Q',data,32,64)
        struct.pack_into('<HH',data,54,56,2)
        struct.pack_into('<IIQQQQQQ',data,64,1,flags,0,0,0,256,256,4096)
        struct.pack_into('<IIQQQQQQ',data,120,1,5,256,4096,4096,256,256,4096)
        old=b'/odm/etc/camera/meishe_lut/\0'
        new=b'/system/etc/realme-lut/\0'.ljust(len(old),b'\0')
        data[192:192+len(old)]=old
        data[256:]=bytes(range(256))
        expected=bytes(data[:192])+new+bytes(data[192+len(old):])
        contract=dict(native_input_sha256=addon.sha(data),native_output_sha256=addon.sha(expected),
                      old_hex=old.hex(),new_hex=new.hex(),prefix_offset=192)
        return bytes(data),contract,expected

    def test_route_preserves_executable_segment_and_all_other_bytes(self):
        original,contract,expected=self.fixture()
        actual=assets_tool.route_assets(original,contract)
        self.assertEqual(actual,expected)
        self.assertEqual(actual[256:],original[256:])
        self.assertEqual(actual[:192],original[:192])

    def test_executable_or_writable_prefix_rejected_even_when_hashes_match(self):
        for flags in [5,6,7]:
            original,contract,_=self.fixture(flags)
            with self.assertRaisesRegex(ValueError,'read-only non-executable'):
                assets_tool.route_assets(original,contract)

    def test_unreviewed_input_and_output_rejected(self):
        original,contract,_=self.fixture()
        for changed in [dict(native_input_sha256='0'*64),dict(native_output_sha256='0'*64),
                        dict(prefix_offset=193)]:
            with self.assertRaises(ValueError): assets_tool.route_assets(original,dict(contract,**changed))


if __name__ == '__main__': unittest.main()
