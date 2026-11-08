package realme.camera.compat;

import android.app.Activity;
import android.util.Log;
import android.view.View;
import android.view.ViewParent;
import android.widget.Toast;
import java.lang.ref.WeakReference;
import java.lang.reflect.Field;
import java.lang.reflect.Method;

/** Exact-APK bridge for the native keyed menu control, preserving its touch path. */
final class LensMenuBinding implements View.OnClickListener {
    private static final String CAMERA = "com.oplus.camera";
    private static final String KEY = "key_show_googlelens_icon";
    private static final String PANEL = "com.oplus.camera.ui.setting.menu.CameraSettingMenuPanel";
    private final WeakReference<Activity> activity;
    private WeakReference<View> target = new WeakReference<>(null);
    private WeakReference<View> panel = new WeakReference<>(null);
    private WeakReference<Object> option = new WeakReference<>(null);
    private Method find, key, view, gate, reset;
    private Field removed, owner;

    LensMenuBinding(Activity owner) { activity = new WeakReference<>(owner); }

    boolean bind(Activity current) {
        if (current == null || current != activity.get() || !CAMERA.equals(current.getPackageName())
                || current.isDestroyed() || !EncoderMenuCompatibility.reviewedApk(current)) {
            close(); return false;
        }
        try {
            int id = current.getResources().getIdentifier("oplus_setting_bar", "id", CAMERA);
            View root = id == 0 ? null : current.findViewById(id);
            if (root == null || !"com.oplus.camera:id/oplus_setting_bar".equals(
                    current.getResources().getResourceName(root.getId()))) { close(); return false; }
            ClassLoader loader = current.getClassLoader();
            Class<?> panelClass = Class.forName(PANEL, false, loader);
            Class<?> optionClass = Class.forName("rj.l", false, loader);
            if (!panelClass.isInstance(root)) { close(); return false; }
            Method nextFind = panelClass.getMethod("j", String.class);
            Method nextKey = optionClass.getMethod("x");
            Method nextView = optionClass.getMethod("q");
            Method nextGate = panelClass.getMethod("v", String.class, boolean.class, boolean.class);
            Field nextRemoved = optionClass.getField("d"), nextOwner = optionClass.getField("j");
            if (nextFind.getReturnType() != optionClass || nextKey.getReturnType() != String.class
                    || nextView.getReturnType() != View.class || nextGate.getReturnType() != boolean.class
                    || nextRemoved.getType() != boolean.class) { close(); return false; }
            Object selected = nextFind.invoke(root, KEY);
            if (selected == null || !"tj.h".equals(selected.getClass().getName())
                    || !(selected instanceof View.OnClickListener)
                    || !KEY.equals(nextKey.invoke(selected)) || nextOwner.get(selected) != root) { close(); return false; }
            Object candidate = nextView.invoke(selected);
            if (!(candidate instanceof View) || !"zj.y".equals(candidate.getClass().getName())
                    || !descendant((View) candidate, root)) { close(); return false; }
            View button = (View) candidate;
            Method nextReset = button.getClass().getMethod("setMbClickUpdate", boolean.class);
            if (nextReset.getReturnType() != void.class) { close(); return false; }
            boolean changed = target.get() != button || option.get() != selected || panel.get() != root;
            if (changed) {
                close();
                panel = new WeakReference<>(root); option = new WeakReference<>(selected);
                target = new WeakReference<>(button); find = nextFind; key = nextKey; view = nextView;
                gate = nextGate; reset = nextReset; removed = nextRemoved; owner = nextOwner;
            }
            // The native option installs a standard click listener and a separate
            // touch listener. Change only this exact option's click callback.
            // Reassert after layout in case the OEM reinitializes the same control.
            button.setOnClickListener(this);
            if (changed) Log.i("RealmeCameraLens", "Bound native keyed Lens control");
            return true;
        } catch (ReflectiveOperationException | RuntimeException | LinkageError unavailable) {
            close();
            Log.w("RealmeCameraLens", "Native Lens menu binding unavailable");
            return false;
        }
    }

    private static boolean descendant(View button, View root) {
        ViewParent parent = button.getParent();
        for (int depth = 0; parent != null && depth < 64; depth++) {
            if (parent == root) return true;
            if (!(parent instanceof View)) return false;
            parent = ((View) parent).getParent();
        }
        return false;
    }

    private boolean ownsCurrentControl(Activity current) {
        View root = panel.get(), button = target.get(); Object selected = option.get();
        if (current == null || root == null || button == null || selected == null
                || find == null || key == null || view == null || owner == null) return false;
        try {
            int id = current.getResources().getIdentifier("oplus_setting_bar", "id", CAMERA);
            return current.findViewById(id) == root && find.invoke(root, KEY) == selected
                    && KEY.equals(key.invoke(selected)) && view.invoke(selected) == button
                    && owner.get(selected) == root
                    && descendant(button, root);
        } catch (ReflectiveOperationException | RuntimeException | LinkageError unavailable) {
            return false;
        }
    }

    void close() {
        Activity current = activity.get(); View button = target.get(); Object selected = option.get();
        if (ownsCurrentControl(current) && selected instanceof View.OnClickListener) {
            button.setOnClickListener((View.OnClickListener) selected);
        }
        target.clear(); option.clear(); panel.clear(); find = null; key = null; view = null;
        gate = null; reset = null; removed = null; owner = null;
    }

    @Override public void onClick(View button) {
        Activity current = activity.get();
        if (button != target.get() || current == null || !CAMERA.equals(current.getPackageName())
                || !GalleryShortcut.unlocked(current) || !ownsCurrentControl(current)) return;
        Method finish = reset;
        try {
            if (!button.isShown() || !button.isEnabled() || !button.isClickable()
                    || removed.getBoolean(option.get())) return;
            // Keep the OEM's paused/capture/mode-transition checks and menu
            // bookkeeping. This exact APK's v/J0 path does not launch Lens.
            if (!Boolean.TRUE.equals(gate.invoke(panel.get(), KEY, false, false))
                    || !GalleryShortcut.unlocked(current) || !ownsCurrentControl(current)) return;
            if (GalleryShortcut.launchLens(current)) {
                Log.i("RealmeCameraLens", "Opened Lens from native menu option");
            } else {
                Toast.makeText(current, "Google Lens is unavailable", Toast.LENGTH_SHORT).show();
            }
        } catch (ReflectiveOperationException | RuntimeException | LinkageError unavailable) {
            Log.w("RealmeCameraLens", "Native Lens click check unavailable");
        } finally {
            // The native view maintains a touch animation flag independently of
            // its listener. Release it on accepted, refused and failed clicks.
            try { finish.invoke(button, false); }
            catch (ReflectiveOperationException | RuntimeException | LinkageError unavailable) {
                Log.w("RealmeCameraLens", "Native Lens click reset unavailable");
            }
        }
    }
}
