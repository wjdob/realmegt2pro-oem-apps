package realme.camera.compat;

import android.app.Activity;
import android.view.View;
import com.oplus.camera.ui.setting.menu.CameraSettingMenuPanel;
import java.io.File;
import java.lang.reflect.Field;

public class LensMenuBindingTest {
    private static int cases;
    private static final String KEY="key_show_googlelens_icon";
    static void require(boolean value){if(!value)throw new AssertionError("case "+cases);}
    static void reviewed(Boolean value)throws Exception{
        Field field=EncoderMenuCompatibility.class.getDeclaredField("reviewed");field.setAccessible(true);field.set(null,value);
    }
    static final class Fixture {
        final Activity activity=new Activity("com.oplus.camera");
        final CameraSettingMenuPanel panel=new CameraSettingMenuPanel();
        final zj.y button=new zj.y(),flash=new zj.y();final View decoy=new View();
        final tj.h lens=new tj.h(KEY,button),other=new tj.h("pref_camera_flashmode_key",flash);
        final LensMenuBinding bridge=new LensMenuBinding(activity);
        Fixture()throws Exception{
            reviewed(true);GalleryShortcut.launches=0;GalleryShortcut.available=true;android.widget.Toast.shown=0;
            panel.id=43;activity.root=panel;decoy.id=42;activity.decoy=decoy;
            panel.add(lens);panel.add(other);
        }
    }
    public static void main(String[] args)throws Exception{
        Fixture f=new Fixture();Object touch=f.button.touch;View.OnClickListener nativeFlash=f.flash.click;
        require(f.button.id==-1);require(f.bridge.bind(f.activity));f.button.performClick();
        require(GalleryShortcut.launches==1&&f.lens.clicks==0&&f.panel.gates==1&&!f.button.pending);f.flash.performClick();
        require(f.other.clicks==1&&f.flash.click==nativeFlash&&f.button.touch==touch);cases++;

        f=new Fixture();final int[] decoyClicks={0};f.decoy.setOnClickListener(v->decoyClicks[0]++);
        require(f.bridge.bind(f.activity));f.decoy.performClick();require(decoyClicks[0]==1&&GalleryShortcut.launches==0);cases++;

        f=new Fixture();require(f.bridge.bind(f.activity));f.activity.awake=false;f.button.performClick();
        require(GalleryShortcut.launches==0&&android.widget.Toast.shown==0);cases++;

        f=new Fixture();require(f.bridge.bind(f.activity));f.button.enabled=false;f.button.performClick();f.button.enabled=true;f.button.shown=false;f.button.performClick();
        require(GalleryShortcut.launches==0);cases++;

        f=new Fixture();require(f.bridge.bind(f.activity));f.activity.destroyed=true;f.button.performClick();f.activity.destroyed=false;f.activity.finishing=true;f.button.performClick();
        require(GalleryShortcut.launches==0&&android.widget.Toast.shown==0);cases++;

        f=new Fixture();reviewed(false);require(!f.bridge.bind(f.activity));require(f.button.click==f.lens);cases++;

        f=new Fixture();reviewed(null);File unknown=File.createTempFile("lens-unreviewed-",".apk");f.activity.getApplicationInfo().sourceDir=unknown.getPath();
        try{require(!f.bridge.bind(f.activity));require(f.button.click==f.lens);}finally{unknown.delete();}cases++;

        f=new Fixture();f.activity.resources.foreign=true;require(!f.bridge.bind(f.activity));require(f.button.click==f.lens);cases++;

        f=new Fixture();f.panel.options.remove(KEY);require(!f.bridge.bind(f.activity));require(f.button.click==f.lens);cases++;

        f=new Fixture();f.panel.alias=f.other;require(!f.bridge.bind(f.activity));require(f.flash.click==f.other);cases++;

        f=new Fixture();rj.l unknownOption=new rj.l(KEY,f.button);f.panel.options.put(KEY,unknownOption);
        require(!f.bridge.bind(f.activity));require(f.button.click==f.lens);cases++;

        f=new Fixture();View alien=new View();tj.h wrongView=new tj.h(KEY,alien);f.panel.add(wrongView);
        require(!f.bridge.bind(f.activity));require(alien.click==wrongView);cases++;

        f=new Fixture();f.button.parent=null;require(!f.bridge.bind(f.activity));require(f.button.click==f.lens);cases++;

        f=new Fixture();require(f.bridge.bind(f.activity));View.OnClickListener stale=f.button.click;f.bridge.close();
        require(f.button.click==f.lens);f.button.performClick();require(f.lens.clicks==1&&GalleryShortcut.launches==0);
        stale.onClick(f.button);require(GalleryShortcut.launches==0);cases++;

        f=new Fixture();require(f.bridge.bind(f.activity));View.OnClickListener before=f.button.click;f.button.setOnClickListener(f.lens);
        require(f.bridge.bind(f.activity));require(f.button.click==before);f.button.performClick();require(GalleryShortcut.launches==1&&f.lens.clicks==0);cases++;

        f=new Fixture();require(f.bridge.bind(f.activity));View old=f.button;zj.y replacement=new zj.y();tj.h next=new tj.h(KEY,replacement);
        old.parent=null;f.panel.add(next);require(f.bridge.bind(f.activity));old.performClick();require(GalleryShortcut.launches==0);
        replacement.performClick();require(GalleryShortcut.launches==1);f.bridge.close();require(replacement.click==next);cases++;

        f=new Fixture();require(f.bridge.bind(f.activity));GalleryShortcut.available=false;f.button.performClick();
        require(android.widget.Toast.shown==1&&GalleryShortcut.launches==0&&f.lens.clicks==0);cases++;

        f=new Fixture();require(f.bridge.bind(f.activity));Activity other=new Activity("other.app");
        require(!f.bridge.bind(other));require(f.button.click==f.lens);f.lens.failure=true;require(!f.bridge.bind(f.activity));require(f.button.click==f.lens);cases++;

        f=new Fixture();require(f.bridge.bind(f.activity));f.lens.d=true;f.button.performClick();
        require(GalleryShortcut.launches==0&&f.panel.gates==0&&!f.button.pending);cases++;

        f=new Fixture();require(f.bridge.bind(f.activity));f.button.clickable=false;f.button.performClick();
        require(GalleryShortcut.launches==0&&f.panel.gates==0&&!f.button.pending);cases++;

        f=new Fixture();require(f.bridge.bind(f.activity));f.panel.allow=false;f.button.performClick();
        require(GalleryShortcut.launches==0&&f.panel.gates==1&&!f.button.pending&&android.widget.Toast.shown==0);
        f.panel.allow=true;f.button.pending=true;f.button.performClick();require(GalleryShortcut.launches==1&&!f.button.pending);cases++;

        f=new Fixture();require(f.bridge.bind(f.activity));f.panel.failure=true;f.button.performClick();
        require(GalleryShortcut.launches==0&&f.panel.gates==1&&!f.button.pending&&android.widget.Toast.shown==0);cases++;

        f=new Fixture();f.lens.j=null;require(!f.bridge.bind(f.activity));require(f.button.click==f.lens);cases++;

        f=new Fixture();require(f.bridge.bind(f.activity));f.lens.j=new Object();f.button.performClick();
        require(GalleryShortcut.launches==0&&f.panel.gates==0);cases++;

        System.out.println("Native Lens cases passed: "+cases);
    }
}
