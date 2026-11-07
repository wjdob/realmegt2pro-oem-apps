package realme.clock.compat;

import android.app.Activity;
import android.app.Application;
import android.os.Bundle;
import android.os.Build;
import android.view.View;
import android.view.ViewTreeObserver;
import android.view.WindowInsets;
import java.lang.ref.WeakReference;
import java.util.WeakHashMap;

/** Add missing navigation clearance without replacing the OEM inset listener. */
public final class ClockInsets {
    private static final WeakHashMap<View, WeakReference<State>> STATES = new WeakHashMap<>();
    private static boolean registered;
    private ClockInsets() { }
    public static void register(Application application) {
        if (Build.VERSION.SDK_INT < 30 || registered) return;
        registered = true;
        application.registerActivityLifecycleCallbacks(new Application.ActivityLifecycleCallbacks() {
            @Override public void onActivityResumed(Activity activity) {
                if (activity.getClass().getName().equals("com.oplus.alarmclock.AlarmClock")) install(activity);
            }
            @Override public void onActivityCreated(Activity a, Bundle b) { }
            @Override public void onActivityStarted(Activity a) { }
            @Override public void onActivityPaused(Activity a) { }
            @Override public void onActivityStopped(Activity a) { }
            @Override public void onActivitySaveInstanceState(Activity a, Bundle b) { }
            @Override public void onActivityDestroyed(Activity a) { }
        });
    }
    public static void apply(Activity activity, WindowInsets insets) {
        if (Build.VERSION.SDK_INT >= 30) install(activity);
    }
    private static void install(Activity activity) {
        View root = activity.findViewById(android.R.id.content);
        int id = activity.getResources().getIdentifier("navigation_label", "id", activity.getPackageName());
        View tabs = id == 0 ? null : activity.findViewById(id);
        if (root == null || tabs == null) return;
        WeakReference<State> reference = STATES.get(root);
        State state = reference == null ? null : reference.get();
        if (state == null) {
            state = new State(root, tabs, activity);
            STATES.put(root, new WeakReference<>(state));
            root.getViewTreeObserver().addOnGlobalLayoutListener(state);
        }
        state.onGlobalLayout();
    }
    private static final class State implements ViewTreeObserver.OnGlobalLayoutListener {
        final View root, tabs, decor;
        final Activity activity;
        final int baseline;
        final int[] position = new int[2];
        State(View root, View tabs, Activity activity) {
            this.root = root; this.tabs = tabs; this.activity = activity;
            this.decor = activity.getWindow().getDecorView();
            baseline = root.getPaddingBottom();
        }
        @Override public void onGlobalLayout() {
            if (tabs.getHeight() == 0 || decor.getHeight() == 0) return;
            WindowInsets insets = activity.getWindowManager().getCurrentWindowMetrics().getWindowInsets();
            int inset = insets.isVisible(WindowInsets.Type.ime()) ? 0
                    : insets.getInsets(WindowInsets.Type.navigationBars()).bottom;
            tabs.getLocationOnScreen(position);
            int bottom = position[1] + tabs.getHeight();
            decor.getLocationOnScreen(position);
            int required = ClockInsetPolicy.bottomPadding(baseline, root.getPaddingBottom(),
                    bottom, position[1] + decor.getHeight(), inset);
            if (root.getPaddingBottom() != required) {
                root.setPadding(root.getPaddingLeft(), root.getPaddingTop(), root.getPaddingRight(), required);
                android.util.Log.d("ClockCompatInsets", "navigationInset="+inset+", paddingBottom="+required);
            }
        }
    }
}
