from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from oem_gallery_followup import patch_toolkit,HELPER

class ToolkitPatchTests(unittest.TestCase):
    def test_only_target_constructor_changes_and_wide_parameter_call_is_preserved(self):
        before='prefix\n.method public constructor <init>(Landroid/content/Context;Landroid/util/AttributeSet;I)V\n    .registers 30\n    move-object/from16 v1, p0\n    return-void\n.end method\nother methods'
        after=patch_toolkit(before)
        self.assertTrue(after.startswith('prefix\n'));self.assertTrue(after.endswith('\nother methods'))
        self.assertIn('invoke-static/range {p1 .. p1}',after);self.assertIn('move-result-object p1',after)
        self.assertLess(after.index(HELPER),after.index('move-object/from16 v1, p0'))
        with self.assertRaises(ValueError):patch_toolkit(after)

    def test_unknown_constructor_or_register_layout_is_rejected(self):
        for value in ['', '.method public constructor <init>(Landroid/content/Context;Landroid/util/AttributeSet;I)V\n.registers 29\n.end method']:
            with self.assertRaises(ValueError):patch_toolkit(value)

if __name__=='__main__':unittest.main()
