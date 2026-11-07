"""Exercise coordinated upgrade outcomes without a phone or mounted partitions."""
from pathlib import Path
import json
import subprocess
import tempfile
import unittest
import sys
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import oem_lens_bundle as lens


class LensBundleTests(unittest.TestCase):
    def scenario(self,camera,photos,fail=''):
        source=(lens.ROOT/'tools/oem_addons/lens-upgrade.sh').read_text().split('# HOST_TEST_FUNCTIONS_END')[0]
        with tempfile.TemporaryDirectory() as directory:
            program=source+'''
WORK="$4"; cam="$5"; photo="$6"; fail="$7"
ui(){ echo "$*"; }
run_pkg(){
 echo "STEP:$1"; : > "$WORK/result.log"
 case "$1" in
 legacy-camera-check)
  [ "$cam" = old ] || [ "$cam" = none ] || return 1
  [ "$photo" = old ] || [ "$photo" = none ] || return 1
  if [ "$photo" = old ]; then echo 'Exact peer app and shared runtime verified' > "$WORK/result.log"; fi ;;
 legacy-photos-check) [ "$photo" = old ] && [ "$fail" != legacy-check ] || return 1 ;;
 legacy-photos-remove) [ "$fail" != remove ] || return 1; photo=none ;;
 legacy-photos-install) [ "$fail" != restore ] || return 1; photo=old ;;
 camera-check) [ "$cam" = new ] && { [ "$photo" = new ] || [ "$photo" = none ]; } || return 1 ;;
 camera-install) [ "$fail" != camera ] && [ "$fail" != restore ] || return 1; cam=new ;;
 photos-install) [ "$fail" != photos ] || return 1; photo=new ;;
 photos-check) [ "$photo" = new ] || return 1 ;;
 *) return 1 ;;
 esac
 return 0
}
if orchestrate; then rc=0; else rc=1; fi
echo "FINAL:$cam:$photo:$rc"
'''
            result=subprocess.run(['sh','-s','--','3','1','unused',directory,camera,photos,fail],input=program,text=True,capture_output=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            return result.stdout

    def test_fresh_camera_does_not_install_photos(self):
        for camera in ('none','old'):
            output=self.scenario(camera,'none')
            self.assertIn('FINAL:new:none:0',output)
            self.assertNotIn('STEP:photos-install',output)

    def test_existing_cohort_is_upgraded_and_photos_restored(self):
        output=self.scenario('old','old')
        self.assertIn('FINAL:new:new:0',output)
        steps=[line for line in output.splitlines() if line.startswith('STEP:')]
        self.assertLess(steps.index('STEP:legacy-photos-check'),steps.index('STEP:legacy-photos-remove'))
        self.assertLess(steps.index('STEP:legacy-photos-remove'),steps.index('STEP:camera-install'))
        self.assertLess(steps.index('STEP:camera-install'),steps.index('STEP:photos-install'))

    def test_modern_cohort_and_camera_only_are_idempotent(self):
        for photos in ('none','new'):
            output=self.scenario('new',photos)
            self.assertIn('FINAL:new:'+photos+':0',output)
            self.assertNotIn('STEP:legacy-photos-remove',output)

    def test_unknown_or_failed_preflight_performs_no_install(self):
        for camera,photos,fail in [('unknown','old',''),('old','unknown',''),('old','old','legacy-check')]:
            output=self.scenario(camera,photos,fail)
            self.assertNotIn('STEP:camera-install',output)
            self.assertNotIn('STEP:legacy-photos-remove',output)
            self.assertIn('FINAL:'+camera+':'+photos+':1',output)

    def test_child_failure_and_incomplete_restore_are_reported(self):
        output=self.scenario('old','old','camera')
        self.assertIn('STEP:legacy-photos-install',output)
        self.assertIn('FINAL:old:old:1',output)
        output=self.scenario('old','old','remove')
        self.assertIn('FINAL:old:old:1',output)
        self.assertNotIn('STEP:camera-install',output)
        output=self.scenario('old','old','photos')
        self.assertIn('FINAL:new:none:1',output)
        self.assertIn('Sideload matching Photos before reboot',output)
        output=self.scenario('old','old','restore')
        self.assertIn('STOP: Photos restoration failed',output)
        self.assertIn('FINAL:old:none:1',output)

    def test_patch_scope_and_sdk_hash_are_enforced(self):
        sdk=b'new-reviewed-sdk';receipt='product/etc/realme-oem-addons/camera.json'
        hook='system/addon.d/80-realme-camera.sh'
        original={lens.SDK:b'old-sdk',receipt:json.dumps({'shared_sdk_sha256':lens.addons.sha(b'old-sdk'),'retained':'yes'}).encode(),
                  hook:b'old-hook','system/priv-app/RealmeStockCamera/base.apk':b'unchanged-apk'}
        profile={'id':'camera','lens_sdk_sha256':lens.addons.sha(sdk)}
        with patch.object(lens.addons,'hook',return_value=b'new-hook'),patch.object(lens.addons,'validate_gallery_runtime'):
            result=lens.patch_payload(profile,original,sdk)
            self.assertEqual(result['system/priv-app/RealmeStockCamera/base.apk'],b'unchanged-apk')
            self.assertEqual(json.loads(result[receipt])['retained'],'yes')
            with self.assertRaises(ValueError):lens.patch_payload(profile,original,b'wrong-sdk')
            invalid=dict(original);invalid[receipt]=b'{"shared_sdk_sha256":"wrong"}'
            with self.assertRaises(ValueError):lens.patch_payload(profile,invalid,sdk)

    def test_incomplete_children_refused(self):
        with self.assertRaises(ValueError):lens.coordinated({'camera-install':b'bytes'},32768)


if __name__=='__main__':unittest.main()
