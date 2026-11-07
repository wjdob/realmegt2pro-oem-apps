"""Real file transactions for narrow Camera privilege and app-directory migration."""
import io
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

import oem_addons as addon
import test_oem_system_placement as system


PERMISSIONS = 'system/etc/permissions/privapp-permissions-realme-camera.xml'


class PrivilegedCameraTests(unittest.TestCase):
    setUp = system.SystemCameraPlacementTests.setUp
    prepare_system = system.SystemCameraPlacementTests.prepare_system
    stage = system.SystemCameraPlacementTests.stage
    run_shell = system.SystemCameraPlacementTests.run_shell
    assert_contents = system.SystemCameraPlacementTests.assert_contents

    def prepare_privileged(self):
        self.prepare_system()
        self.old = dict(self.files)
        self.profile.update(app_class='priv-app', upgrade_from_system_app=True,
                            privileged_permissions=dict(
                                grant=sorted(addon.CAMERA_PRIVILEGED_GRANTS),
                                deny=sorted(addon.CAMERA_PRIVILEGED_DENIALS)))
        self.files = {p.replace('system/app/RealmeStockCamera/',
                               'system/priv-app/RealmeStockCamera/', 1): data
                      for p, data in self.old.items()}
        self.files[PERMISSIONS] = addon.privileged_permissions(self.profile)
        self.old_manifest = {p: addon.sha(data) for p, data in self.old.items()}
        self.archive = addon.archive_bytes(self.profile, self.files, 'install', b'certificate',
                                           {'beta9': self.old_manifest})
        with zipfile.ZipFile(io.BytesIO(self.archive)) as z:
            self.prefix = z.read('META-INF/com/google/android/update-binary').decode().split('\nui "$TITLE:')[0]

    def test_only_system_camera_is_granted_and_other_privileges_are_denied(self):
        self.prepare_privileged()
        xml = ET.fromstring(self.files[PERMISSIONS])
        package, = list(xml)
        self.assertEqual(package.attrib, {'package': 'com.oplus.camera'})
        self.assertEqual({p.get('name') for p in package.findall('permission')},
                         {'android.permission.SYSTEM_CAMERA'})
        self.assertEqual({p.get('name') for p in package.findall('deny-permission')},
                         addon.CAMERA_PRIVILEGED_DENIALS)
        for change in [dict(grant=['android.permission.WRITE_SECURE_SETTINGS'], deny=[]),
                       dict(grant=['android.permission.SYSTEM_CAMERA'], deny=[]),
                       dict(grant=['android.permission.SYSTEM_CAMERA'] * 2,
                            deny=sorted(addon.CAMERA_PRIVILEGED_DENIALS))]:
            with self.assertRaises(ValueError):
                addon.privileged_permissions(dict(self.profile, privileged_permissions=change))
        with self.assertRaises(ValueError):
            addon.app_path(dict(self.profile, id='photos', package='com.coloros.gallery3d'))
        corrupt = dict(self.files, **{PERMISSIONS: b'<permissions />'})
        with self.assertRaises(ValueError):
            addon.archive_bytes(self.profile, corrupt, 'install', b'certificate')

    def test_one_upgrade_moves_known_camera_and_preserves_unrelated_files(self):
        self.prepare_privileged()
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
prune
''')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assert_contents(root, self.files)
            self.assertFalse((root/'system/app/RealmeStockCamera').exists())
            self.assertTrue((root/'system/priv-app/RealmeStockCamera/base.apk').is_file())

    def test_explicit_audited_grant_is_supported_without_granting_other_requests(self):
        self.prepare_privileged()
        wake = 'android.permission.TURN_SCREEN_ON'
        spec = dict(grant=sorted(addon.CAMERA_PRIVILEGED_GRANTS | {wake}),
                    deny=sorted(addon.CAMERA_PRIVILEGED_DENIALS - {wake}))
        xml = ET.fromstring(addon.privileged_permissions(
            dict(self.profile, privileged_permissions=spec)))
        package = xml.find('privapp-permissions')
        self.assertEqual({x.get('name') for x in package.findall('permission')},
                         {'android.permission.SYSTEM_CAMERA', wake})
        self.assertIn('android.permission.WRITE_SECURE_SETTINGS',
                      {x.get('name') for x in package.findall('deny-permission')})
        for bad in [dict(spec, grant=spec['grant'] + ['android.permission.MANAGE_USERS']),
                    dict(spec, deny=spec['deny'] + [wake])]:
            with self.assertRaises(ValueError):
                addon.privileged_permissions(dict(self.profile, privileged_permissions=bad))

    def test_reviewed_camera_policy_is_scoped_and_generates_without_implicit_grants(self):
        self.prepare_privileged()
        review = json.loads((addon.ROOT / 'configs/oem-camera-privileges-23.2.json').read_text())
        self.assertEqual(review['package'], 'com.oplus.camera')
        self.assertEqual(review['apk_sha256'],
                         '9193280507b65cd35b0dda690d86a9f041392ba832fe5b036900207e409f0850')
        spec = review['privileged_permissions']
        package = ET.fromstring(addon.privileged_permissions(
            dict(self.profile, privileged_permissions=spec))).find('privapp-permissions')
        self.assertEqual({x.get('name') for x in package.findall('permission')},
                         {'android.permission.SYSTEM_CAMERA', 'android.permission.TURN_SCREEN_ON',
                          'android.permission.WRITE_SECURE_SETTINGS'})
        self.assertEqual({x.get('name') for x in package.findall('deny-permission')},
                         {'android.permission.WRITE_MEDIA_STORAGE', 'android.permission.UPDATE_DEVICE_STATS',
                          'android.permission.INTERACT_ACROSS_USERS'})

    def test_failed_move_restores_old_app_and_removes_new_permission_file(self):
        self.prepare_privileged()
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
 case "$2" in */priv-app/RealmeStockCamera/base.apk.realme-new) printf partial > "$2"; return 1 ;;
 *) command cp "$@" ;; esac
}
apply_files
''')
            self.assertNotEqual(result.returncode, 0)
            self.assert_contents(root, self.old)
            self.assertFalse((root/PERMISSIONS).exists())
            self.assertFalse((root/'system/priv-app/RealmeStockCamera/base.apk').exists())
            self.assertFalse(list(root.rglob('*.realme-new')))

    def test_unknown_file_in_old_tree_stops_migration_without_cleanup(self):
        self.prepare_privileged()
        with tempfile.TemporaryDirectory(dir=self.scratch) as directory:
            root = Path(directory)
            self.stage(root)
            unknown = root/'system/app/RealmeStockCamera/unknown.so'
            unknown.write_bytes(b'unknown owner')
            result = self.run_shell(root, 'select_upgrade\n')
            self.assertNotEqual(result.returncode, 0)
            self.assert_contents(root, self.old)
            self.assertEqual(unknown.read_bytes(), b'unknown owner')
            self.assertFalse((root/PERMISSIONS).exists())

    def test_same_apk_in_both_trees_is_not_a_valid_predecessor(self):
        self.prepare_privileged()
        mixed = dict(self.old_manifest)
        mixed['system/priv-app/RealmeStockCamera/base.apk'] = self.profile['sha256']
        with self.assertRaises(ValueError):
            addon.checked_upgrades(self.profile, self.files, {'mixed': mixed})

    def test_parent_metadata_is_limited_to_owned_trees_even_without_legacy_root(self):
        self.prepare_system()
        with tempfile.TemporaryDirectory(dir=self.scratch) as directory:
            root = Path(directory)
            self.stage(root, fresh=True)
            result = self.run_shell(root, '''
chmod() { printf '%s\n' "$2" >> "$WORK/parent-metadata"; }
prepare_parent "$SYSTEM/app/RealmeStockCamera/lib/arm64/new.so"
''')
            self.assertEqual(result.returncode, 0, result.stderr)
            paths = (root/'parent-metadata').read_text().splitlines()
            expected = root/'system/app/RealmeStockCamera'
            self.assertEqual(set(paths), {str(expected), str(expected/'lib'), str(expected/'lib/arm64')})


if __name__ == '__main__':
    unittest.main()
