from unittest.mock import patch
import test_oem_lens18 as previous

class Lens20Tests(previous.Lens18Tests):
    def setUp(self):
        self.directory=previous.ROOT/'tools/oem_camera_compat/lens20/realme/camera/compat'
        binding=patch.object(previous,'DIRECTORY',self.directory);binding.start();self.addCleanup(binding.stop)

    def integration_sources(self,fixtures):
        fixtures['android/content/ComponentName.java']='''package android.content;public class ComponentName {
 private final String pkg;public ComponentName(String p){pkg=p;}public String getPackageName(){return pkg;}}'''
        intent=fixtures['android/content/Intent.java'].replace('public class Intent {','public class Intent {public static final String ACTION_MAIN="android.intent.action.MAIN";public ComponentName component;public Intent setComponent(ComponentName c){component=c;return this;}public ComponentName getComponent(){return component;}')
        fixtures['android/content/Intent.java']=intent
        fixtures['android/content/pm/PackageManager.java']='''package android.content.pm;import android.content.*;
public class PackageManager {public boolean installed=true,foreign;
 public ComponentName resolve(Intent i){return null;}
 public Intent getLaunchIntentForPackage(String name){if(!"com.google.ar.lens".equals(name))throw new AssertionError();
 return installed?new Intent(Intent.ACTION_MAIN).setComponent(new ComponentName(foreign?"other.app":name)).setPackage(name).addFlags(0x10000000):null;}}'''
        fixtures['android/app/Activity.java']=fixtures['android/app/Activity.java'].replace('public void startActivity(Intent i){','public void startActivity(Intent i){if(i.getComponent()!=null){if(lensRace)throw new ActivityNotFoundException();if(lensDenied)throw new SecurityException();}')
        harness=fixtures['Harness.java'];start=harness.index(' Activity l=new Activity();');end=harness.index(' System.out.println(',start)
        harness=harness[:start]+''' Activity l=new Activity();GalleryShortcut bridge=new GalleryShortcut(l);bridge.bind();
 l.lens.performClick();require(l.launched==1&&l.last.getComponent()!=null,"native keyed button reaches installed launcher");
 require("com.google.ar.lens".equals(l.last.getComponent().getPackageName())&&"com.google.ar.lens".equals(l.last.pkg),"only installed Lens package");
 require(Intent.ACTION_MAIN.equals(l.last.action)&&l.last.data==null&&l.last.clip==null&&l.last.flags==0x10000000,"ordinary app launch, no media or extra grants");
 l.pm.installed=false;l.lens.performClick();require(l.launched==1,"missing Lens no launch");l.pm.installed=true;
 l.pm.foreign=true;l.lens.performClick();require(l.launched==1,"foreign component refused");l.pm.foreign=false;
 l.lensRace=true;l.lens.performClick();require(l.launched==1,"removed launcher refused");l.lensRace=false;
 l.lensDenied=true;l.lens.performClick();require(l.launched==1,"security refusal");l.lensDenied=false;
 l.keyguard.locked=true;l.lens.performClick();require(l.launched==1,"locked refused");l.keyguard.locked=false;
 l.focus(false);l.lens.performClick();require(l.launched==1,"background refused");l.focus(true);
 l.destroyed=true;require(!GalleryShortcut.launchLens(l),"destroyed refused");l.destroyed=false;
 l.finishing=true;require(!GalleryShortcut.launchLens(l),"finishing refused");l.finishing=false;
 l.pkg="other.app";require(!GalleryShortcut.launchLens(l),"foreign caller refused");l.pkg="com.oplus.camera";
 require(!GalleryShortcut.launchLens(null),"null caller refused");
 bridge.close();l.lens.performClick();require(l.launched==1,"closed bridge restores native callback");
 bridge.bind();l.lens.performClick();require(l.launched==2,"resume/rebind launches again");bridge.close();
'''+harness[end:]
        fixtures['Harness.java']=harness;return fixtures

    def test_modern_google_route_and_gallery_protocol_source_remain_exact(self):
        before=(previous.ROOT/'tools/oem_camera_compat/lens19/realme/camera/compat/GalleryShortcut.java').read_text()
        after=(self.directory/'GalleryShortcut.java').read_text()
        start='    public static boolean launchLens(';end='    @Override public void onGlobalLayout'
        self.assertEqual(before[:before.index(start)],after[:after.index(start)])
        self.assertEqual(before[before.index(end):],after[after.index(end):])
        method=after[after.index(start):after.index(end)]
        self.assertIn('getLaunchIntentForPackage("com.google.ar.lens")',method)
        self.assertNotIn('google://lens',method);self.assertNotIn('startActivityForResult',method)
        for name in ('CaptureThumbnail.java','EncoderMenuCompatibility.java'):
            self.assertEqual((self.directory/name).read_bytes(),(previous.ROOT/'tools/oem_camera_compat/lens19/realme/camera/compat'/name).read_bytes())
        old=(previous.ROOT/'tools/oem_camera_compat/lens19/realme/camera/compat/LensMenuBinding.java').read_text()
        self.assertEqual(old.replace('Opened Lens from native menu option','Requested installed Lens launcher').replace('Google Lens is unavailable','Install Google Lens to use this button'),(self.directory/'LensMenuBinding.java').read_text())
