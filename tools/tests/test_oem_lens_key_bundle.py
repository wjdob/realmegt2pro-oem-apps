"""Exercise multi-generation recognition, sequencing and payload boundaries."""
from pathlib import Path
import json
import io
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import oem_lens_key_bundle as formats


class LensKeyBundleTests(unittest.TestCase):
    def scenario(self,camera,photos,fail=''):
        source=(formats.ROOT/'tools/oem_addons/lens-key-upgrade.sh').read_text().split('# HOST_TEST_FUNCTIONS_END')[0]
        with tempfile.TemporaryDirectory() as directory:
            program=source+'''
WORK="$4"; cam="$5"; photo="$6"; fail="$7"
ui(){ echo "$*"; }
run_pkg(){
 echo "STEP:$1"; : > "$WORK/result.log"
 [ "$1" != "$fail" ] || return 1
 case "$1" in
 old0-camera-check|old1-camera-check|old2-camera-check)
  cohort=${1%%-*}
  { [ "$cam" = "$cohort" ] || [ "$cam" = none ]; } || return 1
  { [ "$photo" = "$cohort" ] || [ "$photo" = none ]; } || return 1
  if [ "$photo" = "$cohort" ]; then echo 'Exact peer app and shared runtime verified' > "$WORK/result.log"; fi ;;
 old0-photos-check|old1-photos-check|old2-photos-check) [ "$photo" = "${1%%-*}" ] || return 1 ;;
 old0-photos-remove|old1-photos-remove|old2-photos-remove) photo=none ;;
 old0-photos-install|old1-photos-install|old2-photos-install) photo=${1%%-*} ;;
 camera-check)
  { [ "$cam" = new ] || [ "$cam" = none ]; } && { [ "$photo" = new ] || [ "$photo" = none ]; } || return 1 ;;
 camera-install) [ "$fail" != restore ] || return 1; cam=new ;;
 photos-install) photo=new ;;
 photos-check) [ "$photo" = new ] || return 1 ;;
 *) return 1 ;;
 esac
 if [ "$fail" = restore ] && { [ "$1" = old0-photos-install ] || [ "$1" = old1-photos-install ] || [ "$1" = old2-photos-install ]; }; then photo=none;return 1;fi
 return 0
}
if orchestrate; then rc=0; else rc=1; fi
echo "FINAL:$cam:$photo:$rc"
'''
            result=subprocess.run(['sh','-s','--','3','1','unused',directory,camera,photos,fail],input=program,text=True,capture_output=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            return result.stdout

    def test_fresh_and_camera_only_keep_photos_absent(self):
        for camera in ('none','old0','old1','old2','new'):
            output=self.scenario(camera,'none')
            self.assertIn('FINAL:new:none:0',output)
            self.assertNotIn('STEP:photos-install',output)

    def test_each_old_cohort_is_recognized_before_removal(self):
        for old in ('old0','old1','old2'):
            output=self.scenario(old,old)
            self.assertIn('FINAL:new:new:0',output)
            steps=[x for x in output.splitlines() if x.startswith('STEP:')]
            self.assertLess(steps.index('STEP:'+old+'-photos-check'),steps.index('STEP:'+old+'-photos-remove'))
            self.assertLess(steps.index('STEP:'+old+'-photos-remove'),steps.index('STEP:camera-install'))
            self.assertLess(steps.index('STEP:camera-install'),steps.index('STEP:photos-install'))

    def test_current_cohort_is_idempotent(self):
        output=self.scenario('new','new');self.assertIn('FINAL:new:new:0',output)
        self.assertNotIn('photos-remove',output)

    def test_unknown_mixed_or_failed_preflight_never_writes(self):
        for camera,photos,fail in [('unknown','old0',''),('old1','unknown',''),('old0','old1',''),('old1','new',''),('old0','old0','old0-photos-check')]:
            output=self.scenario(camera,photos,fail)
            self.assertNotIn('STEP:camera-install',output)
            self.assertNotIn('-photos-remove',output)
            self.assertIn('FINAL:'+camera+':'+photos+':1',output)

    def test_failures_restore_or_stop_with_recovery_instructions(self):
        for old in ('old0','old1','old2'):
            output=self.scenario(old,old,'camera-install')
            self.assertIn('STEP:'+old+'-photos-install',output);self.assertIn(f'FINAL:{old}:{old}:1',output)
            output=self.scenario(old,old,old+'-photos-remove')
            self.assertNotIn('STEP:camera-install',output);self.assertIn(f'FINAL:{old}:{old}:1',output)
            output=self.scenario(old,old,'photos-install')
            self.assertIn('FINAL:new:none:1',output);self.assertIn('Sideload matching Photos before reboot',output)
            output=self.scenario(old,old,'restore')
            self.assertIn('STOP: Photos restoration failed',output);self.assertIn(f'FINAL:{old}:none:1',output)

    def test_payload_scope_keeps_apk_native_and_unrelated_receipt(self):
        sdk=b'new-sdk';apk=b'old-camera';root='system/priv-app/RealmeStockCamera';receipt='product/etc/realme-oem-addons/camera.json';hook='system/addon.d/80-realme-camera.sh'
        original={root+'/base.apk':apk,root+'/lib/arm64/libtest.so':b'native',formats.SDK:b'old-sdk',hook:b'old-hook',receipt:json.dumps({'shared_sdk_sha256':formats.addons.sha(b'old-sdk'),'unrelated':'preserved'}).encode()}
        profile={'id':'camera','package':'com.oplus.camera','app_partition':'system','app_class':'priv-app','directory':'RealmeStockCamera','sha256':formats.addons.sha(apk),'formats_sdk_sha256':formats.addons.sha(sdk),'addon_release':'alpha3-api37'}
        with patch.object(formats.addons,'hook',return_value=b'new-hook'),patch.object(formats.addons,'validate_gallery_runtime'):
            result=formats.patch_payload(profile,original,sdk,b'unused')
            self.assertEqual(result[root+'/base.apk'],apk);self.assertEqual(result[root+'/lib/arm64/libtest.so'],b'native')
            self.assertEqual(json.loads(result[receipt])['unrelated'],'preserved')
            with self.assertRaises(ValueError):formats.patch_payload(profile,original,b'wrong',b'')
            wrong=dict(original);wrong[root+'/base.apk']=b'modified'
            with self.assertRaises(ValueError):formats.patch_payload(profile,wrong,sdk,b'')

    def test_incomplete_children_refused(self):
        with self.assertRaises(ValueError):formats.coordinated({'camera-install':b'bytes'},32768)

    def test_all_three_cohorts_and_mixed_pairs(self):
        for old in ('old0','old1','old2'):
            self.assertIn('FINAL:new:new:0',self.scenario('none',old))
            for other in ('old0','old1','old2','new','unknown'):
                if other==old:continue
                output=self.scenario(old,other)
                self.assertNotIn('STEP:camera-install',output)
                self.assertNotIn('-photos-remove',output)

    def test_exact_sixteen_child_index_and_budget(self):
        names={name+'-'+action for name in ('camera','photos') for action in ('check','install')}
        names|={f'old{n}-{name}-{action}' for n in range(3) for name,action in
                [('camera','check'),('photos','check'),('photos','remove'),('photos','install')]}
        children={name:name.encode() for name in names}
        result=formats.coordinated(children,32768)
        with zipfile.ZipFile(io.BytesIO(result)) as z:
            index=z.read('children.sha256');script=z.read('META-INF/com/google/android/update-binary')
            self.assertEqual(len(index.splitlines()),16)
            self.assertIn(formats.addons.sha(index).encode(),script)
            self.assertIn(b'for cohort in old2 old1 old0;',script)
            self.assertNotIn(b'@SCRATCH_KIB@',script)
        with self.assertRaises(ValueError):formats.coordinated(children,32767)
        children['unexpected']=b'other'
        with self.assertRaises(ValueError):formats.coordinated(children,32768)

    def test_successor_scope_refuses_apk_native_paths_and_missing_changes(self):
        original={formats.SDK:b'old','product/etc/realme-oem-addons/camera.json':b'old',
                  'system/addon.d/80-realme-camera.sh':b'old','base.apk':b'original','native.so':b'original'}
        item={'id':'camera','hook':'80-realme-camera.sh'}
        correct={p:(b'new' if data==b'old' else data) for p,data in original.items()}
        with patch.object(formats,'patch_payload',return_value=correct):
            self.assertEqual(formats.patch_successor(item,original,b'sdk',b'apk'),correct)
        for path in ('base.apk','native.so',formats.SDK):
            wrong=dict(correct);wrong[path]=b'tampered' if path!=formats.SDK else original[path]
            with patch.object(formats,'patch_payload',return_value=wrong):
                with self.assertRaises(ValueError):formats.patch_successor(item,original,b'sdk',b'apk')
        with patch.object(formats,'patch_payload',return_value={**correct,'added':b'extra'}):
            with self.assertRaises(ValueError):formats.patch_successor(item,original,b'sdk',b'apk')


if __name__=='__main__':unittest.main()
