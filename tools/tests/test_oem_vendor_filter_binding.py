"""Optional app packages must reject the earlier incomplete native baseline."""
import copy
import json
import unittest
import oem_addons as a


class VendorFilterBindingTests(unittest.TestCase):
    def setUp(self):
        cfg=json.loads((a.ROOT/'configs/oem-addons-camera12e-23.2.json').read_text())
        self.p=dict(cfg['profiles']['camera'],id='camera')
        c=json.loads(a.VENDOR_FILTER_CONTRACT.read_text())
        self.p['native_requirements']['odm/lib64/libAlgoInterface.so']=c['aps']['sha256']
        self.p['native_requirements'][a.VENDOR_FILTER_PATH]=c['provider']['sha256']
        self.p['vendor_filter_contract']=dict(source=str(a.VENDOR_FILTER_CONTRACT.relative_to(a.ROOT)),sha256=a.sha(a.VENDOR_FILTER_CONTRACT.read_bytes()))

    def test_exact_vendor_provider_binding_passes(self):
        self.assertEqual(len(a.native_requirements(self.p).splitlines()),3)

    def test_provider_cannot_be_silently_omitted(self):
        del self.p['native_requirements'][a.VENDOR_FILTER_PATH]
        with self.assertRaisesRegex(ValueError,'provider/APS binding'):
            a.native_requirements(self.p)

    def test_earlier_soname_workaround_does_not_qualify(self):
        self.p['native_requirements']['odm/lib64/libAlgoInterface.so']='359f28812fa2b3d0c93f7d4de6c696913259eb6b379aa12bd29a5da24437f017'
        with self.assertRaisesRegex(ValueError,'provider/APS binding'):
            a.native_requirements(self.p)

    def test_unknown_extra_native_binding_remains_denied(self):
        self.p['native_requirements']['odm/lib64/arbitrary.so']='a'*64
        with self.assertRaisesRegex(ValueError,'unreviewed Camera native'):
            a.native_requirements(self.p)

    def test_undeclared_third_path_rejected(self):
        self.p.pop('vendor_filter_contract')
        with self.assertRaisesRegex(ValueError,'unreviewed Camera native'):
            a.native_requirements(self.p)

    def test_modified_contract_pin_rejected(self):
        self.p['vendor_filter_contract']['sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'unreviewed vendor filter'):
            a.native_requirements(self.p)
