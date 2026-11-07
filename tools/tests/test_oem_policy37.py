"""Scope, provenance and reversible policy tests; no Android policy is loaded.

The subprocess fixture uses an explicitly hash-bound fake compiler. Actual
neverallow compilation is a separate integration check against target-files.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import oem_policy_context as ctx
import oem_policy37 as build
import oem_runtime_policy as runtime
import oem_platform


class Policy37ContextTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ctx.ROOT/'configs/oem-policy-native22-24.0.json').read_text())
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/'configs').mkdir()

    def context(self, config=None):
        raw = (json.dumps(config or self.config, indent=2)+'\n').encode()
        return ctx.Context(raw, ctx.sha(raw))

    def test_context_records_selected_mapping_and_genfs_inputs(self):
        config = self.context().config
        self.assertEqual(config['mapping_version'], '202604')
        self.assertEqual(config['genfs_labels_version'], '202604')
        self.assertEqual(len(config['compiler_inputs']), 10)
        self.assertEqual(len(config['runtime_inputs']), 12)
        self.assertEqual(len(config['inputs']), 16)
        self.assertNotIn('system/etc/selinux/plat_sepolicy_genfs_202704.cil', config['inputs'])

    def test_context_bytes_and_returned_values_are_independent(self):
        context = self.context()
        context.config['inputs'].clear()
        self.assertEqual(len(context.config['inputs']), 16)
        with self.assertRaisesRegex(ValueError, 'bytes changed'):
            ctx.Context(context.raw+b' ', context.expected_sha256).config

    def test_legacy_mapping_missing_metadata_and_broader_scope_are_refused(self):
        for mode in ('mapping', 'genfs', 'metadata', 'extra', 'compiler', 'fragment', 'platform'):
            config = copy.deepcopy(self.config)
            if mode == 'mapping': config['mapping_version'] = '202504'
            if mode == 'genfs': config['genfs_labels_version'] = '202704'
            if mode == 'metadata': config['inputs'].pop(next(iter(ctx.oem_ota_compat.VERSION_INPUTS37)))
            if mode == 'extra': config['inputs']['data/system/locksettings.db'] = 'a'*64
            if mode == 'compiler': config['compiler_inputs'].append(config['compiler_inputs'][0])
            if mode == 'fragment': config['gallery_fragment_sha256'] = 'a'*64
            if mode == 'platform': config['platform'] = dict(oem_platform.LEGACY)
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.context(config).config

    def test_loader_requires_reviewed_path_and_full_digest(self):
        raw = self.context().raw
        path = self.root/'configs/test.json'
        path.write_bytes(raw)
        good = {'source': 'configs/test.json', 'sha256': ctx.sha(raw)}
        with patch.object(ctx, 'ROOT', self.root):
            self.assertEqual(ctx.load(good).config, self.config)
            for source in ('../test.json', '/configs/test.json', 'configs/../test.json', 'configs/a/b.json'):
                with self.subTest(source=source), self.assertRaises(ValueError):
                    ctx.load(dict(good, source=source))
            with self.assertRaisesRegex(ValueError, 'bytes changed'):
                ctx.load(dict(good, sha256='f'*64))

    def test_profile_generation_cannot_implicitly_choose_another_policy(self):
        with self.assertRaises(ValueError):
            runtime.context_for_profile({'platform': oem_platform.ANDROID17})
        with self.assertRaises(ValueError):
            runtime.context_for_profile({'policy': {'base_context': {'source': 'configs/x.json'}}})
        self.assertIsNone(runtime.context_for_profile({}))
        self.assertNotEqual(runtime.editor_backend({}), runtime.editor_backend({'platform': oem_platform.ANDROID17}))

    def fixture(self):
        config = copy.deepcopy(self.config)
        base = {p: ('base:'+p).encode() for p in ctx.INPUTS}
        prefix = 'product/etc/selinux/'
        base[prefix+'product_mac_permissions.xml'] = b'<policy/>\n'
        base[prefix+'product_seapp_contexts'] = b'# existing app mappings\n'
        base[prefix+'product_sepolicy_and_mapping.sha256'] = (
            ctx.sha(base[build.PRODUCT_CIL]+base[ctx.PRODUCT_MAPPING])+'\n').encode()
        for path in ctx.oem_ota_compat.VERSION_INPUTS37: base[path] = b'202604\n'
        target = self.root/'target.zip'
        with zipfile.ZipFile(target, 'w') as z:
            for p, data in base.items():
                part, tail = p.split('/', 1)
                z.writestr(part.upper()+'/'+tail, data)
            z.writestr('SYSTEM/build.prop', 'ro.build.version.sdk=37\nro.build.version.incremental='+config['build_incremental']+'\n')
        compiler = self.root/'fixture-secilc'
        compiler.write_text('#!'+sys.executable+'\nimport pathlib,sys\na=sys.argv\npathlib.Path(a[a.index("-o")+1]).write_bytes(b"synthetic compiler output")\n')
        compiler.chmod(0o755)
        config['compiler']['sha256'] = build.sha_file(compiler)
        config['target_files_sha256'] = build.sha_file(target)
        config['inputs'] = {p: ctx.sha(data) for p, data in base.items()}
        camera, gallery = b'fixture camera cert', b'fixture gallery cert'
        for name, value in [('CAMERA_CERT_SHA', ctx.sha(camera)), ('GALLERY_CERT_SHA', ctx.sha(gallery))]:
            p = patch.object(runtime, name, value)
            p.start()
            self.addCleanup(p.stop)
        context = self.context(config)
        output = self.root/'bundle'
        build.prepare(target, compiler, camera, gallery, output, context)
        return output, context

    @unittest.skipUnless(os.name == 'posix', 'POSIX executable compiler fixture')
    def test_actual_export_transaction_reconstructs_four_product_files(self):
        output, context = self.fixture()
        manifest, files = runtime.load_bundle(output, build.sha_file(output/'manifest.json'), context=context)
        self.assertEqual(manifest['platform'], oem_platform.ANDROID17)
        self.assertEqual(set(files), runtime.shared_paths())
        self.assertEqual(len(manifest['patches']), 4)
        for row in manifest['patches']:
            self.assertTrue(row['path'].startswith('product/etc/selinux/'))
        with self.assertRaisesRegex(ValueError, 'platform differs'):
            runtime.load_bundle(output, build.sha_file(output/'manifest.json'))

    @unittest.skipUnless(os.name == 'posix', 'POSIX executable compiler fixture')
    def test_rehashed_global_mapping_is_rejected(self):
        output, context = self.fixture()
        relative = runtime.STORAGE+'postimages/product_seapp_contexts'
        path = output/relative
        path.write_bytes(path.read_bytes()+b'\nuser=_app domain=realme_gallery_app\n')
        manifest = json.loads((output/'manifest.json').read_text())
        manifest['files'][relative] = build.sha_file(path)
        (output/'manifest.json').write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'body exceeds'):
            runtime.load_bundle(output, build.sha_file(output/'manifest.json'), context=context)

    @unittest.skipUnless(os.name == 'posix', 'POSIX executable compiler fixture')
    def test_compile_proof_binds_command_candidate_and_exact_context(self):
        output, context = self.fixture()
        proof = json.loads((output/'compile-report.json').read_text())
        candidate = (output/(runtime.STORAGE+'postimages/product_sepolicy.cil')).read_bytes()
        for mode in ('neverallow', 'order', 'return', 'compiler', 'context', 'candidate', 'input', 'device'):
            changed = copy.deepcopy(proof)
            if mode == 'neverallow': changed['commands'][0]['args'].append('-N')
            if mode == 'order': changed['commands'][0]['args'][7] = '/other/input.cil'
            if mode == 'return': changed['commands'][0]['code'] = 1
            if mode == 'compiler': changed['compiler_sha256'] = 'a'*64
            if mode == 'context': changed['context_sha256'] = 'b'*64
            if mode == 'candidate': changed['candidate_sha256'] = 'c'*64
            if mode == 'input': changed['input_hashes'].pop(next(iter(changed['input_hashes'])))
            if mode == 'device': changed['device_accessed'] = True
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                build.verify_compile_proof(changed, context.config, context.expected_sha256, candidate)

    @unittest.skipUnless(os.name == 'posix', 'POSIX executable compiler fixture')
    def test_compiled_output_tampering_is_refused(self):
        output, context = self.fixture()
        (output/'compiled.policy').write_bytes(b'changed output')
        with self.assertRaisesRegex(ValueError, 'proof bytes differ'):
            runtime.load_bundle(output, build.sha_file(output/'manifest.json'), context=context)


if __name__ == '__main__':
    unittest.main()
