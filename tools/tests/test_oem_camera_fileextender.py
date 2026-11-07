"""FileExtender staging must reject a partial or unreviewed native pair."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import oem_camera_fileextender as extender


class FileExtenderPreparationTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name)
        self.native = self.root / 'native'
        self.native.mkdir()
        self.output = self.root / 'prepared'
        self.contract = self.root / 'contract.json'
        self.files = {}
        for name, source in [('libFileExtender-jni.qti.so', 'libFileExtender-jni.so'),
                             ('libextendfile.so', 'libextendfile.so')]:
            data = bytearray(64)
            data[:6] = b'\x7fELF\x02\x01'
            data[18:20] = (183).to_bytes(2, 'little')
            data += name.encode()
            (self.native / source).write_bytes(data)
            self.files[name] = dict(source=source, sha256=hashlib.sha256(data).hexdigest())
        self.spec = dict(files=self.files, scope='test pair', limits='synthetic ELF fixtures')
        self.contract.write_text(json.dumps(self.spec))
        self.guard = patch.object(extender, 'CONTRACT', self.contract)
        self.guard.start()
        self.addCleanup(self.guard.stop)

    def test_requested_qti_filename_preserves_original_bytes(self):
        result = extender.prepare(self.native, self.output)
        self.assertFalse(result['installable'])
        self.assertEqual(set(result['files']), set(self.files))
        for name, spec in self.files.items():
            self.assertEqual((self.output / name).read_bytes(),
                             (self.native / spec['source']).read_bytes())
        self.assertFalse((self.output / 'libFileExtender-jni.so').exists())

    def test_modified_provider_rejected_before_any_output(self):
        (self.native / 'libextendfile.so').write_bytes(b'wrong provider')
        with self.assertRaisesRegex(ValueError, 'unreviewed ARM64'):
            extender.prepare(self.native, self.output)
        self.assertFalse(self.output.exists())

    def test_wrong_architecture_rejected_even_with_matching_hash(self):
        path = self.native / 'libextendfile.so'
        data = bytearray(path.read_bytes())
        data[18:20] = (62).to_bytes(2, 'little')
        path.write_bytes(data)
        self.files['libextendfile.so']['sha256'] = hashlib.sha256(data).hexdigest()
        self.contract.write_text(json.dumps(self.spec))
        with self.assertRaisesRegex(ValueError, 'unreviewed ARM64'):
            extender.prepare(self.native, self.output)
        self.assertFalse(self.output.exists())

    def test_existing_destination_is_never_replaced(self):
        self.output.mkdir()
        sentinel = self.output / 'keep'
        sentinel.write_bytes(b'preserve')
        with self.assertRaisesRegex(ValueError, 'refusing to overwrite'):
            extender.prepare(self.native, self.output)
        self.assertEqual(list(self.output.iterdir()), [sentinel])
        self.assertEqual(sentinel.read_bytes(), b'preserve')


if __name__ == '__main__':
    unittest.main()
