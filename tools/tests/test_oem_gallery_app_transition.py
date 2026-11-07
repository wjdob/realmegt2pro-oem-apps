"""Reject unreviewed APK/signer changes while recognizing the exact OEM alpha."""
from pathlib import Path
import copy
import json
import io
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'tools'))
import oem_addons as addon


class GalleryAppTransitionTests(unittest.TestCase):
    def setUp(self):
        scratch=ROOT/'tmp/oem-gallery-transition-tests';scratch.mkdir(parents=True,exist_ok=True)
        temporary=tempfile.TemporaryDirectory(dir=scratch);self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);(self.root/'configs').mkdir()
        mocked=patch.object(addon,'ROOT',self.root);mocked.start();self.addCleanup(mocked.stop)
        self.app='product/app/RealmeStockPhotos/base.apk';self.files={self.app:b'prepared app'}
        self.profile=dict(id='photos',package='com.coloros.gallery3d',directory='RealmeStockPhotos',
            hook='81-realme-photos.sh',
            sha256=addon.sha(self.files[self.app]),certificate_sha256='b'*64,
            app_partition='product',app_class='app',runtime_contract={'source':'configs/runtime.json'})
        source=dict(sha256='ce058185478efe7faed94ffbae42bc640741e33744963dcf1afd93f7f693c20e',
            certificate_sha256='64aafaf1d5bc9155a9e417a849e4f8eda1d0d1341667c28ed7c443c76f820b9a',min_sdk=31,target_sdk=34)
        self.contract=dict(schema=1,package=self.profile['package'],source_app=source,
            prepared_app=dict(sha256=self.profile['sha256'],certificate_sha256='b'*64,min_sdk=31,target_sdk=34),
            flavor='lineage',signer_role='dedicated-optional-gallery-app',predecessors={'alpha5':source})
        self.write_contract()
        self.history=dict(schema=2,package=self.profile['package'],certificate_sha256='b'*64,
            app_compatibility_contract_sha256=self.profile['app_compatibility']['sha256'],
            versions={'alpha5':{self.app:source['sha256']}})

    def write_contract(self):
        path=self.root/'configs/compat.json';path.write_text(json.dumps(self.contract))
        self.profile['app_compatibility']=dict(source='configs/compat.json',sha256=addon.sha(path.read_bytes()))

    def test_exact_bound_transition_preserves_original_profile_boundary(self):
        result=addon.checked_upgrade_history(self.profile,self.files,self.history)
        self.assertEqual(result,self.history['versions'])
        self.assertEqual(addon.checked_app_compatibility(self.profile),self.contract)

    def test_modified_apk_unknown_old_version_and_unowned_path_refused(self):
        for change in ('modified','version','unowned'):
            value=copy.deepcopy(self.history)
            if change=='modified':value['versions']['alpha5'][self.app]='c'*64
            if change=='version':value['versions']['unknown']=value['versions'].pop('alpha5')
            if change=='unowned':value['versions']['alpha5']['persist/calibration']='d'*64
            with self.subTest(change=change),self.assertRaises(ValueError):addon.checked_upgrade_history(self.profile,self.files,value)

    def test_wrong_target_certificate_schema_or_contract_binding_refused(self):
        for key,value in [('certificate_sha256','a'*64),('schema',1),('app_compatibility_contract_sha256','e'*64)]:
            history=dict(self.history);history[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):addon.checked_upgrade_history(self.profile,self.files,history)

    def test_target_and_source_sdk_or_identity_cannot_drift(self):
        for section,key,value in [('source_app','sha256','f'*64),('source_app','target_sdk',35),('prepared_app','min_sdk',30),('prepared_app','certificate_sha256','a'*64)]:
            original=copy.deepcopy(self.contract);self.contract[section][key]=value;self.write_contract()
            with self.subTest(section=section,key=key),self.assertRaises(ValueError):addon.checked_app_compatibility(self.profile)
            self.contract=original;self.write_contract()

    def test_camera_privileged_policy_or_other_placement_cannot_use_escape(self):
        for key,value in [('id','camera'),('app_partition','system'),('app_class','priv-app'),('policy',{'source':'x'}),('privileged_permissions',{'grant':['x']})]:
            profile=dict(self.profile);profile[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):addon.checked_app_compatibility(profile)

    def test_legacy_same_apk_rule_remains_and_schema2_requires_contract(self):
        profile=dict(self.profile);profile.pop('app_compatibility')
        history=dict(schema=1,package=profile['package'],certificate_sha256=profile['certificate_sha256'],versions={'old':{self.app:profile['sha256']}})
        self.assertEqual(addon.checked_upgrade_history(profile,self.files,history),history['versions'])
        with self.assertRaises(ValueError):addon.checked_upgrade_history(profile,self.files,self.history)

    def prepare_content_predecessor(self):
        cert=addon.oem_runtime_policy.GALLERY_CERT_SHA
        self.profile.update(certificate_sha256=cert,content_addressed_app=True,
            directory='RealmeStockPhotos-'+self.profile['sha256'],legacy_gallery_apk_sha256='c'*64)
        identity=dict(sha256='c'*64,certificate_sha256=cert,min_sdk=31,target_sdk=34)
        old_recipe=self.root/'configs/old-compat.json'
        old_recipe.write_text(json.dumps(dict(package=self.profile['package'],prepared_app=identity,signer_role='dedicated-optional-gallery-app')))
        old=dict(release='alpha7',sdk=36,lineage='23.2',model='RMX3301',profiles={'photos':dict(
            package=self.profile['package'],sha256='c'*64,certificate_sha256=cert,
            directory='RealmeStockPhotos-'+'c'*64,content_addressed_app=True,
            app_compatibility=dict(source='configs/old-compat.json',sha256=addon.sha(old_recipe.read_bytes())))})
        path=self.root/'configs/old-profile.json';path.write_text(json.dumps(old))
        self.contract.update(schema=2,prepared_app=dict(sha256=self.profile['sha256'],certificate_sha256=cert,min_sdk=31,target_sdk=34),
            predecessors={'alpha7':identity},predecessor_profiles={'alpha7':dict(source='configs/old-profile.json',sha256=addon.sha(path.read_bytes()))})
        self.write_contract();return path

    def test_content_addressed_update_binds_exact_previous_profile_and_app_recipe(self):
        self.prepare_content_predecessor()
        self.assertEqual(addon.checked_app_compatibility(self.profile),self.contract)
        self.assertEqual(addon.legacy_app_path(self.profile),'product/app/RealmeStockPhotos-'+'c'*64)
        with self.assertRaises(ValueError):addon.legacy_app_path(dict(self.profile,legacy_gallery_apk_sha256='d'*64))

    def test_changed_predecessor_profile_cannot_be_silently_inherited(self):
        path=self.prepare_content_predecessor();old=json.loads(path.read_text())
        old['profiles']['photos']['package']='another.app';path.write_text(json.dumps(old))
        with self.assertRaises(ValueError):addon.checked_app_compatibility(self.profile)
        self.contract['predecessor_profiles']['alpha7']['sha256']=addon.sha(path.read_bytes());self.write_contract()
        with self.assertRaises(ValueError):addon.checked_app_compatibility(self.profile)

    def test_predecessor_profile_cannot_escape_configuration_root(self):
        self.prepare_content_predecessor();self.contract['predecessor_profiles']['alpha7']['source']='../outside.json';self.write_contract()
        with self.assertRaises(ValueError):addon.checked_app_compatibility(self.profile)

    def prepare_multiple_predecessors(self):
        old_path = self.prepare_content_predecessor()
        previous = json.loads(old_path.read_text())
        previous['release'] = 'alpha8'
        app = previous['profiles']['photos']
        app.update(sha256='d'*64, directory='RealmeStockPhotos-'+'d'*64)
        identity = dict(sha256='d'*64, certificate_sha256=app['certificate_sha256'], min_sdk=31, target_sdk=34)
        recipe = self.root/'configs/second-compat.json'
        recipe.write_text(json.dumps(dict(package=self.profile['package'], prepared_app=identity,
                                         signer_role='dedicated-optional-gallery-app')))
        app['app_compatibility'] = dict(source='configs/second-compat.json', sha256=addon.sha(recipe.read_bytes()))
        profile = self.root/'configs/second-profile.json'
        profile.write_text(json.dumps(previous))
        self.contract['predecessors']['alpha8'] = identity
        self.contract['predecessor_profiles']['alpha8'] = dict(source='configs/second-profile.json', sha256=addon.sha(profile.read_bytes()))
        self.write_contract()
        self.files = {addon.app_path(self.profile)+'/base.apk': b'prepared app'}
        self.history.update(certificate_sha256=self.profile['certificate_sha256'],
                            app_compatibility_contract_sha256=self.profile['app_compatibility']['sha256'],
                            versions={version: {'product/app/RealmeStockPhotos-'+row['sha256']+'/base.apk': row['sha256']}
                                      for version,row in self.contract['predecessors'].items()})

    def test_multiple_hash_roots_come_only_from_bound_predecessor_profiles(self):
        self.prepare_multiple_predecessors()
        expected=['product/app/RealmeStockPhotos-'+'c'*64, 'product/app/RealmeStockPhotos-'+'d'*64]
        self.assertEqual(addon.legacy_app_paths(self.profile),expected)
        self.assertEqual(addon.checked_upgrade_history(self.profile,self.files,self.history),self.history['versions'])
        for root in expected:self.assertTrue(addon.allowed(self.profile,root+'/lib/arm64/library.so'))
        self.assertFalse(addon.allowed(self.profile,'product/app/RealmeStockPhotos-'+'e'*64+'/base.apk'))

    def test_an_old_manifest_cannot_mix_files_from_another_known_app_root(self):
        self.prepare_multiple_predecessors()
        self.history['versions']['alpha7']['product/app/RealmeStockPhotos-'+'d'*64+'/lib/arm64/stale.so']='e'*64
        with self.assertRaisesRegex(ValueError,'mixes application directories'):
            addon.checked_upgrade_history(self.profile,self.files,self.history)

    def test_runtime_inventory_checks_every_known_root_and_refuses_unknowns(self):
        self.prepare_multiple_predecessors()
        script=self.render_fixture('check')
        prefix=script.split('\nui "$TITLE:')[0]
        device=self.root/'device';device.mkdir()
        root='product/app/RealmeStockPhotos-'+'d'*64
        apk=device/root/'base.apk';apk.parent.mkdir(parents=True);apk.write_bytes(b'old app fixture')
        manifest=device/'selected.sha256';manifest.write_text('d'*64+'  '+root+'/base.apk\n')
        import shlex
        setup='\nWORK='+shlex.quote(str(device))+'\nPRODUCT=$WORK/product\nSYSTEM=$WORK/system\n'
        def check():
            return subprocess.run(['sh','-c',prefix+setup+'owned_tree_matches "$WORK/selected.sha256"',
                                   'installer','3','1','unused.zip'],capture_output=True,text=True)
        self.assertEqual(check().returncode,0)
        other=device/('product/app/RealmeStockPhotos-'+'c'*64)/'lib/arm64/unlisted.so'
        other.parent.mkdir(parents=True);other.write_bytes(b'not in selected manifest')
        self.assertNotEqual(check().returncode,0)
        other.unlink()
        unknown=device/('product/app/RealmeStockPhotos-'+'e'*64);unknown.mkdir()
        self.assertNotEqual(check().returncode,0)
        unknown.rmdir()
        # A dangling symlink at an otherwise known root must not be skipped.
        other.parent.rmdir();other.parent.parent.rmdir();other.parent.parent.parent.rmdir()
        (device/('product/app/RealmeStockPhotos-'+'c'*64)).symlink_to(device/'missing-target',target_is_directory=True)
        self.assertNotEqual(check().returncode,0)

    def test_empty_root_pruning_preserves_unrelated_files(self):
        self.prepare_multiple_predecessors()
        script=self.render_fixture('remove')
        prefix=script.split('\nui "$TITLE:')[0]
        device=self.root/'device';device.mkdir()
        roots=[addon.app_path(self.profile),*addon.legacy_app_paths(self.profile)]
        for root in roots:(device/root/'lib/arm64').mkdir(parents=True)
        marker=device/'product/app/Unrelated/base.apk';marker.parent.mkdir();marker.write_bytes(b'preserve')
        import shlex
        setup='\nWORK='+shlex.quote(str(device))+'\nPRODUCT=$WORK/product\nSYSTEM=$WORK/system\n'
        result=subprocess.run(['sh','-c',prefix+setup+'prune','installer','3','1','unused.zip'],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue(all(not (device/root).exists() for root in roots))
        self.assertEqual(marker.read_bytes(),b'preserve')

    def render_fixture(self, action):
        self.profile['title']='Synthetic Gallery fixture'
        with patch.object(addon,'validate_gallery_runtime'):
            archive=addon.archive_bytes(self.profile,self.files,action,b'fixture certificate')
        with zipfile.ZipFile(io.BytesIO(archive)) as z:
            return z.read('META-INF/com/google/android/update-binary').decode()


if __name__=='__main__':unittest.main()
