"""Guard the real audited Gallery recipe against omitted SDK/native dependencies."""
import copy
import json
import unittest

import oem_addons as addon


class GalleryRuntimeTests(unittest.TestCase):
    config_name = 'oem-addons-gallery-alpha2-23.2.json'

    def setUp(self):
        cfg=json.loads((addon.ROOT/'configs'/self.config_name).read_text())
        self.profile=dict(cfg['profiles']['photos'],id='photos',shared_paths=[cfg['shared_sdk']['jar_path'],cfg['shared_sdk']['xml_path']])
        peers=addon.load_peer_history(dict(id='camera',peer_history=self.profile['peer_history']))
        self.manifest=next(iter(peers.values()))['files']

    def test_audited_complete_recipe_passes_without_privilege_or_policy_escalation(self):
        addon.validate_gallery_runtime(self.profile,self.manifest)
        self.assertEqual(len(self.profile['extra_native']),6)
        self.assertEqual(len(addon.camera_assets(self.profile)),71)
        self.assertNotIn('policy',self.profile)
        self.assertNotIn('privileged_permissions',self.profile)

    def test_every_sdk_native_and_apk_dependency_is_required(self):
        paths=['product/app/RealmeStockPhotos/base.apk','product/framework/realme-camera-unit-sdk.jar',
            'product/etc/permissions/realme-camera-unit-sdk.xml']
        paths += ['product/app/RealmeStockPhotos/lib/arm64/'+s['name'] for s in self.profile['extra_native']]
        paths += [next(iter(addon.camera_assets(self.profile)))]
        for path in paths:
            with self.subTest(path=path):
                manifest=dict(self.manifest);manifest.pop(path)
                with self.assertRaises(ValueError):addon.validate_gallery_runtime(self.profile,manifest)
                manifest=dict(self.manifest);manifest[path]='f'*64
                with self.assertRaises(ValueError):addon.validate_gallery_runtime(self.profile,manifest)

    def test_contract_and_dependency_declarations_cannot_be_silently_dropped(self):
        for key in ['runtime_contract','extra_native','native_requirements','platform_framework_sha256',
                    'explicit_reinstall_after_ota','shared_paths']:
            with self.subTest(key=key):
                profile=copy.deepcopy(self.profile);profile.pop(key)
                with self.assertRaises(ValueError):addon.validate_gallery_runtime(profile,self.manifest)

    def test_regular_product_app_scope_is_part_of_the_contract(self):
        for key,value in [('app_partition','system'),('app_class','priv-app'),('policy',{'sha256':'f'*64}),
                          ('privileged_permissions',{'grant':['android.permission.WRITE_SECURE_SETTINGS']})]:
            profile=copy.deepcopy(self.profile);profile[key]=value
            with self.assertRaises(ValueError):addon.validate_gallery_runtime(profile,self.manifest)

    def test_unknown_runtime_contract_digest_refuses(self):
        profile=copy.deepcopy(self.profile);profile['runtime_contract']['sha256']='f'*64
        with self.assertRaises(ValueError):addon.validate_gallery_runtime(profile,self.manifest)


class GalleryAlpha3RuntimeTests(GalleryRuntimeTests):
    config_name = 'oem-addons-gallery-alpha3-23.2.json'
