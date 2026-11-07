"""Execute the real peer/upgrade/file transaction functions on private host fixtures.

No device or mount: APK/SDK/native and policy bytes are synthetic, while all73
shared paths use the real layout. Native/app compatibility is a separate gate.
"""
import io
import json
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import oem_addons as addon
import oem_coinstall as co
import test_oem_policy as policy_tests


class CoInstallTransactionTests(unittest.TestCase):
    def setUp(self):
        scratch=addon.ROOT/'tmp/oem-coinstall-transactions';scratch.mkdir(parents=True,exist_ok=True)
        self.temporary=tempfile.TemporaryDirectory(dir=scratch)
        self.root=Path(self.temporary.name)
        self.configroot=self.root/'source';(self.configroot/'configs').mkdir(parents=True)
        fixture=policy_tests.PolicyTransactionTests();fixture.setUp()
        self.before=fixture.before
        camcfg=json.loads((addon.ROOT/'configs/oem-addons-camera12c-23.2.json').read_text())['profiles']['camera']
        photoscfg=json.loads(addon.CONFIG.read_text())['profiles']['photos']
        self.profiles={}
        for name, original in [('camera',camcfg),('photos',photoscfg)]:
            fields=['title','package','version','source','certificate_sha256','directory','other_directory','hook']
            self.profiles[name]={key:original[key] for key in fields}
            self.profiles[name].update(id=name,addon_release='fixture',peer_history=dict(source='configs/history.json',sha256='pending'),
                shared_paths=sorted(co.SDK_PATHS),native_requirements={p:addon.sha(b'preserve other apps and owner data') for p in addon.NATIVE_REQUIREMENT_PATHS})
        cam=self.profiles['camera'];cam.update(app_partition='system',app_class='priv-app',policy={'sha256':'0'*64},
            privileged_permissions=camcfg['privileged_permissions'],legacy_shared_paths=sorted(co.LEGACY_SDK_PATHS))
        cam['shared_paths']+=sorted(co.CAMERA_LIBRARIES)
        self.profiles['photos']['explicit_reinstall_after_ota']=True
        apk=io.BytesIO()
        with zipfile.ZipFile(apk,'w'):pass
        sdkbytes=b'fixture shared SDK'
        shared=dict(compatible_profiles=['camera','photos'],sha256=addon.sha(sdkbytes),
            library='com.oplus.camera.unit.sdk',jar_path='product/framework/realme-camera-unit-sdk.jar',
            xml_path='product/etc/permissions/realme-camera-unit-sdk.xml')
        actual=json.loads(addon.FILTER_ASSET_CONTRACT.read_text())
        self.assets={p:('fixture '+p).encode() for p in co.lut_paths()}
        fake=dict(actual,files={p.split('/')[-1]:dict(sha256=addon.sha(data)) for p,data in self.assets.items()})
        self.contract=self.configroot/'assets.json';self.contract.write_text(json.dumps(fake))
        specs=[dict(path=p,source='unused',sha256=addon.sha(data)) for p,data in self.assets.items()]
        elf=bytearray(64);elf[:6]=b'\x7fELF\x02\x01';elf[18:20]=(183).to_bytes(2,'little');elf=bytes(elf)
        platforms=[(dict(path=p,sha256=addon.sha(elf)),elf) for p in sorted(co.CAMERA_LIBRARIES)]
        self.files={}
        with patch.object(addon,'FILTER_ASSET_CONTRACT',self.contract):
            for name,profile in self.profiles.items():
                profile['sha256']=addon.sha(apk.getvalue());profile['extra_assets']=specs
                native=[(dict(name='lib'+name+'fixture.so',sha256=addon.sha(elf)),elf)]
                policy=({}, {**fixture.support,**fixture.after}) if name=='camera' else None
                self.files[name]=addon.payload(profile,apk.getvalue(),(shared,sdkbytes),native,policy,
                    platforms if name=='camera' else None,list((s,self.assets[s['path']]) for s in specs))
        history=dict(schema=1,profiles={name:{other+'-fixture':dict(profile=other,files={p:addon.sha(d) for p,d in self.files[other].items()})}
            for name,other in [('camera','photos'),('photos','camera')]})
        historyfile=self.configroot/'configs/history.json';historyfile.write_text(json.dumps(history))
        for profile in self.profiles.values():profile['peer_history']['sha256']=addon.sha(historyfile.read_bytes())
        self.old={}
        self.old['photos']={p:d for p,d in self.files['photos'].items() if p not in self.assets and p not in co.SDK_PATHS and '/lib/arm64/' not in p}
        self.old['photos']['product/etc/realme-oem-addons/photos.json']=b'old photos receipt'
        self.old['camera']=dict(self.files['camera'])
        for p in co.SDK_PATHS:self.old['camera'].pop(p)
        self.old['camera']['system/framework/realme-camera-unit-sdk.jar']=b'old SDK'
        self.old['camera']['system/etc/permissions/realme-camera-unit-sdk.xml']=b'old registration'
        self.old['camera']['product/etc/realme-oem-addons/camera.json']=b'old camera receipt'
        self.archives={};self.prefixes={}
        # These synthetic files exercise storage transactions, not the real Gallery ABI.
        with patch.object(addon,'ROOT',self.configroot),patch.object(addon,'FILTER_ASSET_CONTRACT',self.contract),patch.object(addon,'validate_gallery_runtime'):
            for name,profile in self.profiles.items():
                upgrades={'fixture-old':{p:addon.sha(d) for p,d in self.old[name].items()}}
                for action in ['install','remove','check']:
                    data=addon.archive_bytes(profile,self.files[name],action,b'fixture cert',upgrades)
                    self.archives[name,action]=data
                    with zipfile.ZipFile(io.BytesIO(data)) as z:
                        self.prefixes[name,action]=z.read('META-INF/com/google/android/update-binary').decode().split('\nui "$TITLE:')[0]
        self.device=self.root/'device';self.device.mkdir()
        self.write_files(self.before)
        self.markers=['product/priv-app/GmsCore/base.apk','product/app/Aperture/base.apk','data/user/0/example/owner-marker',
            'system/lib64/libnativewindow.so','odm/lib64/libAlgoInterface.so','persist/calibration-marker',
            'dev/block/by-name/abl_a','data/system/locksettings.db']
        self.write_files({p:b'preserve other apps and owner data' for p in self.markers})

    def tearDown(self):self.temporary.cleanup()

    def write_files(self,files):
        for rel,data in files.items():
            path=self.device/rel;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)

    def run_installer(self,name,action='install',body='',failure=False):
        data=self.archives[name,action];archive=self.device/'package.zip';archive.write_bytes(data)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for rel in z.namelist():
                if rel.startswith(('payload/','policy/','upgrades/','peers/')) or rel=='manifest.sha256':
                    p=self.device/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(z.read(rel))
        (self.device/'journal').write_text('')
        setup='''
ui() { :; }
chown() { :; }
chcon() { :; }
sync() { :; }
WORK='''+shlex.quote(str(self.device))+'''
PRODUCT=$WORK/product
SYSTEM=$WORK/system
'''
        commands='''
prepare_policy
select_peer
select_upgrade
verify_legacy_sdk_state
verify_existing_files
verify_owned_tree
'''
        if action=='install':commands+='stage_upgrade_rollback\nWRITABLE=1\nremove_upgrade_stale\napply_files\n'
        if action=='remove':commands+='stage_remove_rollback\nWRITABLE=1\napply_files\nprune\n'
        p=subprocess.run(['sh','-c',self.prefixes[name,action]+setup+body+commands,'installer','3','1',str(archive)],capture_output=True,text=True)
        if not failure:self.assertEqual(p.returncode,0,p.stderr)
        self.assert_markers()
        return p

    def assert_markers(self):
        for p in self.markers:self.assertEqual((self.device/p).read_bytes(),b'preserve other apps and owner data')

    def assert_files(self,name):
        for p,data in self.files[name].items():self.assertEqual((self.device/p).read_bytes(),data,p)

    def test_both_install_orders_and_each_removal_order(self):
        # Fresh fixture per independent order prevents one sequence masking another.
        self.run_installer('camera');self.run_installer('photos');self.assert_files('camera');self.assert_files('photos')
        self.run_installer('camera','remove');self.assert_files('photos')
        for p in co.CAMERA_LIBRARIES:self.assertFalse((self.device/p).exists())
        self.run_installer('photos','remove')
        for p in co.SDK_PATHS|co.lut_paths():self.assertFalse((self.device/p).exists())
        self.run_installer('photos');self.run_installer('camera');self.assert_files('camera');self.assert_files('photos')
        self.run_installer('photos','remove');self.assert_files('camera')
        self.run_installer('camera','remove')
        for p in co.SDK_PATHS|co.lut_paths():self.assertFalse((self.device/p).exists())

    def test_legacy_camera_upgrade_relocates_sdk_and_removes_only_legacy_registration(self):
        self.write_files(self.old['camera'])
        self.run_installer('camera');self.assert_files('camera')
        for p in co.LEGACY_SDK_PATHS:self.assertFalse((self.device/p).exists())

    def test_gallery_upgrade_accepts_verified_peer_owned_runtime(self):
        self.run_installer('camera');self.write_files(self.old['photos'])
        self.run_installer('photos');self.assert_files('camera');self.assert_files('photos')

    def test_changed_partial_or_unknown_peer_refuses_before_any_apply(self):
        self.run_installer('camera')
        key='system/priv-app/RealmeStockCamera/base.apk';original=(self.device/key).read_bytes()
        (self.device/key).write_bytes(b'changed')
        p=self.run_installer('photos',failure=True);self.assertNotEqual(p.returncode,0)
        self.assertFalse((self.device/'product/app/RealmeStockPhotos/base.apk').exists())
        (self.device/key).write_bytes(original)
        extra=self.device/'system/priv-app/RealmeStockCamera/unowned.so';extra.write_bytes(b'unknown')
        p=self.run_installer('photos',failure=True);self.assertNotEqual(p.returncode,0)
        self.assertEqual(extra.read_bytes(),b'unknown')

    def test_legacy_runtime_or_orphans_cannot_be_claimed_as_a_new_peer(self):
        self.write_files({'system/framework/realme-camera-unit-sdk.jar':b'old runtime'})
        p=self.run_installer('photos',failure=True);self.assertNotEqual(p.returncode,0)
        self.assertFalse((self.device/'product/app/RealmeStockPhotos/base.apk').exists())

    def test_archive_peer_inspection_detects_changed_support(self):
        with patch.object(addon,'ROOT',self.configroot),patch.object(addon,'FILTER_ASSET_CONTRACT',self.contract),patch.object(addon,'validate_gallery_runtime'):
            p=self.root/'inspect.zip';p.write_bytes(self.archives['photos','install'])
            addon.inspect_archive(p,self.profiles['photos'])
            with zipfile.ZipFile(p) as z:members={n:z.read(n) for n in z.namelist()}
            members['peers/camera-fixture.sha256']+=b'x'
            with zipfile.ZipFile(p,'w') as z:
                for n,data in members.items():z.writestr(n,data)
            with self.assertRaises(ValueError):addon.inspect_archive(p,self.profiles['photos'])

    def test_failed_camera_install_restores_policy_without_touching_gallery_runtime(self):
        self.run_installer('photos')
        result=self.run_installer('camera',body='''
cp() {
  case "$2" in
    "$SYSTEM/priv-app/RealmeStockCamera/base.apk.realme-new") printf partial > "$2"; return 1 ;;
    *) command cp "$@" ;;
  esac
}
trap cleanup EXIT
''',failure=True)
        self.assertNotEqual(result.returncode,0)
        self.assert_files('photos')
        for p,data in self.before.items():self.assertEqual((self.device/p).read_bytes(),data)
        for p in self.files['camera']:
            if p not in self.files['photos'] and p not in self.before:self.assertFalse((self.device/p).exists(),p)
        self.assertFalse(list(self.device.rglob('*.realme-new')))

    def test_failed_legacy_upgrade_restores_old_sdk_registration_and_all_old_files(self):
        self.write_files(self.old['camera'])
        result=self.run_installer('camera',body='''
cp() {
  case "$2" in
    "$PRODUCT/framework/realme-camera-unit-sdk.jar.realme-new") printf partial > "$2"; return 1 ;;
    *) command cp "$@" ;;
  esac
}
trap cleanup EXIT
''',failure=True)
        self.assertNotEqual(result.returncode,0)
        for p,data in self.old['camera'].items():self.assertEqual((self.device/p).read_bytes(),data,p)
        for p in co.SDK_PATHS:self.assertFalse((self.device/p).exists())
        self.assertFalse(list(self.device.rglob('*.realme-new')))

    def test_failed_gallery_upgrade_preserves_camera_and_old_gallery(self):
        self.run_installer('camera');self.write_files(self.old['photos'])
        result=self.run_installer('photos',body='''
cp() {
  case "$2" in
    "$PRODUCT/app/RealmeStockPhotos/lib/arm64/libphotosfixture.so.realme-new") printf partial > "$2"; return 1 ;;
    *) command cp "$@" ;;
  esac
}
trap cleanup EXIT
''',failure=True)
        self.assertNotEqual(result.returncode,0);self.assert_files('camera')
        for p,data in self.old['photos'].items():self.assertEqual((self.device/p).read_bytes(),data,p)
        self.assertFalse(list(self.device.rglob('*.realme-new')))

    def test_failed_camera_removal_restores_camera_without_changing_gallery(self):
        self.run_installer('camera');self.run_installer('photos')
        result=self.run_installer('camera','remove',body='''
rm() {
  case "$1" in
    "$SYSTEM/priv-app/RealmeStockCamera/base.apk") return 1 ;;
    *) command rm "$@" ;;
  esac
}
trap cleanup EXIT
''',failure=True)
        self.assertNotEqual(result.returncode,0);self.assert_files('camera');self.assert_files('photos')

    def test_peer_symlink_missing_file_and_unknown_lut_refuse_without_cleanup(self):
        self.run_installer('camera')
        key='system/priv-app/RealmeStockCamera/base.apk';path=self.device/key;data=path.read_bytes()
        path.unlink();path.symlink_to(self.device/'product/app/Aperture/base.apk')
        self.assertNotEqual(self.run_installer('photos',failure=True).returncode,0)
        path.unlink();self.assertNotEqual(self.run_installer('photos',failure=True).returncode,0)
        path.write_bytes(data)
        unknown=self.device/'system/etc/realme-lut/unknown.dat';unknown.write_bytes(b'unowned')
        self.assertNotEqual(self.run_installer('photos',failure=True).returncode,0)
        self.assertEqual(unknown.read_bytes(),b'unowned')

    def test_gallery_native_inputs_mount_only_readonly_and_release_before_check(self):
        body='''
SLOT=_a
verify_logical_identity() { [ "$1" = odm ]; }
mount() {
  [ "$1:$2:$3:$4:$5" = '-t:erofs:-o:ro:/dev/block/mapper/odm_a' ] || exit 31
  mkdir -p "$6/lib64"
  command cp "$WORK/odm/lib64/libAlgoInterface.so" "$6/lib64/libAlgoInterface.so"
  printf '%s\\n' "$*" >> "$WORK/native-mounts"
}
umount() { printf '%s\\n' "$*" >> "$WORK/native-unmounts"; }
verify_policy_inputs
[ -z "$MOUNTED_EXTRAS" ]
'''
        self.run_installer('photos','check',body=body)
        self.assertEqual(len((self.device/'native-mounts').read_text().splitlines()),1)
        self.assertEqual(len((self.device/'native-unmounts').read_text().splitlines()),1)

    def test_gallery_native_hash_failure_releases_readonly_mount_without_app_writes(self):
        (self.device/'system/lib64/libnativewindow.so').write_bytes(b'changed core input')
        body='''
SLOT=_a
verify_logical_identity() { [ "$1" = odm ]; }
mount() {
  [ "$4" = ro ] || exit 31
  mkdir -p "$6/lib64"
  command cp "$WORK/odm/lib64/libAlgoInterface.so" "$6/lib64/libAlgoInterface.so"
}
umount() { printf '%s\\n' "$*" >> "$WORK/native-unmounts"; }
trap cleanup EXIT
verify_policy_inputs
'''
        # This sentinel is deliberately different for the negative input control.
        self.markers.remove('system/lib64/libnativewindow.so')
        p=self.run_installer('photos','check',body=body,failure=True)
        self.assertNotEqual(p.returncode,0)
        self.assertTrue((self.device/'native-unmounts').exists())
        self.assertFalse((self.device/'product/app/RealmeStockPhotos/base.apk').exists())
