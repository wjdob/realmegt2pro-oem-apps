package f6;

import android.app.Activity;
import android.view.Display;
import android.view.Window;
import android.view.WindowManager;
import android.util.Log;
import java.lang.ref.WeakReference;

/** Exact ABI of the pinned Camera DisplayUtil; per-window Android API adapter. */
public final class q0 {
    private static WeakReference<Activity> owner = new WeakReference<>(null);
    private static int originalMode;
    private static boolean saved;
    private static final String TAG = "RealmeCameraCompat";
    private q0() {}

    public static void a() {
        Activity activity = owner.get();
        if (activity != null && saved) {
            Window window = activity.getWindow();
            WindowManager.LayoutParams attrs = window.getAttributes();
            attrs.preferredDisplayModeId = originalMode;
            window.setAttributes(attrs);
            Log.i(TAG, "restored window display mode=" + originalMode);
        }
        owner.clear();
        saved = false;
    }
    public static void b(int modeId) {
        Activity activity = owner.get();
        if (activity == null) {
            Log.w(TAG, "no owned Camera window for mode=" + modeId);
            return;
        }
        if (modeId == 0) { a(); return; }
        Display display = activity.getDisplay();
        if (display == null) return;
        Display.Mode current = display.getMode();
        for (Display.Mode mode : display.getSupportedModes()) {
            if (mode.getModeId() != modeId) continue;
            if (mode.getPhysicalWidth() != current.getPhysicalWidth() ||
                mode.getPhysicalHeight() != current.getPhysicalHeight()) {
                Log.w(TAG, "refused cross-resolution display request=" + modeId);
                return;
            }
            WindowManager.LayoutParams attrs = activity.getWindow().getAttributes();
            attrs.preferredDisplayModeId = modeId;
            activity.getWindow().setAttributes(attrs);
            Log.i(TAG, "Camera window display mode=" + modeId);
            return;
        }
        Log.w(TAG, "unknown display mode=" + modeId);
    }
    public static void c(Activity activity) { d(activity); }
    public static void d(Activity activity) {
        if (activity == null || activity.isDestroyed() || activity.getDisplay() == null) return;
        if (owner.get() != activity) {
            a();
            owner = new WeakReference<>(activity);
            originalMode = activity.getWindow().getAttributes().preferredDisplayModeId;
            saved = true;
        }
        Display.Mode current = activity.getDisplay().getMode();
        Display.Mode selected = current;
        for (Display.Mode mode : activity.getDisplay().getSupportedModes()) {
            if (mode.getPhysicalWidth() == current.getPhysicalWidth() &&
                mode.getPhysicalHeight() == current.getPhysicalHeight() &&
                mode.getRefreshRate() > selected.getRefreshRate()) selected = mode;
        }
        b(selected.getModeId());
    }
}
