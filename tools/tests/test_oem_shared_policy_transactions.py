"""Execute shared-policy install/remove/upgrade and failure paths on host files."""
import copy
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import zipfile

import oem_addons as addon
import oem_coinstall as co
import oem_runtime_policy as policy
import test_oem_coinstall_transactions as transactions


class SharedPolicyTransactionTests(unittest.TestCase):
    def fixture(self):
        f=transactions.CoInstallTransactionTests();f.setUp();self.addCleanup(f.tearDown)
        old={name:dict(files) for name,files in f.files.items()}
        before=copy.deepcopy(f.before)
        after={p:data+b' plus Gallery domain' for p,data in f.files['camera'].items()
               if p in policy.PATCH_PATHS}
        support={policy.STORAGE+'preimages/'+Path(p).name:before[p] for p in policy.PATCH_PATHS}
        support.update({policy.STORAGE+'postimages/'+Path(p).name:after[p] for p in policy.PATCH_PATHS})
        support[policy.STORAGE+'inputs.sha256']=b''
        support[policy.STORAGE+'patches.tsv']=''.join(addon.sha(after[p])+' '+addon.sha(before[p])+' '+p+'\n'
                                                     for p in sorted(policy.PATCH_PATHS)).encode()
        for name,p in f.profiles.items():
            p['policy']=dict(kind=policy.KIND,sha256='0'*64)
            p['shared_paths']+=sorted(policy.shared_paths())
            f.files[name].update(after);f.files[name].update(support)
            f.files[name]['product/etc/realme-oem-addons/'+name+'.json']=json.dumps(
                {'id':name,'fixture_generation':'new'},sort_keys=True).encode()
        gal=f.profiles['photos'];gal['certificate_sha256']=policy.GALLERY_CERT_SHA
        gal['runtime_contract']={'source':'configs/unused-runtime.json'}
        gal['native_requirements'][policy.EDITOR_BACKEND_PATH]=policy.EDITOR_BACKEND_SHA
        gal['content_addressed_app']=True;gal['directory']='RealmeStockPhotos-'+gal['sha256']
        oldroot='product/app/RealmeStockPhotos';newroot=addon.app_path(gal)
        f.files['photos']={newroot+p[len(oldroot):] if p.startswith(oldroot+'/') else p:d
                           for p,d in f.files['photos'].items()}
        source=dict(sha256='ce058185478efe7faed94ffbae42bc640741e33744963dcf1afd93f7f693c20e',
                    certificate_sha256='64aafaf1d5bc9155a9e417a849e4f8eda1d0d1341667c28ed7c443c76f820b9a',min_sdk=31,target_sdk=34)
        prepared=dict(sha256=gal['sha256'],certificate_sha256=gal['certificate_sha256'],min_sdk=31,target_sdk=34)
        contract=dict(schema=1,package=gal['package'],source_app=source,prepared_app=prepared,
                      flavor='lineage',signer_role='dedicated-optional-gallery-app',
                      predecessors={'fixture-old':prepared},policyless_predecessors=['fixture-old'])
        path=f.configroot/'configs/compat.json';path.write_text(json.dumps(contract))
        gal['app_compatibility']=dict(source='configs/compat.json',sha256=addon.sha(path.read_bytes()))
        hashes=lambda files:{p:addon.sha(d) for p,d in files.items()}
        history=dict(schema=1,profiles={
            'camera':{'photos-old':dict(profile='photos',files=hashes(old['photos'])),
                      'photos-new':dict(profile='photos',files=hashes(f.files['photos']))},
            'photos':{'camera-new':dict(profile='camera',files=hashes(f.files['camera']))}})
        path=f.configroot/'configs/history.json';path.write_text(json.dumps(history))
        for p in f.profiles.values():p['peer_history']['sha256']=addon.sha(path.read_bytes())
        f.old=old
        with patch.object(addon,'ROOT',f.configroot),patch.object(addon,'FILTER_ASSET_CONTRACT',f.contract),patch.object(addon,'validate_gallery_runtime'):
            for name,p in f.profiles.items():
                for action in ['install','remove','check']:
                    data=addon.archive_bytes(p,f.files[name],action,b'fixture cert',{'fixture-old':hashes(old[name])})
                    f.archives[name,action]=data
                    with zipfile.ZipFile(io.BytesIO(data)) as z:
                        f.prefixes[name,action]=z.read('META-INF/com/google/android/update-binary').decode().split('\nui "$TITLE:')[0]
        return f

    def test_camera_then_gallery_remove_camera_first_retains_policy(self):
        f=self.fixture();f.run_installer('camera');f.run_installer('photos')
        f.assert_files('camera');f.assert_files('photos')
        f.run_installer('camera','remove');f.assert_files('photos')
        f.run_installer('photos','remove')
        for p,data in f.before.items():self.assertEqual((f.device/p).read_bytes(),data)

    def test_gallery_then_camera_remove_gallery_first_retains_policy(self):
        f=self.fixture();f.run_installer('photos');f.run_installer('camera')
        f.assert_files('camera');f.assert_files('photos')
        f.run_installer('photos','remove');f.assert_files('camera')
        f.run_installer('camera','remove')
        for p,data in f.before.items():self.assertEqual((f.device/p).read_bytes(),data)

    def test_policyless_gallery_upgrade_moves_only_the_recognized_tree(self):
        f=self.fixture();f.write_files(f.old['photos']);f.run_installer('camera')
        f.run_installer('photos');f.assert_files('camera');f.assert_files('photos')
        self.assertFalse((f.device/'product/app/RealmeStockPhotos/base.apk').exists())

    def test_failed_policyless_upgrade_restores_existing_baseline_policy(self):
        f=self.fixture();f.write_files(f.old['photos'])
        result=f.run_installer('photos',body='''
trap cleanup EXIT
mv() { case "$2" in */product_sepolicy.cil) return 1 ;; *) command mv "$@" ;; esac; }
''',failure=True)
        self.assertNotEqual(result.returncode,0)
        for p,data in f.before.items():self.assertEqual((f.device/p).read_bytes(),data)
        for p,data in f.old['photos'].items():self.assertEqual((f.device/p).read_bytes(),data)

    def test_failed_app_move_preserves_the_peer_policy_and_original_gallery(self):
        f=self.fixture();f.write_files(f.old['photos']);f.run_installer('camera')
        result=f.run_installer('photos',body='''
trap cleanup EXIT
mv() { case "$2" in */RealmeStockPhotos-*/base.apk) return 1 ;; *) command mv "$@" ;; esac; }
''',failure=True)
        self.assertNotEqual(result.returncode,0);f.assert_files('camera')
        for p,data in f.old['photos'].items():self.assertEqual((f.device/p).read_bytes(),data)

    def test_unknown_content_directory_is_refused_before_install(self):
        f=self.fixture();p=f.device/('product/app/RealmeStockPhotos-'+'f'*64+'/base.apk')
        p.parent.mkdir(parents=True);p.write_bytes(b'unknown app')
        result=f.run_installer('camera',body='ui() { printf "%s\\n" "$*" >&2; }\n',failure=True);self.assertNotEqual(result.returncode,0)
        self.assertIn('Unreviewed peer placement',result.stderr)
        self.assertEqual(p.read_bytes(),b'unknown app')
        for path,data in f.before.items():self.assertEqual((f.device/path).read_bytes(),data)

    def test_archive_inspection_distinguishes_signer_xml_from_sdk_registration(self):
        f=self.fixture()
        with patch.object(addon,'ROOT',f.configroot),patch.object(addon,'FILTER_ASSET_CONTRACT',f.contract),patch.object(addon,'validate_gallery_runtime'):
            for name,p in f.profiles.items():
                result=addon.inspect_archive(io.BytesIO(f.archives[name,'install']),p)
                self.assertTrue(result['payload_present'])
                self.assertEqual(result['owned_files'],len(f.files[name]))


if __name__=='__main__':unittest.main()
