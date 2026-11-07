"""Exact shared runtime ownership; no app, policy or native-provider sharing."""
import unittest
import oem_coinstall as peer


class OemCoInstallTests(unittest.TestCase):
    def setUp(self):
        self.shared={p:'a'*64 for p in peer.SDK_PATHS|peer.lut_paths()}
        self.camera=dict(self.shared)
        self.photos=dict(self.shared)
        for name,files in [('camera',self.camera),('photos',self.photos)]:
            files.update({peer.APP_ROOTS[name]+'/base.apk':'b'*64,peer.HOOKS[name]:'c'*64,
                'product/etc/realme-oem-addons/'+name+'.json':'d'*64,
                'product/etc/sysconfig/realme-'+name+'.xml':'e'*64})

    def test_both_install_orders_have_exact_symmetric_shared_ownership(self):
        camera=peer.validate('camera',self.camera,{'photos-alpha2':dict(profile='photos',files=self.photos)})
        photos=peer.validate('photos',self.photos,{'camera-beta12d':dict(profile='camera',files=self.camera)})
        self.assertEqual(camera['photos-alpha2']['shared'],photos['camera-beta12d']['shared'])
        self.assertEqual(len(camera['photos-alpha2']['shared']),73)

    def test_policy_native_provider_and_foreign_paths_cannot_be_shared(self):
        for path in ['system/lib64/libnativewindow.so','data/local/tmp/file',
                     'product/etc/selinux/product_sepolicy.cil',
                     'system/etc/permissions/privapp-permissions-realme-camera.xml',
                     'system/lib64/libHeifEncoderWrapper.so']:
            with self.subTest(path=path):
                photos=dict(self.photos,**{path:'f'*64})
                with self.assertRaises(ValueError):
                    peer.validate('camera',dict(self.camera,**{path:'f'*64}),{'photos-alpha2':dict(profile='photos',files=photos)})

    def test_wrong_hash_partial_or_legacy_runtime_refuses(self):
        for mutate in [lambda d:d.pop(next(iter(peer.SDK_PATHS))),
                       lambda d:d.pop(next(iter(peer.lut_paths()))),
                       lambda d:d.update({next(iter(peer.SDK_PATHS)):'f'*64}),
                       lambda d:d.update({next(iter(peer.LEGACY_SDK_PATHS)):'f'*64})]:
            files=dict(self.photos);mutate(files)
            with self.assertRaises(ValueError):peer.validate('camera',self.camera,{'photos-alpha2':dict(profile='photos',files=files)})

    def test_peer_identity_app_receipt_and_paths_are_required(self):
        for missing in [peer.APP_ROOTS['photos']+'/base.apk',peer.HOOKS['photos'],
                        'product/etc/realme-oem-addons/photos.json']:
            files=dict(self.photos);files.pop(missing)
            with self.assertRaises(ValueError):peer.validate('camera',self.camera,{'photos-alpha2':dict(profile='photos',files=files)})
        for key in ['camera-alpha2','../photos-alpha2','photos-']:
            with self.assertRaises(ValueError):peer.validate('camera',self.camera,{key:dict(profile='photos',files=self.photos)})
        with self.assertRaises(ValueError):peer.validate('photos',self.photos,{'camera-beta12d':dict(profile='photos',files=self.camera)})

    def test_owner_manifest_is_validated_and_peer_count_bounded(self):
        with self.assertRaises(ValueError):peer.validate('camera',dict(self.camera,**{'data/credentials':'a'*64}),{})
        with self.assertRaises(ValueError):peer.validate('photos',dict(self.photos,**{next(iter(peer.SDK_PATHS)):'bad'}),{})
        with self.assertRaises(ValueError):peer.validate('camera',self.camera,
            {'photos-alpha'+str(i):dict(profile='photos',files=self.photos) for i in range(9)})

    def test_support_roundtrip_and_tamper_rejection(self):
        history={'photos-alpha2':dict(profile='photos',files=self.photos)}
        members=peer.support_members('camera',self.camera,history)
        self.assertEqual(peer.inspect_members('camera',self.camera,members),peer.validate('camera',self.camera,history))
        changes=[]
        altered=dict(members);altered['peers/photos-alpha2.sha256']+=b'x';changes.append(altered)
        altered=dict(members);altered.pop('peers/photos-alpha2.sha256');changes.append(altered)
        altered=dict(members);altered['peers/unknown.sha256']=b'ignored';changes.append(altered)
        altered=dict(members);altered['peers/index.sha256']*=2;changes.append(altered)
        altered=dict(members);altered['peers/index.sha256']=b'';changes.append(altered)
        for changed in changes:
            with self.subTest(members=list(changed)):
                with self.assertRaises(ValueError):peer.inspect_members('camera',self.camera,changed)

    def test_rehashed_unsafe_manifest_still_refuses(self):
        import hashlib
        members=peer.support_members('camera',self.camera,{'photos-alpha2':dict(profile='photos',files=self.photos)})
        path='peers/photos-alpha2.sha256'
        for extra in [b'a'*64+b'  data/credential\n',members[path].splitlines(keepends=True)[0]]:
            changed=dict(members);changed[path]+=extra
            changed['peers/index.sha256']=(hashlib.sha256(changed[path]).hexdigest()+'  photos-alpha2\n').encode()
            with self.assertRaises(ValueError):peer.inspect_members('camera',self.camera,changed)
