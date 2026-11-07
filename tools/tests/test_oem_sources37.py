import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import prepare_oem37_sources as stage


class SourceBoundariesTests(unittest.TestCase):
    def test_drift_is_rejected_before_creating_a_source_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, 'source bytes differ'):
                stage.write_reviewed(root, 'shared/native.so', b'changed', '0'*64)
            self.assertFalse((root/'shared').exists())

    def test_shared_sources_must_be_equal_and_destinations_stay_in_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = b'accepted'
            digest = hashlib.sha256(value).hexdigest()
            stage.write_reviewed(root, 'shared/input', value, digest)
            stage.write_reviewed(root, 'shared/input', value, digest)
            changed = b'different'
            with self.assertRaisesRegex(ValueError, 'conflicting shared source'):
                stage.write_reviewed(root, 'shared/input', changed, hashlib.sha256(changed).hexdigest())
            for name in ('../outside', '/absolute', 'shared/../../outside'):
                with self.assertRaises(ValueError):
                    stage.write_reviewed(root, name, value, digest)
            self.assertEqual((root/'shared/input').read_bytes(), value)


if __name__ == '__main__':
    unittest.main()
