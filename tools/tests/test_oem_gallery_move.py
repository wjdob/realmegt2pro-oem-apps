from pathlib import Path
import os
import io
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from oem_gallery_move import patch_move_callback, helper_entry, TARGET, HELPER, METHOD, ROOT


class MoveGateTests(unittest.TestCase):
    def specimen(self):
        return ('outside\n'+METHOD+'\n    .registers 13\n'
                '    iget-object v2, p0, '+TARGET+'->this$0:Lcom/oplus/aiunit/vision/ad9;\n'
                '    iget-object v3, p0, '+TARGET+'->$context:Landroid/content/Context;\n'
                '    invoke-static {}, Lcom/oplus/aiunit/vision/r45;->a()Lcom/oplus/aiunit/vision/r45;\n'
                '    move-result-object v4\n    monitor-enter v4\n'
                '    invoke-virtual {p0, p1}, Lcom/oplus/aiunit/vision/wc9;->i(Ljava/util/ArrayList;)I\n'
                '    return-void\n.end method\nuntouched')

    def test_consent_precedes_lock_and_mutation_and_decline_sets_original_cancel_flag(self):
        old = self.specimen(); new = patch_move_callback(old)
        self.assertLess(new.index(HELPER), new.index('monitor-enter'))
        self.assertLess(new.index(HELPER), new.index('wc9;->i'))
        self.assertIn('instance-of v4, v2, Lcom/oplus/aiunit/vision/md9;', new)
        self.assertIn('iput-boolean v4, v2, Lcom/oplus/aiunit/vision/ad9;->m:Z\n\n    return-void', new)
        self.assertTrue(new.startswith('outside\n')); self.assertTrue(new.endswith('\nuntouched'))
        self.assertEqual(new.count('wc9;->i'), 1)

    def test_repeat_unknown_register_context_or_lock_layout_is_rejected(self):
        old = self.specimen()
        for value in [patch_move_callback(old), old.replace('registers 13', 'registers 14'),
                      old.replace('iget-object v3', 'iget-object v4'),
                      old.replace('monitor-enter v4', 'monitor-enter v5'), '']:
            with self.assertRaises(ValueError): patch_move_callback(value)

    def test_helper_member_is_reproducible_across_build_clocks(self):
        original = zipfile.ZipInfo('classes5.dex', (2024, 4, 6, 8, 10, 12))
        original.compress_type = zipfile.ZIP_DEFLATED
        original.create_system = 3
        original.external_attr = 0o100644 << 16
        outputs = []
        for now in [(2026, 10, 30, 12, 0, 0), (2027, 1, 3, 1, 2, 4)]:
            stream = io.BytesIO()
            with mock.patch('zipfile.time.localtime', return_value=now):
                with zipfile.ZipFile(stream, 'w') as archive:
                    archive.writestr(helper_entry(original), b'compiled helper fixture')
            outputs.append(stream.getvalue())
        self.assertEqual(outputs[0], outputs[1])
        with zipfile.ZipFile(io.BytesIO(outputs[0])) as archive:
            entry = archive.getinfo('classes11.dex')
            self.assertEqual(entry.date_time, original.date_time)
            self.assertEqual(entry.external_attr, original.external_attr)
            self.assertEqual(archive.read(entry), b'compiled helper fixture')


class MoveCoreJavaTests(unittest.TestCase):
    def test_snapshot_boundaries_and_consent_lifecycle(self):
        java = Path(os.environ.get('GALLERY_TEST_JAVA', '/build/lineage/prebuilts/jdk/jdk21/linux-x86/bin'))
        if not (java/'javac').is_file(): self.skipTest('build JDK21 unavailable')
        harness = r'''
import realme.gallery.compat.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;
public class MoveCoreProbe {
  interface Job { void run() throws Exception; }
  static void ok(boolean test) { if (!test) throw new AssertionError(); }
  static void rejects(Job job) throws Exception {
    try { job.run(); } catch (IllegalArgumentException expected) { return; }
    throw new AssertionError("invalid item accepted");
  }
  public static void main(String[] args) throws Exception {
    Set<String> volumes = new HashSet<>(Arrays.asList("external_primary", "abcd-1234"));
    String root = "/storage/emulated/0";
    String source = root+"/DCIM/a.jpg", target = root+"/Pictures/Album/a.jpg";
    MoveMediaItem item = new MoveMediaItem(214, 1, source, target, root, volumes);
    ok(item.volume.equals("external_primary")); ok(item.matches(214, source));
    ok(!item.matches(215, source)); ok(!item.matches(214, root+"/DCIM/b.jpg"));
    ok(new MoveMediaItem(214, 3, "/storage/abcd-1234/DCIM/a.mp4",
       "/storage/abcd-1234/Movies/a.mp4", root, volumes).volume.equals("abcd-1234"));
    rejects(() -> new MoveMediaItem(0, 1, source, target, root, volumes));
    rejects(() -> new MoveMediaItem(214, 2, source, target, root, volumes));
    rejects(() -> new MoveMediaItem(214, 1, source, source, root, volumes));
    rejects(() -> new MoveMediaItem(214, 1, root+"/DCIM/../a.jpg", target, root, volumes));
    rejects(() -> new MoveMediaItem(214, 1, "/storage/emulated/10/DCIM/a.jpg", target, root, volumes));
    rejects(() -> new MoveMediaItem(214, 1, source, "/storage/abcd-1234/Pictures/a.jpg", root, volumes));
    rejects(() -> new MoveMediaItem(214, 1, "/data/private.jpg", target, root, volumes));
    rejects(() -> new MoveMediaItem(214, 1, "relative.jpg", target, root, volumes));
    rejects(() -> new MoveMediaItem(214, 1, source, root+"/../10/Pictures/a.jpg", root, volumes));
    rejects(() -> new MoveMediaItem(214, 1, source, null, root, volumes));
    AtomicLong clock = new AtomicLong(1000);
    MoveConsentState approved = new MoveConsentState(clock::get, 1000);
    ok(approved.pending()); ok(approved.finish(true)); ok(approved.await());
    ok(!approved.finish(false)); ok(approved.await());
    MoveConsentState denied = new MoveConsentState(clock::get, 1000);
    ok(denied.finish(false)); ok(!denied.finish(true)); ok(!denied.await());
    MoveConsentState expired = new MoveConsentState(clock::get, 1000);
    clock.set(2000); ok(!expired.pending()); ok(!expired.finish(true)); ok(!expired.await());
    MoveConsentState late = new MoveConsentState(clock::get, 1000);
    clock.set(3000); ok(late.finish(true)); ok(!late.await());
    for (int n=0; n<20; n++) {
      MoveConsentState race = new MoveConsentState(clock::get, 1000);
      CountDownLatch start = new CountDownLatch(1);
      AtomicInteger winners = new AtomicInteger();
      Thread yes = new Thread(() -> { try { start.await(); if(race.finish(true)) winners.incrementAndGet(); }
                                      catch (InterruptedException e) { throw new AssertionError(e); } });
      Thread no = new Thread(() -> { try { start.await(); if(race.finish(false)) winners.incrementAndGet(); }
                                     catch (InterruptedException e) { throw new AssertionError(e); } });
      yes.start(); no.start(); start.countDown(); yes.join(); no.join();
      ok(winners.get()==1); ok(!race.pending()); ok(!race.finish(true));
    }
    MoveConsentState interrupted = new MoveConsentState(() -> System.nanoTime()/1000000, 1000);
    AtomicBoolean caught = new AtomicBoolean();
    Thread worker = new Thread(() -> { try { interrupted.await(); }
                                      catch (InterruptedException e) { caught.set(true); } });
    worker.start(); worker.interrupt(); worker.join(1000);
    ok(!worker.isAlive()); ok(caught.get()); ok(!interrupted.await()); ok(!interrupted.finish(true));
    MoveConsentState timeout = new MoveConsentState(() -> System.nanoTime()/1000000, 10);
    ok(!timeout.await()); ok(!timeout.finish(true));
    rejects(() -> new MoveConsentState(clock::get, 0));
    rejects(() -> new MoveConsentState(clock::get, 300001));
    System.out.println("snapshot, cancellation, deadline, race and interruption cases pass");
  }
}
'''
        with tempfile.TemporaryDirectory(prefix='gallery-move-core-') as name:
            work = Path(name); source = work/'MoveCoreProbe.java'; source.write_text(harness)
            subprocess.run([str(java/'javac'), '--release', '8', '-d', str(work),
                            str(ROOT/'tools/oem_gallery_compat/MoveConsentState.java'),
                            str(ROOT/'tools/oem_gallery_compat/MoveMediaItem.java'), str(source)],
                           check=True, capture_output=True, timeout=30)
            result = subprocess.run([str(java/'java'), '-cp', str(work), 'MoveCoreProbe'],
                                    check=True, capture_output=True, timeout=15, text=True)
            self.assertIn('cases pass', result.stdout)


if __name__ == '__main__': unittest.main()
