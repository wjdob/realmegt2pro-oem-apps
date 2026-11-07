"""Exercise real optional bridge source across lifecycle and MediaStore boundaries."""
from pathlib import Path
import json
import sqlite3
import subprocess
import tempfile
import unittest

import oem_camera_gallery as gallery

JDK = Path('/build/lineage/prebuilts/jdk/jdk21/linux-x86/bin')


@unittest.skipUnless((JDK/'javac').is_file(), 'requires WSL realme-build JDK21')
class GalleryLifecycleTests(unittest.TestCase):
    def test_latest_capture_reopen_observer_lock_and_stale_delivery(self):
        fixtures = {
            'android/os/Looper.java': '''package android.os; public class Looper {
 public static Looper getMainLooper(){return new Looper();}}''',
            'android/os/Handler.java': '''package android.os; import java.util.*;
public class Handler {
 static long now; static final List<Job> jobs=new ArrayList<>();
 static class Job {Runnable r;long due;Job(Runnable r,long due){this.r=r;this.due=due;}}
 public Handler(Looper l){} public boolean post(Runnable r){return postDelayed(r,0);}
 public boolean postDelayed(Runnable r,long delay){synchronized(jobs){jobs.add(new Job(r,now+delay));}return true;}
 public void removeCallbacks(Runnable r){synchronized(jobs){jobs.removeIf(j->j.r==r);}}
 public static void flush(long advance){now+=advance; for(int n=0;n<500;n++){
 Job run=null;synchronized(jobs){for(Job j:jobs)if(j.due<=now){run=j;break;}if(run!=null)jobs.remove(run);}
 if(run==null)return;run.r.run();}throw new AssertionError("unbounded callback loop");}
}''',
            'android/os/Bundle.java': '''package android.os; import java.util.*;
public class Bundle {Map<String,Object> x=new HashMap<>();
 public void putString(String k,String v){x.put(k,v);} public String getString(String k){return (String)x.get(k);}
 public void putStringArray(String k,String[] v){x.put(k,v);} public String[] getStringArray(String k){return (String[])x.get(k);}
 public void putInt(String k,int v){x.put(k,v);} public int getInt(String k){return (Integer)x.get(k);}}
''',
            'android/os/CancellationSignal.java': '''package android.os; public class CancellationSignal {
 volatile boolean canceled;public void cancel(){canceled=true;}public boolean isCanceled(){return canceled;}}''',
            'android/app/KeyguardManager.java': '''package android.app; public class KeyguardManager {
 public boolean locked;public boolean isKeyguardLocked(){return locked;}public boolean isDeviceLocked(){return locked;}}''',
            'android/content/Context.java': '''package android.content; public class Context {
 public ContentResolver resolver=new ContentResolver();public ContentResolver getContentResolver(){return resolver;}
 public Context getApplicationContext(){return this;}}
''',
            'android/app/Activity.java': '''package android.app; import android.content.*;import android.view.*;import android.content.res.Resources;
public class Activity extends Context {
 public boolean destroyed,finishing; public int launched;public String pkg="com.oplus.camera";
 public final KeyguardManager keyguard=new KeyguardManager();public final Window window=new Window();
 public final android.widget.ImageView image=new android.widget.ImageView();
 public boolean isDestroyed(){return destroyed;}public boolean isFinishing(){return finishing;}
 public String getPackageName(){return pkg;}public Window getWindow(){return window;}
 public <T> T getSystemService(Class<T> type){return type.cast(keyguard);}
 public Resources getResources(){return new Resources();}public View findViewById(int id){return image;}
 public void startActivity(Intent i){launched++;}
 public void focus(boolean b){window.view.focused=b;window.view.observer.dispatch(b);}
}''',
            'android/content/res/Resources.java': '''package android.content.res;public class Resources {
 public String getResourceName(int id){return id==0x7f09062f?"com.oplus.camera:id/thumbnail":"other:id/view";}}''',
            'android/view/Window.java': '''package android.view;public class Window {
 public final View view=new View();public View getDecorView(){return view;}}''',
            'android/view/View.java': '''package android.view;public class View {
 public interface OnClickListener{void onClick(View v);}public boolean focused=true;public int id=0x7f09062f;
 public final ViewTreeObserver observer=new ViewTreeObserver();public int getId(){return id;}
 public boolean hasWindowFocus(){return focused;}public ViewTreeObserver getViewTreeObserver(){return observer;}
 public void setOnClickListener(OnClickListener listener){}
}''',
            'android/view/ViewTreeObserver.java': '''package android.view;import java.util.*;
public class ViewTreeObserver {
 public interface OnWindowFocusChangeListener{void onWindowFocusChanged(boolean focus);}
 public interface OnGlobalLayoutListener{void onGlobalLayout();}
 public final Set<OnWindowFocusChangeListener> focus=new HashSet<>();public final Set<OnGlobalLayoutListener> layout=new HashSet<>();
 public boolean isAlive(){return true;}public void addOnWindowFocusChangeListener(OnWindowFocusChangeListener l){focus.add(l);}
 public void removeOnWindowFocusChangeListener(OnWindowFocusChangeListener l){focus.remove(l);}
 public void addOnGlobalLayoutListener(OnGlobalLayoutListener l){layout.add(l);}
 public void removeOnGlobalLayoutListener(OnGlobalLayoutListener l){layout.remove(l);}
 public void dispatch(boolean b){for(OnWindowFocusChangeListener l:new ArrayList<>(focus))l.onWindowFocusChanged(b);}
}''',
            'android/graphics/Bitmap.java': '''package android.graphics;public class Bitmap {
 public long id;public final int width,height;public Bitmap(long id){this.id=id;
 width=id==95?180:320;height=id==95?320:180;}}''',
            'android/graphics/drawable/Drawable.java': '''package android.graphics.drawable;public class Drawable {
 public android.graphics.Bitmap bitmap;public Drawable(android.graphics.Bitmap b){bitmap=b;}}''',
            'android/widget/ImageView.java': '''package android.widget;import android.graphics.*;import android.graphics.drawable.*;
public class ImageView extends android.view.View {
 public enum ScaleType{FIT_XY,CENTER_CROP}public ScaleType scale=ScaleType.FIT_XY;
 public double renderedWidth,renderedHeight;
 public void setScaleType(ScaleType value){scale=value;}
 public Drawable drawable;public int sets;public Drawable getDrawable(){return drawable;}
 public void setImageBitmap(Bitmap b){sets++;drawable=new Drawable(b);
 if(scale==ScaleType.CENTER_CROP){double s=Math.max(80.0/b.width,80.0/b.height);
 renderedWidth=b.width*s;renderedHeight=b.height*s;}else{renderedWidth=80;renderedHeight=80;}}
 public void setImageDrawable(Drawable d){drawable=d;}}
''',
            'android/util/Log.java': '''package android.util;public class Log {public static int w(String t,String m){return 0;}}''',
            'android/util/Size.java': '''package android.util;public class Size {public int w,h;public Size(int w,int h){this.w=w;this.h=h;}}''',
            'android/net/Uri.java': '''package android.net;public class Uri {
 final java.net.URI u;private Uri(String s){u=java.net.URI.create(s);}public static Uri parse(String s){return new Uri(s);}
 public String getScheme(){return u.getScheme();}public String getAuthority(){return u.getAuthority();}
 public String getPath(){return u.getPath();}public String getQuery(){return u.getQuery();}public String getFragment(){return u.getFragment();}
 public String toString(){return u.toString();}}
''',
            'android/content/ContentUris.java': '''package android.content;public class ContentUris {
 public static android.net.Uri withAppendedId(android.net.Uri base,long id){return android.net.Uri.parse(base+"/"+id);}}''',
            'android/provider/MediaStore.java': '''package android.provider;import android.net.Uri;
public class MediaStore {public static final String VOLUME_EXTERNAL_PRIMARY="external_primary";
 public static class Files{public static Uri getContentUri(String v){return Uri.parse("content://media/"+v+"/file");}}
 public static class Images{public static class Media{public static Uri getContentUri(String v){return Uri.parse("content://media/"+v+"/images/media");}}}
 public static class Video{public static class Media{public static Uri getContentUri(String v){return Uri.parse("content://media/"+v+"/video/media");}}}}
''',
            'android/database/ContentObserver.java': '''package android.database;public class ContentObserver {
 public ContentObserver(android.os.Handler h){}public void onChange(boolean b){}public void onChange(boolean b,android.net.Uri u){onChange(b);}}''',
            'android/database/Cursor.java': '''package android.database;public interface Cursor extends AutoCloseable {
 boolean moveToFirst();long getLong(int i);int getInt(int i);String getString(int i);void close();}''',
            'android/content/ContentResolver.java': '''package android.content;
import android.database.*;import android.os.*;import android.net.Uri;import android.graphics.Bitmap;import android.util.Size;
public class ContentResolver {
 public static final String QUERY_ARG_SQL_SELECTION="selection",QUERY_ARG_SQL_SELECTION_ARGS="args",QUERY_ARG_SQL_SORT_ORDER="sort",QUERY_ARG_LIMIT="limit";
 public volatile int queries,loads,registered;public volatile boolean block;public boolean empty;public long newest=93;public int media=1;public String mime="image/heic",owner="com.oplus.camera";
 public ContentObserver observer;public Bundle last;
 public Cursor query(Uri u,String[] cols,Bundle b,CancellationSignal signal){queries++;last=b;
 while(block&&!signal.isCanceled())try{Thread.sleep(2);}catch(InterruptedException e){throw new RuntimeException(e);}
 if(signal.isCanceled())throw new IllegalStateException("cancelled");
 final long id=newest;final int kind=media;final String type=mime,who=owner;final boolean none=empty;
 return new Cursor(){public boolean moveToFirst(){return !none;}public long getLong(int i){return id;}
 public int getInt(int i){return kind;}public String getString(int i){return i==2?type:who;}public void close(){}};}
 public Bitmap loadThumbnail(Uri u,Size size,CancellationSignal signal)throws java.io.IOException{
 if(size.w>256||size.h>256)throw new AssertionError("unbounded bitmap");loads++;return new Bitmap(Long.parseLong(u.getPath().substring(u.getPath().lastIndexOf('/')+1)));}
 public void registerContentObserver(Uri u,boolean descendants,ContentObserver o){registered++;observer=o;}
 public void unregisterContentObserver(ContentObserver o){registered--;observer=null;}
}''',
            'android/content/Intent.java': '''package android.content;import android.net.Uri;
public class Intent {public static final String ACTION_VIEW="android.intent.action.VIEW";public static final int FLAG_GRANT_READ_URI_PERMISSION=1;
 public Intent(String a){}public Intent setDataAndType(Uri u,String t){return this;}public Intent addFlags(int f){return this;}
 public void setClipData(ClipData d){}}
''',
            'android/content/ClipData.java': '''package android.content;public class ClipData {
 public static ClipData newRawUri(String label,android.net.Uri u){return new ClipData();}}''',
            'android/content/ActivityNotFoundException.java': '''package android.content;public class ActivityNotFoundException extends RuntimeException {}''',
            'android/widget/Toast.java': '''package android.widget;public class Toast {
 public static final int LENGTH_SHORT=0;public static Toast makeText(android.content.Context c,String t,int n){return new Toast();}public void show(){}}''',
            'Harness.java': '''import android.app.*;import android.os.*;import android.database.*;import realme.camera.compat.*;
public class Harness {
 static void require(boolean ok,String text){if(!ok)throw new AssertionError(text);}
 static void settle()throws Exception{for(int i=0;i<60;i++){Thread.sleep(3);Handler.flush(0);}}
 static void refresh()throws Exception{Handler.flush(250);settle();}
 public static void main(String[] args)throws Exception{
 Activity a=new Activity();CaptureThumbnail t=new CaptureThumbnail(a);t.bind(a.image);refresh();
 require(a.image.getDrawable().bitmap.id==93,"initial latest HEIC");require(a.launched==0,"thumbnail never launches viewer");
 require(Math.abs(a.image.renderedWidth/a.image.renderedHeight-320.0/180)<0.00001,
 "landscape thumbnail must preserve image proportions instead of stretching to square");
 require(a.image.renderedWidth>=80&&a.image.renderedHeight>=80,"thumbnail fills its viewport");
 require(a.resolver.last.getString("sort").equals("date_added DESC, _id DESC"),"explicit ordering");
 require(a.resolver.registered==1,"one observer");for(int i=0;i<10;i++)t.bind(a.image);require(a.resolver.registered==1,"bind must not duplicate observer");
 a.image.scale=android.widget.ImageView.ScaleType.FIT_XY; // OEM updates may reset shared widget state.
 a.resolver.newest=95;a.resolver.media=3;a.resolver.mime="video/mp4";a.resolver.observer.onChange(false);refresh();
 require(a.image.getDrawable().bitmap.id==95,"new video notification");
 require(Math.abs(a.image.renderedWidth/a.image.renderedHeight-180.0/320)<0.00001,
 "late portrait replacement must preserve proportions after stock widget update");
 require(a.image.renderedWidth>=80&&a.image.renderedHeight>=80,"portrait viewport coverage");
 ContentObserver stale=a.resolver.observer;t.close();int before=a.resolver.queries;stale.onChange(false);refresh();
 require(a.resolver.queries==before&&a.resolver.registered==0&&a.window.view.observer.focus.isEmpty(),"detached cleanup");
 Activity b=new Activity();b.resolver.newest=99;CaptureThumbnail u=new CaptureThumbnail(b);u.bind(b.image);refresh();
 require(b.image.getDrawable().bitmap.id==99,"fresh controller restores latest without private cache");
 b.resolver.block=true;b.resolver.observer.onChange(false);Handler.flush(250);Thread.sleep(20);
 b.keyguard.locked=true;b.focus(false);b.resolver.block=false;settle();
 require(b.image.getDrawable()==null,"lock clears owned thumbnail and rejects in-flight result");
 b.resolver.newest=101;b.keyguard.locked=false;b.focus(true);refresh();
 require(b.image.getDrawable()!=null&&b.image.getDrawable().bitmap.id==101,"unlock refresh");
 b.resolver.empty=true;b.resolver.observer.onChange(false);refresh();require(b.image.getDrawable()==null,"deleted captures clear owned cached thumbnail");
 b.resolver.empty=false;b.resolver.observer.onChange(false);refresh();
 int loads=b.resolver.loads;b.resolver.owner="other.app";b.resolver.observer.onChange(false);refresh();require(b.resolver.loads==loads,"foreign owner refused");
 b.resolver.owner="com.oplus.camera";b.resolver.media=2;b.resolver.observer.onChange(false);refresh();require(b.resolver.loads==loads,"non-photo/video refused");
 b.keyguard.locked=true;GalleryShortcut g=new GalleryShortcut(b);before=b.resolver.queries;g.onClick(b.image);refresh();require(b.launched==0&&b.resolver.queries==before,"locked click refused");g.close();u.close();
 Activity wrong=new Activity();wrong.pkg="other.app";CaptureThumbnail denied=new CaptureThumbnail(wrong);denied.bind(wrong.image);refresh();require(wrong.resolver.queries==0,"foreign package refused");
 Activity locked=new Activity();locked.keyguard.locked=true;CaptureThumbnail guarded=new CaptureThumbnail(locked);guarded.bind(locked.image);refresh();require(locked.resolver.queries==0&&locked.image.getDrawable()==null,"locked initial binding refused");guarded.close();
 Activity c=new Activity();GalleryShortcut deferred=new GalleryShortcut(c);deferred.bind();refresh();c.resolver.block=true;deferred.onClick(c.image);Thread.sleep(20);deferred.close();deferred.bind();c.resolver.block=false;settle();require(c.launched==0,"detached/rebound viewer lookup must not launch");
 deferred.onClick(c.image);settle();require(c.launched==1,"fresh visible unlocked click launches viewer");deferred.close();
 System.out.println("{\\"lifecycle_cases\\":10,\\"sort\\":\\"date_added DESC, _id DESC\\",\\"selection\\":\\"owner_package_name=? AND media_type IN (1,3) AND is_pending=0 AND is_trashed=0\\"}");
 }}''',
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, source in fixtures.items():
                path = root/name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(source)
            sources = [str(p) for p in root.rglob('*.java')]
            compile_result = subprocess.run([str(JDK/'javac'), '-d', str(root/'out'),
                *sources, str(gallery.GALLERY), str(gallery.THUMBNAIL)], capture_output=True, text=True)
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            result = subprocess.run([str(JDK/'java'), '-cp', str(root/'out'), 'Harness'],
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            contract = json.loads(result.stdout.strip())
        # Exercise the actual produced query contract with older/newer captures,
        # same-second ID ties, pending/trash entries and another app's media.
        db = sqlite3.connect(':memory:')
        db.execute('CREATE TABLE media (_id, date_added, owner_package_name, media_type, is_pending, is_trashed)')
        db.executemany('INSERT INTO media VALUES (?,?,?,?,?,?)', [
            (1, 100, 'com.oplus.camera', 1, 0, 0),
            (2, 200, 'com.oplus.camera', 1, 0, 0),
            (3, 200, 'com.oplus.camera', 3, 0, 0),
            (4, 300, 'com.oplus.camera', 1, 1, 0),
            (5, 400, 'com.oplus.camera', 1, 0, 1),
            (6, 500, 'other.app', 1, 0, 0)])
        sql = 'SELECT _id FROM media WHERE '+contract['selection']+' ORDER BY '+contract['sort']+' LIMIT 1'
        self.assertEqual(db.execute(sql, ['com.oplus.camera']).fetchone(), (3,))
        self.assertEqual(db.execute(sql.replace(contract['sort'], 'date_added, _id DESC'),
                                    ['com.oplus.camera']).fetchone(), (1,))
        db.close()


if __name__ == '__main__':
    unittest.main()
