"""Policy transaction composition and content-bound app layout boundaries."""
from pathlib import Path
import copy
import json
import tempfile
import sys
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import oem_addons as addon
import oem_coinstall as co
import oem_policy as legacy
import oem_runtime_policy as policy


class SharedPolicyTests(unittest.TestCase):
    def setUp(self):
        self.cam=b'fixture camera cert';self.gal=b'fixture gallery cert'
        self.patches=[patch.object(policy,'CAMERA_CERT_SHA',policy.sha(self.cam)),
                      patch.object(policy,'GALLERY_CERT_SHA',policy.sha(self.gal))]
        for p in self.patches:p.start();self.addCleanup(p.stop)

    def test_signers_select_only_the_exact_two_packages(self):
        root=ET.fromstring(policy.certificate_map(self.cam,self.gal))
        self.assertEqual(len(root),2)
        self.assertEqual([r[0].attrib for r in root],
            [{'name':'com.oplus.camera'},{'name':'com.coloros.gallery3d'}])
        self.assertTrue(all(len(r)==1 and len(r[0])==1 and r[0][0].tag=='seinfo' for r in root))
        for cam,gal in [(self.cam+b'x',self.gal),(self.cam,self.gal+b'x')]:
            with self.assertRaises(ValueError):policy.certificate_map(cam,gal)

    def test_compile_proof_rejects_changed_body_missing_inputs_or_neverallow_bypass(self):
        config=json.loads(legacy.CONFIG.read_text());body=b'candidate'
        args=['shell','/system/bin/secilc']+['/'+p for p in config['compiler_inputs']
                    if p!='product/etc/selinux/product_sepolicy.cil']
        good=dict(compile_passed=True,live_policy_unchanged=True,
                  candidate_sha256=policy.sha(body),commands=[dict(args=args,code=0)],input_hashes=config['inputs'])
        policy.compile_proof(good,body)
        for kind in ['bypass','input','changed','live','failed','context']:
            value=copy.deepcopy(good)
            if kind=='bypass':value['commands'][0]['args'].append('-N')
            if kind=='input':value['commands'][0]['args'].pop()
            if kind=='changed':value['candidate_sha256']='0'*64
            if kind=='live':value['live_policy_unchanged']=False
            if kind=='failed':value['commands'][0]['code']=255
            if kind=='context':value['input_hashes']['vendor/etc/selinux/vendor_sepolicy.cil']='f'*64
            with self.subTest(kind=kind),self.assertRaises(ValueError):policy.compile_proof(value,body)

    def bundle_fixture(self):
        parent=policy.ROOT/'tmp/oem-shared-policy-unit';parent.mkdir(parents=True,exist_ok=True)
        tmp=tempfile.TemporaryDirectory(dir=parent);self.addCleanup(tmp.cleanup);root=Path(tmp.name)
        config=json.loads(legacy.CONFIG.read_text());pre={p:('base '+p).encode() for p in policy.PATCH_PATHS}
        config['inputs'].update({p:policy.sha(v) for p,v in pre.items()})
        mapping=b'pinned mapping';config['inputs']['product/etc/selinux/mapping/202504.cil']=policy.sha(mapping)
        cp=root/'config.json';cp.write_text(json.dumps(config));mock=patch.object(legacy,'CONFIG',cp);mock.start();self.addCleanup(mock.stop)
        desired=policy.desired_files(pre,self.cam,self.gal,mapping)
        old={policy.STORAGE+'preimages/'+Path(p).name:v for p,v in pre.items()}
        old[policy.STORAGE+'inputs.sha256']=''.join(config['inputs'][p]+'  '+p+'\n' for p in config['compiler_inputs']).encode()
        old['product/etc/selinux/product_mac_permissions.xml']=ET.tostring(ET.fromstring(policy.certificate_map(self.cam,self.gal)))
        proof=dict(compile_passed=True,live_policy_unchanged=True,
            candidate_sha256=policy.sha(desired['product/etc/selinux/product_sepolicy.cil']),
            input_hashes=config['inputs'],commands=[dict(code=0,args=['shell','/system/bin/secilc']+
                ['/'+p for p in config['compiler_inputs'] if p!='product/etc/selinux/product_sepolicy.cil'])])
        output=root/'bundle'
        with patch.object(legacy,'load_bundle',return_value=({'fragment_sha256':config['fragment_sha256']},old)):
            policy.prepare(root/'legacy','a'*64,self.gal,mapping,proof,output)
        return output

    def test_bundle_reconstruction_preserves_exact_preimages_and_two_domains(self):
        out=self.bundle_fixture();m,files=policy.load_bundle(out,policy.sha((out/'manifest.json').read_bytes()))
        self.assertEqual(set(files),policy.shared_paths())
        self.assertIn(b'realme_gallery_app',files['product/etc/selinux/product_sepolicy.cil'])
        self.assertIn(b'realme_camera_app',files['product/etc/selinux/product_sepolicy.cil'])

    def test_rehashed_payload_cannot_add_a_global_app_mapping(self):
        out=self.bundle_fixture();relative=policy.STORAGE+'postimages/product_seapp_contexts';path=out/relative
        path.write_bytes(path.read_bytes()+b'\nuser=_app domain=realme_gallery_app\n')
        m=json.loads((out/'manifest.json').read_text());m['files'][relative]=policy.sha(path.read_bytes())
        (out/'manifest.json').write_text(json.dumps(m))
        with self.assertRaises(ValueError):policy.load_bundle(out,policy.sha((out/'manifest.json').read_bytes()))

    def test_complete_shared_policy_can_coexist_but_partial_or_different_cannot(self):
        own={co.APP_ROOTS['camera']+'/base.apk':'a'*64,co.HOOKS['camera']:'a'*64,
             'product/etc/realme-oem-addons/camera.json':'a'*64,
             'product/etc/sysconfig/realme-camera.xml':'a'*64}
        other={co.APP_ROOTS['photos']+'/base.apk':'b'*64,co.HOOKS['photos']:'b'*64,
               'product/etc/realme-oem-addons/photos.json':'b'*64,
               'product/etc/sysconfig/realme-photos.xml':'b'*64}
        for p in co.SDK_PATHS|co.lut_paths()|policy.shared_paths():own[p]=other[p]='c'*64
        entry={'photos-fixture':dict(profile='photos',files=other)}
        result=co.validate('camera',own,entry)
        self.assertEqual(set(result['photos-fixture']['shared']),co.shareable_paths())
        for mode in ['different','partial']:
            peers=copy.deepcopy(entry);key=next(iter(policy.shared_paths()))
            if mode=='different':peers['photos-fixture']['files'][key]='d'*64
            else:peers['photos-fixture']['files'].pop(key)
            with self.subTest(mode=mode),self.assertRaises(ValueError):co.validate('camera',own,peers)


class ContentAddressedLayoutTests(unittest.TestCase):
    def test_directory_tracks_actual_apk_and_cannot_cover_another_package(self):
        digest='a'*64;root='product/app/RealmeStockPhotos-'+digest
        self.assertEqual(co.app_root('photos',{root+'/base.apk':digest}),root)
        with self.assertRaises(ValueError):co.app_root('photos',{root+'/base.apk':'b'*64})
        with self.assertRaises(ValueError):co.app_root('camera',{root+'/base.apk':digest})
        with self.assertRaises(ValueError):co.app_root('photos',{root+'/base.apk':digest,co.APP_ROOTS['photos']+'/base.apk':digest})

    def test_profile_relocation_is_gallery_only_and_legacy_root_is_fixed(self):
        profile=dict(id='photos',package='com.coloros.gallery3d',sha256='a'*64,
            directory='RealmeStockPhotos-'+'a'*64,content_addressed_app=True)
        self.assertEqual(addon.app_path(profile),'product/app/'+profile['directory'])
        self.assertEqual(addon.legacy_app_path(profile),'product/app/RealmeStockPhotos')
        for key,value in [('id','camera'),('directory','OtherApp'),('app_partition','system'),('app_class','priv-app')]:
            with self.subTest(key=key),self.assertRaises(ValueError):addon.app_path(dict(profile,**{key:value}))


if __name__=='__main__':unittest.main()
