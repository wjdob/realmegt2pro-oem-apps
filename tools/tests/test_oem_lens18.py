"""Exercise the actual keyed Lens binder against the audited OEM method contract."""
from pathlib import Path
import ast
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tools'))
import oem_camera_gallery as gallery
from test_oem_formats17 import FAKES as BASE_FAKES

DIRECTORY=ROOT/'tools/oem_camera_compat/lens18/realme/camera/compat'
FAKES={**BASE_FAKES,
 'android/content/res/Resources.java':'''package android.content.res;public class Resources {
 public boolean foreign;public int getIdentifier(String n,String t,String p){return "oplus_setting_bar".equals(n)?43:"google_lens_id".equals(n)?42:0;}
 public String getResourceName(int id){return id==43?(foreign?"other:id/oplus_setting_bar":"com.oplus.camera:id/oplus_setting_bar"):id==42?"com.oplus.camera:id/google_lens_id":"other:id/unknown";}}''',
 'android/view/ViewParent.java':'''package android.view;public interface ViewParent {}''',
 'android/view/View.java':'''package android.view;public class View implements ViewParent {
 public interface OnClickListener{void onClick(View v);}public int id=-1;public boolean shown=true,enabled=true;
 public ViewParent parent;public OnClickListener click;public Object touch=new Object();
 public boolean clickable=true;public boolean isClickable(){return clickable;}
 public int getId(){return id;}public ViewParent getParent(){return parent;}
 public boolean isShown(){return shown;}public boolean isEnabled(){return enabled;}
 public void setOnClickListener(OnClickListener c){click=c;}public void performClick(){if(click!=null)click.onClick(this);}}''',
 'android/app/Activity.java':'''package android.app;public class Activity extends android.content.Context {
 public boolean destroyed,finishing,awake=true;public android.view.View root,decoy;
 public ClassLoader loader=Activity.class.getClassLoader();public final android.content.res.Resources resources=new android.content.res.Resources();
 public Activity(String p){super(p);}public boolean isDestroyed(){return destroyed;}
 public android.content.res.Resources getResources(){return resources;}public ClassLoader getClassLoader(){return loader;}
 public android.view.View findViewById(int id){return root!=null&&root.getId()==id?root:decoy!=null&&decoy.getId()==id?decoy:null;}}''',
 'android/widget/Toast.java':'''package android.widget;public class Toast {public static int shown;public static final int LENGTH_SHORT=0;
 public static Toast makeText(android.content.Context c,String s,int n){return new Toast();}public void show(){shown++;}}''',
 'rj/l.java':'''package rj;public class l {public String key;public android.view.View view;public boolean failure,d;public Object j;
 public l(String k,android.view.View v){key=k;view=v;}public String x(){return key;}
 public android.view.View q(){if(failure)throw new IllegalStateException("unavailable");return view;}}''',
 'tj/h.java':'''package tj;public class h extends rj.l implements android.view.View.OnClickListener {
 public int clicks;public h(String k,android.view.View v){super(k,v);v.setOnClickListener(this);}public void onClick(android.view.View v){clicks++;}}''',
 'zj/y.java':'''package zj;public class y extends android.view.View {public boolean pending=true;public int resets;
 public void setMbClickUpdate(boolean value){pending=value;resets++;}}''',
 'com/oplus/camera/ui/setting/menu/CameraSettingMenuPanel.java':'''package com.oplus.camera.ui.setting.menu;
 public class CameraSettingMenuPanel extends android.view.View {public final java.util.Map<String,rj.l> options=new java.util.HashMap<>();
 public rj.l alias;public boolean allow=true,failure;public int gates;
 public rj.l j(String key){return alias!=null?alias:options.get(key);}
 public boolean v(String key,boolean first,boolean second){gates++;if(first||second||!"key_show_googlelens_icon".equals(key))throw new AssertionError();
 if(failure)throw new IllegalStateException();return allow;}
 public void add(rj.l item){options.put(item.key,item);item.view.parent=this;item.j=this;}}''',
 'realme/camera/compat/GalleryShortcut.java':'''package realme.camera.compat;public class GalleryShortcut {
 public static int launches;public static boolean available=true;
 static boolean unlocked(android.app.Activity a){return a!=null&&!a.destroyed&&!a.finishing&&a.awake;}
 public static boolean launchLens(android.app.Activity a){if(!unlocked(a)||!available)return false;launches++;return true;}}''',
}


@unittest.skipUnless(shutil.which('javac') and shutil.which('java'),'JDK required')
class Lens18Tests(unittest.TestCase):
    def integration_sources(self, fixtures):
        return fixtures

    def test_real_gallery_bridge_reaches_keyed_button_and_preserves_review_protocol(self):
        # Reuse the historical public-API fixture, then replace its synthetic
        # Lens ID with the native keyed/no-ID control found in the pinned APK.
        tree=ast.parse((ROOT/'tools/tests/test_oem_camera_gallery.py').read_text())
        assignment=next(n for n in ast.walk(tree) if isinstance(n,ast.Assign)
                        and any(isinstance(t,ast.Name) and t.id=='fixtures' for t in n.targets))
        fixtures=ast.literal_eval(assignment.value)
        tree=ast.parse((ROOT/'tools/tests/test_oem_camera_lens16.py').read_text())
        for node in ast.walk(tree):
            if not isinstance(node,ast.Assign) or len(node.targets)!=1:continue
            target=node.targets[0]
            if not isinstance(target,ast.Subscript) or not isinstance(target.value,ast.Name) or target.value.id!='fixtures':continue
            name=ast.literal_eval(target.slice)
            if isinstance(node.value,ast.Constant):fixtures[name]=ast.literal_eval(node.value)
            elif isinstance(node.value,ast.Call) and isinstance(node.value.func,ast.Attribute) and node.value.func.attr=='replace':
                fixtures[name]=fixtures[name].replace(*(ast.literal_eval(a) for a in node.value.args))
        for name in ['android/view/ViewParent.java','android/util/Log.java','rj/l.java','tj/h.java','zj/y.java',
                     'com/oplus/camera/ui/setting/menu/CameraSettingMenuPanel.java']:
            fixtures[name]=FAKES[name]
        fixtures['android/view/View.java']=fixtures['android/view/View.java'].replace('public class View {','public class View implements ViewParent {').replace(
            'public int id=', 'public ViewParent parent;public ViewParent getParent(){return parent;}public boolean isEnabled(){return true;}public boolean isClickable(){return true;}public int id=')
        fixtures['android/content/res/Resources.java']=FAKES['android/content/res/Resources.java'].replace(
            '"other:id/unknown"', 'id==0x7f09062f?"com.oplus.camera:id/thumbnail":id==0x7f090497?"com.oplus.camera:id/popup_thumbnail":"other:id/unknown"')
        fixtures['android/app/Activity.java']=fixtures['android/app/Activity.java'].replace(
            'public final View lens=new View();', '''public final zj.y lens=new zj.y();
 public final com.oplus.camera.ui.setting.menu.CameraSettingMenuPanel panel=new com.oplus.camera.ui.setting.menu.CameraSettingMenuPanel();
 public final tj.h option=new tj.h("key_show_googlelens_icon",lens);
 public ClassLoader getClassLoader(){return Activity.class.getClassLoader();}''').replace(
            'lens.id=42;', 'lens.id=-1;panel.id=43;panel.add(option);').replace(
            'return id==42?lens:', 'return id==43?panel:')
        fixtures['realme/camera/compat/EncoderMenuCompatibility.java']='''package realme.camera.compat;
 final class EncoderMenuCompatibility {static boolean reviewedApk(android.app.Activity a){return true;}static void apply(android.app.Activity a){}}'''
        fixtures['Harness.java']=fixtures['Harness.java'].replace(
            'l.lens.performClick();require(l.launched==1',
            'require(l.lens.id==-1,"actual Lens has no resource ID");l.lens.performClick();require(l.launched==1')
        fixtures=self.integration_sources(fixtures)
        with tempfile.TemporaryDirectory(prefix='lens-integration-') as directory:
            root=Path(directory);inputs=[]
            for name,source in fixtures.items():
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(source);inputs.append(path)
            inputs += [DIRECTORY/name for name in ('GalleryShortcut.java','CaptureThumbnail.java','LensMenuBinding.java')]
            result=subprocess.run(['javac','--release','17','-d',str(root/'out'),*map(str,inputs)],capture_output=True,text=True,timeout=60)
            self.assertEqual(result.returncode,0,result.stderr)
            result=subprocess.run(['java','-Xmx256m','-cp',str(root/'out'),'Harness'],capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertTrue(json.loads(result.stdout)['single_uri'])

    def test_actual_native_key_binding_lifecycle_and_refusals(self):
        with tempfile.TemporaryDirectory(prefix='lens-key-') as directory:
            root=Path(directory);inputs=[]
            for name,text in FAKES.items():
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text);inputs.append(path)
            inputs += [DIRECTORY/'LensMenuBinding.java',DIRECTORY/'EncoderMenuCompatibility.java',ROOT/'tools/tests/java/LensMenuBindingTest.java']
            result=subprocess.run(['javac','--release','17','-d',str(root/'out'),*map(str,inputs)],capture_output=True,text=True,timeout=60)
            self.assertEqual(result.returncode,0,result.stderr)
            result=subprocess.run(['java','-Xmx256m','-cp',str(root/'out'),'realme.camera.compat.LensMenuBindingTest'],capture_output=True,text=True,timeout=60)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn('Native Lens cases passed: 24',result.stdout)

    def test_modern_google_route_and_gallery_protocol_source_remain_exact(self):
        before=(ROOT/'tools/oem_camera_compat/formats17/realme/camera/compat/GalleryShortcut.java').read_text()
        after=(DIRECTORY/'GalleryShortcut.java').read_text()
        # The actual URI-launch implementation already has route/fallback tests.
        # Keep that complete method byte-identical while fixing its native binding.
        def method(text,start,end):return text[text.index(start):text.index(end,text.index(start))]
        self.assertEqual(method(before,'    public static boolean launchLens(','    @Override public void onGlobalLayout'),method(after,'    public static boolean launchLens(','    @Override public void onGlobalLayout'))
        self.assertEqual((ROOT/'tools/oem_camera_compat/formats17/realme/camera/compat/CaptureThumbnail.java').read_bytes(),(DIRECTORY/'CaptureThumbnail.java').read_bytes())
        marker='    public static Intent createViewIntent('
        self.assertEqual(before[before.index(marker):],after[after.index(marker):])

    def test_previous_adapter_recipe_source_hashes_are_preserved(self):
        for name in ['oem-camera-lens16-23.2.json','oem-camera-formats17-23.2.json']:
            contract=json.loads((ROOT/'configs'/name).read_text())
            self.assertEqual({str(p.relative_to(ROOT)):hashlib.sha256(p.read_text().encode()).hexdigest() for p in gallery.sources(contract)},contract['sources_sha256'])


if __name__=='__main__':unittest.main()
