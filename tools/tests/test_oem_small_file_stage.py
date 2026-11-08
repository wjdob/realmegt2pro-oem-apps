from pathlib import Path
import hashlib,shutil,subprocess,sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import oem_small_file_stage as stage
import oem_installed_space as space

class SmallFileStageTests(unittest.TestCase):
    def test_old_recipes_exact_and_extension_strict(self):
        base=(Path(stage.__file__).parent/'oem_addons/update-binary.sh').read_bytes()
        self.assertEqual(hashlib.sha256(base).hexdigest(),space.BASE_SHA256)
        self.assertEqual(stage.prepare(base,{}),base)
        profile={'id':'photos','platform':{'sdk':37,'lineage':'24.0'},'extent_staged_metadata':True,'installed_space_budget':True}
        source=space.prepare(base,profile);output=stage.prepare(source,profile)
        self.assertIn(b'verify_persisted_files()',output)
        self.assertLess(output.index(b'chcon "$(file_context "$rel")" "$dest.realme-new"'),output.index(b'finish_staged_payload "$dest.realme-new"'))
        for bad in [dict(profile,extent_staged_metadata=False),dict(profile,id='other'),dict(profile,platform={'sdk':36,'lineage':'23.2'})]:
            with self.assertRaises(ValueError):stage.prepare(source,bad)
        with self.assertRaises(ValueError):stage.prepare(source+b'changed',profile)

    @unittest.skipUnless(all(shutil.which(t) for t in ['sh','dd','truncate','stat','sha256sum']),'POSIX staging tools required')
    def test_real_staging_sizes_bytes_and_hash_refusal(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for length in [0,123,4096,4097,12000]:
                data=bytes(i%251 for i in range(length));(root/'input').write_bytes(data);digest=hashlib.sha256(data).hexdigest()
                script=stage.FUNCTION+'\nset -eu\nstage_payload "$1/input" "$1/output"\nif [ "$staged_size" -le 4096 ]; then [ "$(stat -c %s "$1/output")" = 4096 ]; fi\nfinish_staged_payload "$1/output" "$2"\n'
                result=subprocess.run(['sh','-s','--',directory,digest],input=script,text=True,capture_output=True)
                self.assertEqual(result.returncode,0,result.stderr);self.assertEqual((root/'output').read_bytes(),data)
                result=subprocess.run(['sh','-s','--',directory,'0'*64],input=script,text=True,capture_output=True)
                self.assertNotEqual(result.returncode,0)
