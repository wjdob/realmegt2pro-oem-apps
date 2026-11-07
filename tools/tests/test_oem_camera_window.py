"""Exercise the real window adapter against small Android boundary fixtures."""
from pathlib import Path
import subprocess
import tempfile
import unittest

import oem_camera_window as window

JAVAC = Path('/build/lineage/prebuilts/jdk/jdk21/linux-x86/bin/javac')


@unittest.skipUnless(JAVAC.is_file(), 'requires WSL realme-build JDK21')
class CameraWindowBehaviorTests(unittest.TestCase):
    def test_modes_callbacks_detach_and_cancelled_registration(self):
        sources = {
            'android/app/Activity.java': '''package android.app;
import android.view.Window; import java.util.*;
public class Activity {
 public boolean multi,pip,destroyed,defer;
 public final Window window=new Window(); private final List<Runnable> queue=new ArrayList<>();
 public boolean isDestroyed(){return destroyed;}
 public boolean isInMultiWindowMode(){return multi;}
 public boolean isInPictureInPictureMode(){return pip;}
 public Window getWindow(){return window;}
 public void runOnUiThread(Runnable r){if(defer)queue.add(r);else r.run();}
 public void flush(){List<Runnable> pending=new ArrayList<>(queue);queue.clear();for(Runnable r:pending)r.run();}
}''',
            'android/view/Window.java': '''package android.view;
public class Window {public final View view=new View();public View getDecorView(){return view;}}''',
            'android/view/ViewTreeObserver.java': '''package android.view;
import java.util.*;
public class ViewTreeObserver {
 public interface OnWindowFocusChangeListener {void onWindowFocusChanged(boolean focused);}
 public final Set<OnWindowFocusChangeListener> focus=new HashSet<>();
 public boolean isAlive(){return true;}
 public void addOnWindowFocusChangeListener(OnWindowFocusChangeListener l){focus.add(l);}
 public void removeOnWindowFocusChangeListener(OnWindowFocusChangeListener l){focus.remove(l);}
 public void fire(boolean value){for(OnWindowFocusChangeListener l:new ArrayList<>(focus))l.onWindowFocusChanged(value);}
}''',
            'android/view/View.java': '''package android.view;
import java.util.*;
public class View {
 public interface OnLayoutChangeListener {void onLayoutChange(View v,int l,int t,int r,int b,int ol,int ot,int or,int ob);}
 public interface OnAttachStateChangeListener {void onViewAttachedToWindow(View v);void onViewDetachedFromWindow(View v);}
 public final Set<OnLayoutChangeListener> layout=new HashSet<>();
 public final Set<OnAttachStateChangeListener> attach=new HashSet<>();
 public final ViewTreeObserver observer=new ViewTreeObserver();
 public void addOnLayoutChangeListener(OnLayoutChangeListener l){layout.add(l);}
 public void removeOnLayoutChangeListener(OnLayoutChangeListener l){layout.remove(l);}
 public void addOnAttachStateChangeListener(OnAttachStateChangeListener l){attach.add(l);}
 public void removeOnAttachStateChangeListener(OnAttachStateChangeListener l){attach.remove(l);}
 public ViewTreeObserver getViewTreeObserver(){return observer;}
 public void fireLayout(){for(OnLayoutChangeListener l:new ArrayList<>(layout))l.onLayoutChange(this,0,0,1,1,0,0,2,2);}
 public void detach(){for(OnAttachStateChangeListener l:new ArrayList<>(attach))l.onViewDetachedFromWindow(this);}
 public int listeners(){return layout.size()+attach.size()+observer.focus.size();}
}''',
            'og/d.java': '''package og; public final class d implements ek.v0$c {public int calls;public void a(){calls++;}}''',
            'WindowBehavior.java': '''import android.app.Activity; import ek.v0;
public class WindowBehavior {
 static void check(boolean value,String why){if(!value)throw new AssertionError(why);}
 public static void main(String[] args){
  v0 helper=new v0();Activity activity=new Activity();og.d callback=new og.d();
  helper.d(activity,callback);
  check(!helper.c()&&callback.calls==0,"fullscreen initial state");
  check(activity.window.view.listeners()==3,"listeners registered");
  activity.window.view.observer.fire(false);
  check(callback.calls==0,"focus loss alone is not multi-window");
  activity.multi=true;activity.window.view.fireLayout();
  check(helper.c()&&callback.calls==1,"multi-window transition");
  activity.window.view.fireLayout();activity.window.view.observer.fire(true);
  check(callback.calls==1,"stable layout must not loop callbacks");
  activity.multi=false;activity.pip=true;helper.e();
  check(helper.c()&&callback.calls==1,"PiP remains non-fullscreen");
  activity.pip=false;activity.window.view.fireLayout();
  check(!helper.c()&&callback.calls==2,"return to fullscreen");
  helper.f();activity.multi=true;activity.window.view.fireLayout();
  check(activity.window.view.listeners()==0&&callback.calls==2,"unregister clears late callbacks");
  activity.defer=true;helper.d(activity,callback);helper.f();activity.flush();
  check(activity.window.view.listeners()==0,"cancel before queued registration");
  activity.defer=false;helper.d(activity,callback);activity.window.view.detach();
  check(activity.window.view.listeners()==0&&helper.d==null&&!helper.c(),"detach clears callback and state");
  Activity next=new Activity();og.d nextCallback=new og.d();helper.d(activity,callback);helper.d(next,nextCallback);
  check(activity.window.view.listeners()==0&&next.window.view.listeners()==3,"replacement releases old activity");
  next.destroyed=true;next.window.view.fireLayout();
  check(!helper.c(),"destroyed activity is not an active multi-window");
  helper.f();helper.b=true;next.destroyed=false;next.multi=true;helper.d(next,nextCallback);
  check(!helper.a(next)&&next.window.view.listeners()==0,"explicit unavailable backend respected");
  helper.d(null,null);helper.e();helper.f();
  System.out.println("WINDOW_BEHAVIOR_OK");
 }
}''',
        }
        scratch = window.ROOT / 'tmp/oem-window-tests'
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as directory:
            root = Path(directory)
            files = []
            for relative, data in sources.items():
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(data)
                files.append(target)
            # Runtime boundary fixtures replace only Android and the callback body.
            files += [p for p in window.source_files() if p != window.STUBS/'og/d.java']
            classes = root / 'classes'
            compiled = subprocess.run([str(JAVAC), '--release', '17', '-d', str(classes),
                                       *map(str, files)], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(JAVAC.with_name('java')), '-cp', str(classes),
                                     'WindowBehavior'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('WINDOW_BEHAVIOR_OK', result.stdout)


if __name__ == '__main__':
    unittest.main()
