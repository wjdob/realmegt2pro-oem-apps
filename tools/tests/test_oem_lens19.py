"""Keep Android's caller identity when opening Lens from Camera."""
from unittest.mock import patch
import test_oem_lens18 as previous


class Lens19Tests(previous.Lens18Tests):
    def setUp(self):
        self.directory=previous.ROOT/'tools/oem_camera_compat/lens19/realme/camera/compat'
        self.binding=patch.object(previous,'DIRECTORY',self.directory)
        self.binding.start()
        self.addCleanup(self.binding.stop)

    def integration_sources(self,fixtures):
        activity=fixtures['android/app/Activity.java']
        extra='''public int resultLaunches;public void startActivityForResult(Intent i,int request){
 if(request!=0)throw new AssertionError("OEM request code changed");resultLaunches++;startActivity(i);}
'''
        fixtures['android/app/Activity.java']=activity[:activity.rfind('}')]+extra+'}\n'
        fixtures['Harness.java']=fixtures['Harness.java'].replace(
            'require("com.google.android.googlequicksearchbox".equals(l.last.pkg)',
            'require(l.resultLaunches==1,"Lens must provide Android calling package through result launch");require("com.google.android.googlequicksearchbox".equals(l.last.pkg)')
        return fixtures

    def test_modern_google_route_and_gallery_protocol_source_remain_exact(self):
        before=(previous.ROOT/'tools/oem_camera_compat/lens18/realme/camera/compat/GalleryShortcut.java').read_text()
        after=(self.directory/'GalleryShortcut.java').read_text()
        start=before.index('    public static boolean launchLens(');end=before.index('    @Override public void onGlobalLayout',start)
        expected=before[start:end].replace('current.startActivity(intent);',
            '// LensExportedActivity requires a calling package. Preserve the OEM\n                // request code and Activity result launch so Android supplies it.\n                current.startActivityForResult(intent, 0);')
        self.assertEqual(before[:start]+expected+before[end:],after)
        for name in ('CaptureThumbnail.java','EncoderMenuCompatibility.java','LensMenuBinding.java'):
            self.assertEqual((self.directory/name).read_bytes(),(previous.ROOT/'tools/oem_camera_compat/lens18/realme/camera/compat'/name).read_bytes())
