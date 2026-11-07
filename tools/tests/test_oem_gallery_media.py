from pathlib import Path
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from oem_gallery_app import string_pool, xml_chunks
from oem_gallery_media import (ROOT, SOURCES, PERMISSION, REPLACE, WORKER,
                               add_media_management, patch_worker, patch_query, prepare)
from test_oem_gallery_manifest import fixture

JDK = Path('/build/lineage/prebuilts/jdk/jdk21/linux-x86/bin')
SDK = Path('/build/lineage/prebuilts/sdk/31/public/android.jar')


class MediaManifestTests(unittest.TestCase):
    def test_only_one_permission_declaration_added_without_rewriting_components(self):
        original = xml_chunks(fixture())
        changed = xml_chunks(add_media_management(fixture()))
        strings = string_pool(changed[0][1])[0]
        self.assertEqual(changed[1:3], original[1:3])
        self.assertEqual(changed[-3:], original[-3:])
        added = changed[3:-3]
        self.assertEqual([strings[struct.unpack_from('<I', raw, 20)[0]] for kind, raw in added],
                         ['uses-permission', 'uses-permission'])
        self.assertEqual(struct.unpack_from('<H', added[0][1], 28)[0], 1)
        name = struct.unpack_from('<I', added[0][1], 52)[0]
        self.assertEqual(strings[name], PERMISSION)
        self.assertNotIn('android.permission.MANAGE_EXTERNAL_STORAGE', strings)

    def test_repeated_or_corrupt_manifest_refused(self):
        once = add_media_management(fixture())
        with self.assertRaises(ValueError):
            add_media_management(once)
        with self.assertRaises(ValueError):
            add_media_management(fixture()[:-1])

    def test_foreign_apk_refused_before_candidate_output(self):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as temporary:
            root = Path(temporary)
            apk = root / 'foreign.apk'
            apk.write_bytes(b'not the reviewed app')
            with self.assertRaisesRegex(ValueError, 'unreviewed Gallery predecessor'):
                prepare(apk, root / 'android', root / 'output.apk', root / 'work')
            self.assertFalse((root / 'work').exists())
            self.assertFalse((root / 'output.apk').exists())


class MediaGatePatchTests(unittest.TestCase):
    def test_scope_attachment_is_inside_original_failure_handler_before_task_execution(self):
        old = ('.method public final invokeSuspend(Ljava/lang/Object;)Ljava/lang/Object;\n'
               '    .registers 8\n    :try_start_16\n'
               '    sget-object v4, Lkotlin/Result;->Companion:Lkotlin/Result$Companion;\n'
               '    invoke-interface {p1}, Lcom/oplus/aiunit/vision/fe7;->run()V\n'
               '    return-object p1\n.end method\nunchanged following method\n')
        changed = patch_worker(old)
        self.assertLess(changed.index(':try_start_16'), changed.index(REPLACE))
        self.assertLess(changed.index(REPLACE), changed.index('fe7;->run()V'))
        self.assertTrue(changed.endswith('unchanged following method\n'))
        for invalid in [changed, old.replace('.registers 8', '.registers 7'),
                        old.replace(':try_start_16', ':try_start_other')]:
            with self.assertRaises(ValueError):
                patch_worker(invalid)

    def test_source_query_gate_uses_fresh_local_and_covers_both_returns(self):
        old = ('.method public run()V\n    .registers 10\n    return-void\n'
               '    invoke-virtual {v3, v0}, Lcom/oplus/aiunit/vision/k9b;->a(Landroid/database/Cursor;)V\n'
               '    invoke-interface {p0}, Ljava/io/Closeable;->close()V\n'
               '    :goto_b1\n    return-void\n.end method\n')
        changed = patch_query(old)
        self.assertEqual(changed.count('->authorize(Ljava/lang/Object;)V'), 2)
        self.assertIn('.registers 11', changed)
        self.assertLess(changed.index('Closeable;->close()V'), changed.rindex('->authorize'))
        for invalid in [changed, old.replace('.registers 10', '.registers 11'),
                        old.replace('    :goto_b1', '    move-object v9, p0\n    :goto_b1')]:
            with self.assertRaises(ValueError):
                patch_query(invalid)


@unittest.skipUnless((JDK / 'javac').exists(), 'requires build WSL JDK21')
class MediaPolicyTests(unittest.TestCase):
    def test_actual_flow_handles_management_per_operation_denial_and_late_callbacks(self):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as temporary:
            root = Path(temporary)
            probe = root / 'Probe.java'
            probe.write_text('''import realme.gallery.compat.MediaConsentFlow;
import static realme.gallery.compat.MediaConsentFlow.Step.*;
public class Probe {
 static void ok(boolean value) { if(!value) throw new AssertionError(); }
 public static void main(String[] args) {
  int cases=0;
  for(boolean management:new boolean[]{false,true}) for(boolean explained:new boolean[]{false,true}) {
   MediaConsentFlow f=new MediaConsentFlow();
   ok(f.begin(management,explained)==(management||explained?WRITE:OFFER));
   if(management||explained) {ok(!f.settings());ok(f.writeReturned());ok(!f.writeReturned());}
   else {ok(!f.writeReturned());ok(f.settings());ok(!f.settings());ok(f.settingsReturned(true));ok(f.writeReturned());}
   ok(f.step()==FINISHED);ok(f.begin(true,true)==FINISHED);cases++;
  }
  MediaConsentFlow declined=new MediaConsentFlow();declined.begin(false,false);
  ok(declined.settings());ok(!declined.settingsReturned(false));ok(!declined.writeReturned());
  ok(declined.step()==FINISHED);cases++;
  MediaConsentFlow once=new MediaConsentFlow();once.begin(false,false);
  ok(once.justThisChange());ok(!once.justThisChange());ok(once.writeReturned());cases++;
  for(MediaConsentFlow.Step stage:new MediaConsentFlow.Step[]{OFFER,SETTINGS,WRITE}) {
   MediaConsentFlow f=new MediaConsentFlow();f.begin(false,false);
   if(stage==SETTINGS)f.settings();if(stage==WRITE)f.justThisChange();f.cancel();
   ok(!f.settingsReturned(true));ok(!f.writeReturned());ok(!f.justThisChange());cases++;
  }
  System.out.print(cases);
 }
}''')
            subprocess.run([str(JDK / 'javac'), '--release', '8', '-d', str(root),
                            str(ROOT / 'tools/oem_gallery_compat/MediaConsentFlow.java'), str(probe)],
                           check=True, capture_output=True, timeout=30)
            self.assertEqual(subprocess.check_output([str(JDK / 'java'), '-cp', str(root), 'Probe'], text=True), '9')

    def test_selected_row_identity_rejects_alias_volume_owner_and_type_changes(self):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as temporary:
            root = Path(temporary)
            probe = root / 'Probe.java'
            probe.write_text('''import realme.gallery.compat.ReplaceMediaItem;
import java.util.*;
public class Probe {
 static void ok(boolean v) {if(!v)throw new AssertionError();}
 public static void main(String[] args)throws Exception {
  String base=new java.io.File(args[0]).getCanonicalPath(), path=base+"/DCIM/test.heic";
  Set<String> mounted=new HashSet<>(Arrays.asList("external_primary","ABCD-1234"));
  ReplaceMediaItem item=new ReplaceMediaItem(4,1,path,"external_primary","camera",base,mounted);
  ok(item.matches(4,1,path,"external_primary","camera"));
  ok(!item.matches(5,1,path,"external_primary","camera"));
  ok(!item.matches(4,3,path,"external_primary","camera"));
  ok(!item.matches(4,1,path,"ABCD-1234","camera"));
  ok(!item.matches(4,1,path,"external_primary","other"));
  int refused=0;
  for(String p:new String[]{base+"/../elsewhere/test.jpg",base+"2/test.jpg",base+"/DCIM/../test.jpg","relative",null})
   try {new ReplaceMediaItem(4,1,p,"external_primary",null,base,mounted);throw new AssertionError();}
   catch(IllegalArgumentException expected){refused++;}
  for(String volume:new String[]{"external","internal","missing","../escape"})
   try {new ReplaceMediaItem(4,1,path,volume,null,base,mounted);throw new AssertionError();}
   catch(IllegalArgumentException expected){refused++;}
  for(int type:new int[]{0,2,4})
   try {new ReplaceMediaItem(4,type,path,"external_primary",null,base,mounted);throw new AssertionError();}
   catch(IllegalArgumentException expected){refused++;}
  try {new ReplaceMediaItem(0,1,path,"external_primary",null,base,mounted);throw new AssertionError();}
  catch(IllegalArgumentException expected){refused++;}
  ok(new ReplaceMediaItem(4,1,path,"external_primary",null,base,mounted).matches(4,1,path,"external_primary",null));
  System.out.print(refused);
 }
}''')
            subprocess.run([str(JDK / 'javac'), '--release', '8', '-d', str(root),
                            str(ROOT / 'tools/oem_gallery_compat/ReplaceMediaItem.java'), str(probe)],
                           check=True, capture_output=True, timeout=30)
            self.assertEqual(subprocess.check_output([str(JDK / 'java'), '-cp', str(root), 'Probe', str(root)], text=True), '13')

    def test_real_scope_interface_binding_rejects_cancelled_missing_and_foreign_jobs(self):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as temporary:
            root = Path(temporary)
            sources = {
                'kotlin/coroutines/CoroutineContext.java': '''package kotlin.coroutines;
public interface CoroutineContext { interface Key {} Object get(Key key); }''',
                'kotlinx/coroutines/CoroutineScope.java': '''package kotlinx.coroutines;
public interface CoroutineScope { kotlin.coroutines.CoroutineContext getCoroutineContext(); }''',
                'kotlinx/coroutines/Job.java': '''package kotlinx.coroutines;
public interface Job { kotlin.coroutines.CoroutineContext.Key Key=new kotlin.coroutines.CoroutineContext.Key(){}; boolean isActive(); }''',
                'Probe.java': '''import realme.gallery.compat.MediaSaveScope;
import kotlinx.coroutines.*; import kotlin.coroutines.*;
public class Probe {
 public static class Context implements CoroutineContext {Object job; Context(Object j){job=j;} public Object get(Key k){if(k!=Job.Key)throw new AssertionError();return job;}}
 public static class Scope implements CoroutineScope {CoroutineContext c;Scope(Object j){c=new Context(j);}public CoroutineContext getCoroutineContext(){return c;}}
 static void ok(boolean v){if(!v)throw new AssertionError();}
 public static void main(String[] args){
  ok(MediaSaveScope.active(new Scope((Job)()->true)));
  ok(!MediaSaveScope.active(new Scope((Job)()->false)));
  ok(!MediaSaveScope.active(new Scope(null)));ok(!MediaSaveScope.active(new Scope("foreign")));
  ok(!MediaSaveScope.active(null));ok(!MediaSaveScope.active(new Object()));
  Thread.currentThread().interrupt();ok(!MediaSaveScope.active(new Scope((Job)()->true)));Thread.interrupted();
  System.out.print(7);
 }}'''}
            files = []
            for name, text in sources.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
                files.append(str(path))
            subprocess.run([str(JDK / 'javac'), '--release', '8', '-d', str(root),
                            str(ROOT / 'tools/oem_gallery_compat/MediaSaveScope.java'), *files],
                           check=True, capture_output=True, timeout=30)
            self.assertEqual(subprocess.check_output([str(JDK / 'java'), '-cp', str(root), 'Probe'], text=True), '7')

    @unittest.skipUnless(SDK.is_file(), 'requires Android31 public SDK')
    def test_android_helpers_compile_against_original_minimum_public_api(self):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as temporary:
            p = subprocess.run([str(JDK / 'javac'), '--release', '8', '-cp', str(SDK), '-d', temporary,
                                str(ROOT / 'tools/oem_gallery_compat/MoveConsentState.java'), *map(str, SOURCES)],
                               capture_output=True, text=True, timeout=45)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    @unittest.skipUnless(SDK.is_file(), 'requires Android31 public SDK')
    def test_real_replace_gate_leaves_save_copy_independent_of_scope_or_media_access(self):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as temporary:
            root = Path(temporary)
            stubs = {
                'android/os/Build.java': '''package android.os;
public class Build {public static class VERSION {public static int SDK_INT=36;}}''',
                'com/oplus/aiunit/vision/fid.java': '''package com.oplus.aiunit.vision;
public class fid {public java.util.Map<String,Object> a=new java.util.HashMap<>();}''',
                'com/oplus/gallery/photoeditor/editingvvm/output/OriginFileProcessType.java': '''package com.oplus.gallery.photoeditor.editingvvm.output;
public enum OriginFileProcessType { NONE, RECYCLE, DELETE }''',
                'Probe.java': '''import realme.gallery.compat.ReplaceOriginalAccess;
import com.oplus.aiunit.vision.fid;
import com.oplus.gallery.photoeditor.editingvvm.output.OriginFileProcessType;
public class Probe {public static void main(String[] args) {
 fid data=new fid();data.a.put("KEY_ORIGIN_FILE_PROCESS_TYPE",OriginFileProcessType.NONE);
 java.util.Map<String,Object> before=new java.util.HashMap<>(data.a);
 ReplaceOriginalAccess.authorize(data);
 if(!before.equals(data.a))throw new AssertionError("Save copy changed data");
 for(OriginFileProcessType type:new OriginFileProcessType[]{OriginFileProcessType.RECYCLE,OriginFileProcessType.DELETE}) {
  data.a.put("KEY_ORIGIN_FILE_PROCESS_TYPE",type);before=new java.util.HashMap<>(data.a);
  try {ReplaceOriginalAccess.authorize(data);throw new AssertionError("unbound replacement allowed");}
  catch(SecurityException expected) {}
  if(!before.equals(data.a))throw new AssertionError("refusal changed data");
 }System.out.print(3);
}}'''}
            files = []
            for name, text in stubs.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
                files.append(str(path))
            p = subprocess.run([str(JDK / 'javac'), '--release', '8', '-cp', str(SDK), '-d', str(root),
                                str(ROOT / 'tools/oem_gallery_compat/MoveConsentState.java'),
                                *map(str, SOURCES), *files], capture_output=True, text=True, timeout=45)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            p = subprocess.run([str(JDK / 'java'), '-cp', str(root) + os.pathsep + str(SDK), 'Probe'],
                               capture_output=True, text=True, timeout=30)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertEqual(p.stdout, '3')


if __name__ == '__main__':
    unittest.main()
