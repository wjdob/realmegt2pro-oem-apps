package ek;

import android.app.Activity;
import android.view.View;
import android.view.ViewTreeObserver;
import java.lang.ref.WeakReference;

/** Pinned Camera helper ABI, backed by the Camera activity's Android window state. */
public final class v0 {
    public volatile boolean a;
    public boolean b;
    // Retain the stock field descriptors for its existing synthetic callers.
    // The new backend never obtains or registers an OEM task-management service.
    public android.app.OplusActivityTaskManager c;
    public volatile v0$c d;
    public final v0$a e = null;
    private volatile WindowWatcher window;

    public v0() {}
    public static v0 b() { return v0$b.a; }

    public final boolean a(Activity activity) {
        boolean state = activity != null && !activity.isDestroyed() && !b &&
                (activity.isInMultiWindowMode() || activity.isInPictureInPictureMode());
        a = state;
        return state;
    }
    public final boolean c() { return a; }

    // og.d is the original APK's callback class. The compile stub is never packaged.
    // Keep the exact descriptor; dispatch through its genuine parent interface.
    public final synchronized void d(Activity activity, og.d callback) {
        f();
        if (activity == null || activity.isDestroyed() || b) return;
        d = (v0$c) (Object) callback;
        WindowWatcher next = new WindowWatcher(this, activity);
        window = next;
        activity.runOnUiThread(next);
    }

    public final synchronized void e() {
        c = null;
        WindowWatcher current = window;
        if (current != null) current.schedule();
    }
    public final synchronized void f() {
        d = null;
        WindowWatcher current = window;
        window = null;
        a = false;
        c = null;
        if (current != null) {
            current.stopping = true;
            current.schedule();
        }
    }

    static final class WindowWatcher implements Runnable, View.OnLayoutChangeListener,
            View.OnAttachStateChangeListener, ViewTreeObserver.OnWindowFocusChangeListener {
        private final v0 owner;
        private final WeakReference<Activity> activity;
        private WeakReference<View> decor = new WeakReference<>(null);
        private boolean attached;
        volatile boolean stopping;

        WindowWatcher(v0 owner, Activity activity) {
            this.owner = owner;
            this.activity = new WeakReference<>(activity);
        }
        void schedule() {
            Activity current = activity.get();
            if (current != null) current.runOnUiThread(this);
        }
        @Override public void run() {
            Activity current = activity.get();
            if (stopping || owner.window != this || current == null || current.isDestroyed()) {
                detach();
                return;
            }
            if (!attached) {
                View view = current.getWindow().getDecorView();
                decor = new WeakReference<>(view);
                view.addOnLayoutChangeListener(this);
                view.addOnAttachStateChangeListener(this);
                view.getViewTreeObserver().addOnWindowFocusChangeListener(this);
                attached = true;
            }
            refresh();
        }
        private void refresh() {
            if (stopping || owner.window != this) return;
            boolean previous = owner.a;
            boolean current = owner.a(activity.get());
            v0$c callback = owner.d;
            if (previous != current && callback != null && !stopping && owner.window == this) {
                callback.a();
            }
        }
        private void detach() {
            View view = decor.get();
            if (view != null && attached) {
                view.removeOnLayoutChangeListener(this);
                view.removeOnAttachStateChangeListener(this);
                ViewTreeObserver observer = view.getViewTreeObserver();
                if (observer.isAlive()) observer.removeOnWindowFocusChangeListener(this);
            }
            attached = false;
            decor.clear();
            if (owner.window == this) {
                owner.window = null;
                owner.d = null;
                owner.a = false;
            }
        }
        @Override public void onLayoutChange(View view, int l, int t, int r, int b,
                int oldL, int oldT, int oldR, int oldB) { refresh(); }
        @Override public void onWindowFocusChanged(boolean focused) { refresh(); }
        @Override public void onViewAttachedToWindow(View view) { refresh(); }
        @Override public void onViewDetachedFromWindow(View view) { detach(); }
    }
}
