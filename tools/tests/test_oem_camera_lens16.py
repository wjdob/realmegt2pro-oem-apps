"""Exercise the post-capture window and viewer protocol that failed on-device."""
from pathlib import Path
import ast
import json
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'tools'))
import oem_camera_gallery as gallery

JDK = Path('/build/lineage24-store/tree/prebuilts/jdk/jdk21/linux-x86/bin')
CONTRACT = {'adapter_source_directory': 'tools/oem_camera_compat/lens16/realme/camera/compat'}


@unittest.skipUnless((JDK/'javac').is_file(), 'requires WSL realme-build JDK21')
class Lens16ProtocolTests(unittest.TestCase):
    def test_popup_capture_review_fallback_grants_lock_and_native_ownership(self):
        # Reuse the older lifecycle fixture's ordinary Android API model; keep
        # the historical tests and adapter source unchanged/reproducible.
        tree = ast.parse((ROOT/'tools/tests/test_oem_camera_gallery.py').read_text())
        assignment = next(n for n in ast.walk(tree) if isinstance(n, ast.Assign)
                          and any(isinstance(t, ast.Name) and t.id == 'fixtures' for t in n.targets))
        fixtures = ast.literal_eval(assignment.value)
        fixtures['android/view/View.java'] = '''package android.view;
public class View {
 public interface OnClickListener{void onClick(View v);}public boolean focused=true,shown=true;
 public int id=0x7f09062f;public Object token=new Object();public View child;public OnClickListener click;
 public final ViewTreeObserver observer=new ViewTreeObserver();public int getId(){return id;}
 public boolean hasWindowFocus(){return focused;}public boolean isShown(){return shown;}
 public Object getApplicationWindowToken(){return token;}
 public View findViewById(int wanted){return id==wanted?this:child!=null&&child.id==wanted?child:null;}
 public ViewTreeObserver getViewTreeObserver(){return observer;}
 public void setOnClickListener(OnClickListener listener){click=listener;}
 public void performClick(){if(click!=null)click.onClick(this);}
}'''
        fixtures['android/view/inspector/WindowInspector.java'] = '''package android.view.inspector;
public class WindowInspector {
 public static final java.util.List<android.view.View> roots=new java.util.ArrayList<>();
 public static java.util.List<android.view.View> getGlobalWindowViews(){return new java.util.ArrayList<>(roots);}
}'''
        fixtures['android/content/res/Resources.java'] = '''package android.content.res;
public class Resources {public int getIdentifier(String n,String t,String p){return "google_lens_id".equals(n)?42:0;}
public String getResourceName(int id){if(id==42)return "com.oplus.camera:id/google_lens_id";return id==0x7f09062f?
 "com.oplus.camera:id/thumbnail":id==0x7f090497?"com.oplus.camera:id/popup_thumbnail":"other:id/view";}}'''
        fixtures['android/app/Activity.java'] = '''package android.app;
import android.content.*;import android.view.*;import android.content.res.Resources;
public class Activity extends Context {
 public boolean destroyed,finishing,reviewUnsupported,lensRace,lensDenied;public int launched;public String pkg="com.oplus.camera";
 public final KeyguardManager keyguard=new KeyguardManager();public final Window window=new Window();
 public final View lens=new View(); public final android.content.pm.PackageManager pm=new android.content.pm.PackageManager();
 public android.content.pm.PackageManager getPackageManager(){return pm;}
 public final android.widget.ImageView image=new android.widget.ImageView();public Intent last;
 public Activity(){lens.id=42;image.token=window.view.token;}
 public boolean isDestroyed(){return destroyed;}public boolean isFinishing(){return finishing;}
 public String getPackageName(){return pkg;}public Window getWindow(){return window;}
 public <T> T getSystemService(Class<T> type){return type.cast(keyguard);}
 public Resources getResources(){return new Resources();}
 public View findViewById(int id){return id==42?lens:id==0x7f09062f?image:null;}
 public void startActivity(Intent i){if(i.data!=null&&i.data.toString().startsWith("google:")){
 if(lensDenied)throw new SecurityException();if(lensRace)throw new ActivityNotFoundException();}
 if(reviewUnsupported&&android.provider.MediaStore.ACTION_REVIEW.equals(i.action))
 throw new ActivityNotFoundException();launched++;last=i;}
 public void focus(boolean b){window.view.focused=b;window.view.observer.dispatch(b);}
}'''
        fixtures['android/content/Intent.java'] = '''package android.content;import android.net.Uri;
public class Intent {
 public static final String ACTION_VIEW="android.intent.action.VIEW";public static final int FLAG_GRANT_READ_URI_PERMISSION=1;
 public String action,mime,pkg;public Uri data;public int flags;public ClipData clip;
 public Intent(String a){action=a;}public Intent setData(Uri u){data=u;return this;}
 public Intent setPackage(String p){pkg=p;return this;}
 public android.content.ComponentName resolveActivity(android.content.pm.PackageManager p){return p.resolve(this);}public Intent setAction(String a){action=a;return this;}
 public Intent setDataAndType(Uri u,String t){data=u;mime=t;return this;}
 public Intent addFlags(int f){flags|=f;return this;}public void setClipData(ClipData c){clip=c;}
}'''
        fixtures['android/content/ComponentName.java'] = 'package android.content; public class ComponentName {}'
        fixtures['android/content/pm/PackageManager.java'] = '''package android.content.pm;import android.content.*;
public class PackageManager {public boolean modern=true,legacy=true;
 public ComponentName resolve(Intent i){return ((modern&&"google://lens".equals(i.data.toString()))||
 (legacy&&"googleapp://lens".equals(i.data.toString())))?new ComponentName():null;}}'''
        fixtures['android/provider/MediaStore.java'] = fixtures['android/provider/MediaStore.java'].replace(
            'public class MediaStore {', 'public class MediaStore {public static final String ACTION_REVIEW="com.android.camera.action.REVIEW";')
        fixtures['android/graphics/Bitmap.java'] = '''package android.graphics;
public class Bitmap {
 public final long id;public final int width,height;public final int cropX,cropY;
 public Bitmap(long id){this(id,id==95?180:320,id==95?320:180,0,0);}
 private Bitmap(long id,int w,int h,int x,int y){this.id=id;width=w;height=h;cropX=x;cropY=y;}
 public int getWidth(){return width;}public int getHeight(){return height;}
 public static Bitmap createBitmap(Bitmap b,int x,int y,int w,int h){
 if(x<0||y<0||w<=0||h<=0||x+w>b.width||y+h>b.height)throw new IllegalArgumentException("crop");
 return new Bitmap(b.id,w,h,x,y);}
}'''
        fixtures['Harness.java'] = '''import android.app.*;import android.os.*;import android.view.*;
import android.view.inspector.*;import android.graphics.*;import android.content.*;import realme.camera.compat.*;
public class Harness {
 static void require(boolean ok,String msg){if(!ok)throw new AssertionError(msg);}
 static void settle()throws Exception{for(int i=0;i<70;i++){Thread.sleep(3);Handler.flush(0);}}
 static void refresh()throws Exception{Handler.flush(250);settle();}
 static View popup(Activity a){View root=new View();root.id=0;root.token=a.window.view.token;
 View thumb=new View();thumb.id=0x7f090497;thumb.token=root.token;root.child=thumb;return root;}
 public static void main(String[] args)throws Exception{
 Activity a=new Activity();GalleryShortcut g=new GalleryShortcut(a);g.bind();refresh();
 require(a.image.getDrawable().bitmap.width==a.image.getDrawable().bitmap.height,"square landscape restoration");
 require(a.image.getDrawable().bitmap.cropX==70,"center crop, not stretch");
 // The popup is a separate root; Activity.findViewById cannot see it.
 View root=popup(a);WindowInspector.roots.add(root);final int[] stock={0};root.child.setOnClickListener(v->stock[0]++);
 Activity foreign=new Activity();View wrong=popup(foreign);WindowInspector.roots.add(wrong);
 View.OnClickListener unrelated=v->stock[0]+=100;wrong.child.setOnClickListener(unrelated);
 a.resolver.newest=95;a.resolver.media=3;a.resolver.mime="video/mp4";
 a.image.setImageBitmap(new Bitmap(95));int nativeSets=a.image.sets;
 a.resolver.observer.onChange(false);refresh();
 require(a.image.sets==nativeSets,"preserve newly landed native video thumbnail");
 require(wrong.child.click==unrelated,"do not bind another Activity's popup");
 root.child.performClick();settle();require(stock[0]==0&&a.launched==1,"popup uses bridge immediately after capture");
 require(android.provider.MediaStore.ACTION_REVIEW.equals(a.last.action),"prefer standard review for album browsing");
 require(a.last.data.toString().endsWith("/video/media/95"),"newest owned video URI");
 require(a.last.clip==null&&a.last.flags==Intent.FLAG_GRANT_READ_URI_PERMISSION,"one URI/read-only grant, no duplicate ClipData");
 a.reviewUnsupported=true;root.child.performClick();settle();
 require(a.launched==2&&Intent.ACTION_VIEW.equals(a.last.action),"plain-view fallback for other galleries");
 require(a.last.clip==null,"fallback must not duplicate URI");
 a.keyguard.locked=true;int q=a.resolver.queries;root.child.performClick();settle();
 require(a.launched==2&&a.resolver.queries==q,"locked popup cannot query or open capture");
 a.keyguard.locked=false;a.focus(false);a.focus(true);refresh();
 require(a.image.getDrawable().bitmap.width==180&&a.image.getDrawable().bitmap.height==180,"square portrait video restoration after resume");
 require(a.image.getDrawable().bitmap.cropY==70,"center crop portrait");
 a.resolver.block=true;root.child.performClick();Thread.sleep(20);g.close();a.resolver.block=false;settle();
 require(a.launched==2,"closed bridge rejects delayed viewer request");
 require(a.resolver.registered==0,"observer cleanup");
 try{GalleryShortcut.createViewIntent(android.net.Uri.parse("content://other/private/1"),"image/jpeg");
 throw new AssertionError("foreign URI accepted");}catch(IllegalArgumentException expected){}
 Activity l=new Activity();GalleryShortcut bridge=new GalleryShortcut(l);bridge.bind();
 l.lens.performClick();require(l.launched==1&&"google://lens".equals(l.last.data.toString()),"bound Lens button uses modern URI");
 require("com.google.android.googlequicksearchbox".equals(l.last.pkg)&&l.last.flags==0&&l.last.clip==null,"package-scoped route without grants");
 l.pm.modern=false;l.lens.performClick();require(l.launched==2&&"googleapp://lens".equals(l.last.data.toString()),"legacy handler fallback");
 l.pm.legacy=false;l.lens.performClick();require(l.launched==2,"Google-free no launch");
 l.pm.modern=true;l.pm.legacy=true;l.lensRace=true;l.lens.performClick();
 require(l.launched==3&&"googleapp://lens".equals(l.last.data.toString()),"handler removal race fallback");
 l.lensRace=false;l.lensDenied=true;l.lens.performClick();require(l.launched==3,"security denial stops fallback");l.lensDenied=false;
 l.keyguard.locked=true;l.lens.performClick();require(l.launched==3,"locked Lens blocked");l.keyguard.locked=false;
 l.focus(false);l.lens.performClick();require(l.launched==3,"background Lens blocked");l.focus(true);
 l.destroyed=true;require(!GalleryShortcut.launchLens(l),"destroyed Activity blocked");l.destroyed=false;
 l.finishing=true;require(!GalleryShortcut.launchLens(l),"finishing Activity blocked");l.finishing=false;
 l.pkg="other.app";require(!GalleryShortcut.launchLens(l),"other app blocked");l.pkg="com.oplus.camera";
 require(!GalleryShortcut.launchLens(null),"null Activity blocked");
 bridge.close();l.lens.performClick();require(l.launched==3,"closed hook blocked");
 bridge.bind();l.lens.performClick();require(l.launched==4,"rebind after resume");bridge.close();
 System.out.println("{\\"protocol_cases\\":10,\\"popup\\":true,\\"single_uri\\":true,\\"review_fallback\\":true}");
 }}'''
        work = ROOT/'tmp/gallery13-tests'
        work.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=work) as directory:
            root = Path(directory)
            for name, source in fixtures.items():
                path = root/name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(source)
            result = subprocess.run([str(JDK/'javac'), '-d', str(root/'out'),
                *(str(p) for p in root.rglob('*.java')),
                *(str(p) for p in gallery.sources(CONTRACT)[-2:])], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(JDK/'java'), '-cp', str(root/'out'), 'Harness'],
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(result.stdout)['single_uri'])

    def test_contract_rejects_arbitrary_source_directory(self):
        with self.assertRaises(ValueError):
            gallery.sources({'adapter_source_directory': '../../outside'})


if __name__ == '__main__':
    unittest.main()
