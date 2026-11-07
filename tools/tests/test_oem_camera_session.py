"""Exercise production session/proxy sources at the Android API boundary."""
from pathlib import Path
import subprocess
import tempfile
import unittest

import oem_camera_session as session

JAVAC = Path('/build/lineage/prebuilts/jdk/jdk21/linux-x86/bin/javac')


@unittest.skipUnless(JAVAC.is_file(), 'requires WSL realme-build JDK21')
class CameraSessionBehaviorTests(unittest.TestCase):
    def test_caller_identity_serialization_and_original_proxy_behavior(self):
        sources = {
            'android/app/Application.java': '''package android.app;
public class Application {private final String name;
 public Application(String n){name=n;} public String getPackageName(){return name;}}
''',
            'android/app/ActivityThread.java': '''package android.app;
public class ActivityThread {public static Application app;
 public static Application currentApplication(){return app;}}
''',
            'android/view/Surface.java': 'package android.view; public class Surface {}',
            'android/hardware/camera2/CaptureRequest.java': '''package android.hardware.camera2;
import java.util.*; import android.view.Surface;
public class CaptureRequest {
 public static class Key<T> {public final String name; public final Class<T> type;
  public Key(String n,Class<T> t){name=n;type=t;}}
 public static class Builder {
  public final Map<String,Object> values=new HashMap<>();
  public final Set<Surface> targets=new HashSet<>();public Object tag;
  public boolean failSet,failGet,corrupt;public int builds;
  public <T> void set(Key<T> k,T v){if(failSet)throw new IllegalArgumentException("set");values.put(k.name,v);}
  public <T> T get(Key<T> k){if(failGet)throw new IllegalArgumentException("get");
   if(corrupt && k.name.equals("com.oplus.packageName"))return (T)new byte[0];return (T)values.get(k.name);}
  public void addTarget(Surface s){Objects.requireNonNull(s);targets.add(s);}
  public void removeTarget(Surface s){Objects.requireNonNull(s);targets.remove(s);}
  public void setTag(Object t){tag=t;}public CaptureRequest build(){builds++;return new CaptureRequest();}
 }
}''',
            'SessionBehavior.java': '''import android.app.*;
import android.hardware.camera2.CaptureRequest;import android.view.Surface;
import com.oplus.ocs.camera.metadata.parameter.CaptureRequestBuilderProxy;
import realme.camera.compat.SessionCaller;import java.nio.charset.StandardCharsets;import java.util.*;
public class SessionBehavior {
 static int checks;static void check(boolean b){if(!b)throw new AssertionError();checks++;}
 static void rejected(Runnable r){try{r.run();throw new AssertionError("accepted");}
  catch(IllegalStateException expected){checks++;}}
 public static void main(String[] args){
  CaptureRequest.Builder b=new CaptureRequest.Builder();
  rejected(()->SessionCaller.apply(b));check(b.values.isEmpty());
  ActivityThread.app=new Application("org.lineageos.aperture");
  rejected(()->SessionCaller.apply(b));check(b.values.isEmpty());
  ActivityThread.app=new Application("com.oplus.camera.other");
  rejected(()->SessionCaller.apply(b));check(b.values.isEmpty());
  ActivityThread.app=new Application("com.oplus.camera");
  CaptureRequestBuilderProxy p=new CaptureRequestBuilderProxy(b);
  CaptureRequest.Key<Integer> key=new CaptureRequest.Key<>("android.control.mode",Integer.class);
  check(p.setParameter(key,3));check(p.getParameter(key).equals(3));
  Object tag=new Object();p.setTag(tag);check(b.tag==tag);
  Surface s=new Surface();p.addTarget(s);check(b.targets.contains(s));p.removeTarget(s);check(b.targets.isEmpty());
  check(p.build()!=null && b.builds==1);
  byte[] expected="com.oplus.camera\\0".getBytes(StandardCharsets.UTF_8);
  check(Arrays.equals(expected,(byte[])b.values.get("com.oplus.packageName")));
  check(expected.length==17 && expected[16]==0);check(p.getParameter(key).equals(3));check(b.values.size()==2);
  p.build();check(b.builds==2 && b.values.size()==2);
  b.failSet=true;check(!p.setParameter(key,4));b.failSet=false;
  b.failGet=true;try{p.getParameter(key);throw new AssertionError();}catch(IllegalArgumentException expectedError){checks++;}b.failGet=false;
  b.corrupt=true;rejected(()->p.build());check(b.builds==2);b.corrupt=false;
  ActivityThread.app=null;rejected(()->p.build());check(b.builds==2);
  CaptureRequestBuilderProxy empty=new CaptureRequestBuilderProxy(null);
  check(empty.build()==null && empty.getParameter(key)==null && empty.setParameter(key,1));
  empty.addTarget(s);empty.removeTarget(s);empty.setTag(tag);
  System.out.println("SESSION_BEHAVIOR_OK checks="+checks);
 }
}''',
        }
        with tempfile.TemporaryDirectory(prefix='camera-session-test-') as directory:
            root = Path(directory)
            fixtures = []
            for name, text in sources.items():
                path = root/name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
                fixtures.append(str(path))
            subprocess.run([str(JAVAC), '--release', '17', '-d', str(root/'classes'),
                            *fixtures, *map(str, session.SOURCES)],
                           check=True, capture_output=True)
            result = subprocess.run([str(JAVAC.with_name('java')), '-cp',
                                     str(root/'classes'), 'SessionBehavior'],
                                    check=True, capture_output=True, text=True)
            self.assertIn('SESSION_BEHAVIOR_OK checks=24', result.stdout)


if __name__ == '__main__':
    unittest.main()
