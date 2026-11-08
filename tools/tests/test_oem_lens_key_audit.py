from pathlib import Path
import copy
import io
import sys
import unittest
from unittest.mock import patch
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import oem_lens_key_audit as audit


class NativeLensAuditTests(unittest.TestCase):
    def fixture(self, rows=None, duplicate=False):
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as z:
            z.writestr('classes.dex',b'pinned-dex')
            if duplicate:z.writestr('classes2.dex',b'duplicate')
        data=stream.getvalue()
        if rows is None:
            rows={name:{'superclass':spec['superclass'],'access':1,
                        'methods':dict.fromkeys(spec['methods'],1),'fields':dict.fromkeys(spec['fields'],1)}
                  for name,spec in audit.REQUIRED.items()}
        return data,rows

    def check(self,data,rows):
        with patch.object(audit,'APK_SHA',audit.sha(data)),patch.object(audit,'abi',return_value=rows):
            return audit.inspect_bytes(data)

    def test_exact_native_contract_and_dex_hash(self):
        data,rows=self.fixture();result=self.check(data,rows)
        self.assertEqual(result['native_abi'],rows)
        self.assertEqual(result['dex_sha256'],{'classes.dex':audit.sha(b'pinned-dex')})

    def test_wrong_apk_refused_before_parsing(self):
        with patch.object(audit,'abi') as reader:
            with self.assertRaisesRegex(ValueError,'unreviewed Camera'):audit.inspect_bytes(b'other-apk')
            reader.assert_not_called()

    def test_missing_duplicate_private_static_and_wrong_parent_refused(self):
        data,rows=self.fixture();name='Lrj/l;'
        variants=[]
        for change in ('missing','class-private','parent','method-private','field-static','field-missing'):
            candidate=copy.deepcopy(rows)
            if change=='missing':candidate.pop(name)
            elif change=='class-private':candidate[name]['access']=0
            elif change=='parent':candidate[name]['superclass']='Lother;'
            elif change=='method-private':candidate[name]['methods']['x()Ljava/lang/String;']=2
            elif change=='field-static':candidate[name]['fields']['d:Z']=9
            elif change=='field-missing':candidate[name]['fields'].pop('j:Lrj/l$c;')
            variants.append(candidate)
        for candidate in variants:
            with self.assertRaises(ValueError):self.check(data,candidate)
        duplicate,rows=self.fixture(duplicate=True)
        with self.assertRaisesRegex(ValueError,'duplicate'):self.check(duplicate,rows)


if __name__=='__main__':unittest.main()
