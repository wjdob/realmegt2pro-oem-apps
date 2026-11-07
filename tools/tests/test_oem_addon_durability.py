"""Run the real durable-verification/cleanup functions with simulated mounts."""
from pathlib import Path
import hashlib
import shlex
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
SCRIPT=(ROOT/'tools/oem_addons/update-binary.sh').read_text()


def function(name):
    start=SCRIPT.index(name+'() {')
    return SCRIPT[start:SCRIPT.index('\n}',start)+3]


class DurabilityTests(unittest.TestCase):
    def run_fixture(self,mode='success',action='install'):
        parent=ROOT/'tmp/oem-durability-tests';parent.mkdir(parents=True,exist_ok=True)
        temporary=tempfile.TemporaryDirectory(dir=parent);self.addCleanup(temporary.cleanup)
        work=Path(temporary.name)
        product=work/'product-mount';system=work/'system-mount/system'
        (product/'etc').mkdir(parents=True);system.mkdir(parents=True)
        payload=b'exact signed XML\n';digest=hashlib.sha256(payload).hexdigest()
        manifest=[digest+'  product/etc/app.xml']
        if action=='install':(product/'etc/app.xml').write_bytes(payload)
        else:
            (product/'framework').mkdir();(product/'framework/shared.jar').write_bytes(payload)
            (product/'etc/policy.xml').write_bytes(b'patched policy' if mode=='retained-policy' else b'original policy')
            manifest += [digest+'  product/framework/shared.jar',hashlib.sha256(b'patched policy').hexdigest()+'  product/etc/policy.xml']
        (work/'manifest.sha256').write_text('\n'.join(manifest)+'\n')
        (work/'rollback').mkdir();(work/'rollback/saved-original').write_bytes(b'preserve rollback')
        body='\n'.join(function(name) for name in ('target','verify_persisted_files','cleanup'))
        setup='''
set -eu
WORK='''+shlex.quote(str(work))+'''
PRODUCT=$WORK/product-mount
SYSTEM_MOUNT=$WORK/system-mount
SYSTEM=$SYSTEM_MOUNT/system
SLOT=_b
ACTION='''+shlex.quote(action)+'''
MODE='''+shlex.quote(mode)+'''
MOUNTED_SYSTEM=1
MOUNTED_PRODUCT=1
MOUNTED_EXTRAS=''
WRITABLE=1
COMMITTED=0
DURABILITY_PENDING=0
ui() { printf '%s\n' "$*"; }
die() { ui "ERROR: $*"; exit 1; }
release_policy_inputs() { :; }
policy_patch() { [ "$1" = product/etc/policy.xml ]; }
policy_before() { printf '%s\n' '''+hashlib.sha256(b'original policy').hexdigest()+'''; }
retain_shared() { [ "$1" = product/framework/shared.jar ] || { [ "$MODE" = retained-policy ] && [ "$1" = product/etc/policy.xml ]; }; }
sync() { printf 'sync\n' >> "$WORK/commands"; }
umount() {
 printf 'umount %s\n' "$*" >> "$WORK/commands"
 [ "$MODE" != unmount-failure ]
}
mount() {
 printf 'mount %s\n' "$*" >> "$WORK/commands"
 [ "$1:$2:$3:$4" = '-t:ext4:-o:ro,noload' ] || exit 99
 if [ "$MODE" = corrupt-after-unmount ] && [ "$5" = /dev/block/mapper/product_b ]; then
  printf 'stale unrelated disk bytes' > "$PRODUCT/etc/app.xml"
 fi
}
trap cleanup EXIT
verify_persisted_files
COMMITTED=1
printf 'COMPLETE\n'
'''
        result=subprocess.run(['sh','-c',body+'\n'+setup],capture_output=True,text=True)
        self.assertEqual((work/'rollback/saved-original').read_bytes(),b'preserve rollback')
        return result,work

    def test_success_checks_both_filesystems_after_unmount_before_commit(self):
        p,work=self.run_fixture();self.assertEqual(p.returncode,0,p.stderr);self.assertIn('COMPLETE',p.stdout)
        commands=(work/'commands').read_text()
        self.assertLess(commands.index('umount'),commands.index('mount -t'))
        self.assertEqual(commands.count('mount -t ext4 -o ro,noload'),2)
        self.assertNotIn('-o rw',commands)

    def test_cache_time_success_followed_by_remount_corruption_is_failure(self):
        p,work=self.run_fixture('corrupt-after-unmount')
        self.assertNotEqual(p.returncode,0);self.assertNotIn('COMPLETE',p.stdout)
        self.assertIn('Durable file hash mismatch: product/etc/app.xml',p.stdout)
        self.assertIn('scratch retained',p.stdout)
        self.assertEqual((work/'product-mount/etc/app.xml').read_bytes(),b'stale unrelated disk bytes')

    def test_partial_unmount_failure_never_rolls_back_into_bare_scratch(self):
        p,work=self.run_fixture('unmount-failure')
        self.assertNotEqual(p.returncode,0);self.assertNotIn('COMPLETE',p.stdout)
        self.assertIn('scratch retained',p.stdout);self.assertNotIn('Rolling back',p.stdout)
        self.assertNotIn('mount -t',(work/'commands').read_text())

    def test_remove_checks_absence_retained_peer_and_original_policy(self):
        p,_=self.run_fixture(action='remove');self.assertEqual(p.returncode,0,p.stderr);self.assertIn('COMPLETE',p.stdout)

    def test_remove_preserves_policy_when_the_other_bundle_still_owns_it(self):
        p,_=self.run_fixture(mode='retained-policy',action='remove')
        self.assertEqual(p.returncode,0,p.stderr);self.assertIn('COMPLETE',p.stdout)


if __name__=='__main__':unittest.main()
