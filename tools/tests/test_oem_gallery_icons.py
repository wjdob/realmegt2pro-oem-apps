from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from oem_gallery_icons import patch_icon_helper,HELPER

class ResolverPatchTests(unittest.TestCase):
    def specimen(self):
        return ('prefix\n.method public a(Landroid/content/Intent;Landroid/content/pm/ResolveInfo;Ljava/lang/String;)Lcom/oplus/aiunit/vision/ht;\n'
                '    .registers 15\n    # OEM resolver call remains here\n'
                '    if-nez v1, :cond_22\n\n    return-object v2\n'
                '    :cond_22\n    # Original OEM non-null path\n.end method\nsuffix')

    def test_only_absent_oem_item_uses_standard_icon_and_retains_choice_identity(self):
        old=self.specimen();new=patch_icon_helper(old)
        self.assertEqual(old.split('    if-nez v1, :cond_22')[0],new.split('    if-nez v1, :cond_22')[0])
        self.assertEqual(old.split('    :cond_22')[1],new.split('    :cond_22')[1])
        self.assertIn(HELPER+'->icon',new);self.assertIn(HELPER+'->label',new)
        self.assertIn('move-object v4, p3',new);self.assertIn('move-object v5, p2',new)
        self.assertIn('invoke-direct/range {v0 .. v7}',new)
        with self.assertRaises(ValueError):patch_icon_helper(new)

    def test_unreviewed_branch_and_register_changes_are_rejected(self):
        for old in ['',self.specimen().replace('.registers 15','.registers 14'),self.specimen().replace(':cond_22',':cond_23')]:
            with self.assertRaises(ValueError):patch_icon_helper(old)

if __name__=='__main__':unittest.main()
