from pathlib import Path
import sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import prepare_stock_clock as clock

class StockClockStagingTests(unittest.TestCase):
    def test_unknown_apk_stops_before_signature_tool_or_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);apk=root/'clock.apk';apk.write_bytes(b'unknown')
            with patch.object(clock.subprocess,'run') as run,self.assertRaises(ValueError):clock.stage(apk,root,root/'out')
            run.assert_not_called();self.assertFalse((root/'out').exists())

    def test_existing_output_is_preserved_before_any_input_read(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);out=root/'out';out.mkdir();keep=out/'keep';keep.write_bytes(b'preserved')
            with self.assertRaises(ValueError):clock.stage(root/'missing.apk',root,out)
            self.assertEqual(keep.read_bytes(),b'preserved')

if __name__=='__main__':unittest.main()
