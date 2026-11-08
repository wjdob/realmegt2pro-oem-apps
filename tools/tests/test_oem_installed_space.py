"""Regression for full-APK space reservation during exact installed recognition."""
from pathlib import Path
import hashlib
import re
import shlex
import subprocess
import tempfile
import unittest
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import oem_installed_space

SOURCE=Path(__file__).resolve().parents[1]/'oem_addons/update-binary.sh'
PROFILE={'id':'camera','platform':{'sdk':37,'lineage':'24.0'},'installed_space_budget':True}


class InstalledSpaceTests(unittest.TestCase):
    def scenario(self,action='check',present='exact',already=0,system=240000,product=300000,owned=True):
        text=oem_installed_space.prepare(SOURCE.read_bytes(),PROFILE).decode()
        functions='\n'.join(re.search(r'(?m)^'+name+r'\(\) \{\n.*?^\}',text,re.S).group() for name in ['known_install_matches','select_space_budget'])
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'system').mkdir();(root/'system/base.apk').write_bytes(b'exact apk')
            (root/'system/native.so').write_bytes(b'exact native')
            rows={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in ['system/base.apk','system/native.so']}
            (root/'manifest.sha256').write_text(''.join(h+'  '+n+'\n' for n,h in rows.items()))
            if present=='partial':(root/'system/native.so').unlink()
            if present=='unknown':(root/'system/base.apk').write_bytes(b'unreviewed')
            if present=='absent':
                for name in rows:(root/name).unlink()
            script=functions+f'''
WORK={shlex.quote(str(root))}
ACTION={action}; ALREADY_INSTALLED={already}; SYSTEM_KIB={system}; PRODUCT_KIB={product}; FULL_PAYLOAD_KIB=540000
target() {{ printf '%s/%s\n' "$WORK" "$1"; }}
no_links() {{ [ ! -L "$1" ]; }}
owned_tree_matches() {{ {'true' if owned else 'false'}; }}
manifest_hash() {{ awk -v p="$2" '$2==p{{print $1;found=1}}END{{if(!found)exit 1}}' "$1"; }}
peer_owns() {{ return 1; }}
select_space_budget
printf '%s %s %s\n' "$SYSTEM_KIB" "$PRODUCT_KIB" "$FULL_PAYLOAD_KIB"
'''
            result=subprocess.run(['sh','-s'],input=script,text=True,capture_output=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            return tuple(map(int,result.stdout.split()))

    def test_exact_check_uses_margin_without_changing_full_ram_budget(self):
        self.assertEqual(self.scenario(),(4096,16384,540000))

    def test_fresh_partial_and_modified_check_keep_full_budget(self):
        for present in ['absent','partial','unknown']:
            with self.subTest(present=present):self.assertEqual(self.scenario(present=present),(240000,300000,540000))

    def test_unknown_owned_tree_never_reduces_budget(self):
        self.assertEqual(self.scenario(owned=False),(240000,300000,540000))

    def test_fresh_install_and_remove_do_not_gain_exact_check_exception(self):
        for action in ['install','remove']:
            self.assertEqual(self.scenario(action=action),(240000,300000,540000))

    def test_proven_idempotent_install_uses_margin(self):
        self.assertEqual(self.scenario(action='install',already=1),(4096,16384,540000))

    def test_predecessor_delta_budget_remains_intact(self):
        self.assertEqual(self.scenario(present='unknown',system=8192,product=20480),(8192,20480,540000))

    def test_budget_selection_precedes_capacity_checks_after_identity_validation(self):
        main=oem_installed_space.prepare(SOURCE.read_bytes(),PROFILE).decode().split('ui "$TITLE:')[1]
        self.assertLess(main.index('verify_owned_tree\n'),main.index('select_space_budget\n'))
        self.assertLess(main.index('verify_policy_inputs\n'),main.index('select_space_budget\n'))
        self.assertLess(main.index('select_space_budget\n'),main.index('free_product=$(free_kib'))

    def test_prior_recipes_are_byte_exact_and_other_platforms_refuse_extension(self):
        original=SOURCE.read_bytes()
        for profile in [{'id':'camera'},{'id':'photos','platform':{'sdk':37,'lineage':'24.0'}}]:
            self.assertEqual(oem_installed_space.prepare(original,profile),original)
        for profile in [dict(PROFILE,platform={'sdk':36,'lineage':'23.2'}),dict(PROFILE,installed_space_budget=False),dict(PROFILE,id='clock')]:
            with self.assertRaises(ValueError):oem_installed_space.prepare(original,profile)
        with self.assertRaises(ValueError):oem_installed_space.prepare(original+b'\n',PROFILE)


if __name__=='__main__':unittest.main()
