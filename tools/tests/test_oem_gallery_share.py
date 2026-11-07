from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from oem_gallery_share import patch_share_utils


class ShareGrantTests(unittest.TestCase):
    def specimen(self):
        return ('prefix\n.method public static final j(Ljava/util/Set;Z)Landroid/content/Intent;\n'
                '    .registers 16\n    # Keep source URI and chosen metadata mode\n'
                '    invoke-virtual {v2, v0}, Landroid/content/Intent;->addFlags(I)Landroid/content/Intent;\n\n'
                '    const/4 p0, 0x2\n\n    .line 27\n\n'
                '    invoke-virtual {v2, p0}, Landroid/content/Intent;->addFlags(I)Landroid/content/Intent;\n\n'
                '    const/high16 p0, 0x8880000\n    return-object v2\n.end method\nsuffix')

    def test_share_removes_only_write_delegation_and_keeps_original_source(self):
        old=self.specimen();new=patch_share_utils(old)
        removed='    const/4 p0, 0x2\n\n    .line 27\n\n    invoke-virtual {v2, p0}, Landroid/content/Intent;->addFlags(I)Landroid/content/Intent;\n\n'
        self.assertEqual(new,old.replace(removed,''))
        # Both retained baksmali formatting variants encode the same boundary.
        compact=old.replace('    .line 27\n\n','    .line 27\n')
        self.assertEqual(patch_share_utils(compact),new)
        with self.assertRaises(ValueError):patch_share_utils(new)

    def test_unreviewed_share_signature_flags_or_registers_are_rejected(self):
        for old in ['',self.specimen().replace('.registers 16','.registers 15'),self.specimen().replace('const/4 p0, 0x2','const/4 p0, 0x3')]:
            with self.assertRaises(ValueError):patch_share_utils(old)

if __name__=='__main__':unittest.main()
