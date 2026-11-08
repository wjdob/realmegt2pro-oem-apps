package realme.camera.compat;

import android.app.Activity;
import android.app.KeyguardManager;
import android.content.ContentResolver;
import android.content.ContentUris;
import android.content.Context;
import android.database.ContentObserver;
import android.database.Cursor;
import android.graphics.Bitmap;
import android.graphics.drawable.Drawable;
import android.net.Uri;
import android.os.CancellationSignal;
import android.os.Handler;
import android.os.Looper;
import android.provider.MediaStore;
import android.util.Log;
import android.util.Size;
import android.view.View;
import android.view.ViewTreeObserver;
import android.widget.ImageView;
import java.lang.ref.WeakReference;
import java.util.concurrent.atomic.AtomicBoolean;

/** Restore the optional Camera's own latest completed capture through Android APIs. */
public final class CaptureThumbnail implements Runnable, ViewTreeObserver.OnWindowFocusChangeListener {
    private final WeakReference<Activity> activity;
    private final GalleryShortcut gallery;
    private boolean captureRefresh;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final AtomicBoolean busy = new AtomicBoolean();
    private final Changes changes = new Changes(main, this);
    private ContentResolver resolver;
    private ViewTreeObserver focus;
    private ImageView view;
    private Drawable displayed;
    private CancellationSignal signal;
    private long generation;
    private boolean closed = true;
    private boolean blankRequested;
    private boolean again;

    public CaptureThumbnail(Activity current) { this(current, null); }
    public CaptureThumbnail(Activity current, GalleryShortcut gallery) {
        activity = new WeakReference<>(current);
        this.gallery = gallery;
    }

    private void mediaChanged() {
        // A new completed capture invalidates an older in-flight thumbnail.
        generation++;
        if (signal != null) signal.cancel();
        captureRefresh = true;
        schedule();
    }

    static Bitmap squareThumbnail(Bitmap source) {
        int side = Math.min(source.getWidth(), source.getHeight());
        return Bitmap.createBitmap(source, (source.getWidth() - side) / 2,
                (source.getHeight() - side) / 2, side, side);
    }

    private boolean active() {
        Activity current = activity.get();
        if (closed || current == null || current.isFinishing() || current.isDestroyed()) return false;
        KeyguardManager keyguard = current.getSystemService(KeyguardManager.class);
        return keyguard != null && !keyguard.isKeyguardLocked() && !keyguard.isDeviceLocked()
                && current.getWindow().getDecorView().hasWindowFocus();
    }

    public void bind(View target) {
        Activity current = activity.get();
        if (current == null || current.isDestroyed() || !(target instanceof ImageView)
                || !"com.oplus.camera".equals(current.getPackageName())
                || !"com.oplus.camera:id/thumbnail".equals(
                        current.getResources().getResourceName(target.getId()))) return;
        ViewTreeObserver observer = current.getWindow().getDecorView().getViewTreeObserver();
        boolean changed = view != target || focus != observer || closed;
        if (focus != observer) {
            if (focus != null && focus.isAlive()) focus.removeOnWindowFocusChangeListener(this);
            focus = observer;
            if (observer.isAlive()) observer.addOnWindowFocusChangeListener(this);
        }
        if (view != target) { generation++; displayed = null; }
        view = (ImageView)target;
        // MediaStore thumbnails are bounded, not necessarily square. Preserve
        // their aspect ratio when replacing the OEM's already cropped preview.
        view.setScaleType(ImageView.ScaleType.CENTER_CROP);
        closed = false;
        if (!active()) { clearIfLocked(); return; }
        watch(current.getApplicationContext().getContentResolver());
        if (changed || (!blankRequested && view.getDrawable() == null)) {
            captureRefresh = false;
            schedule();
        }
    }

    private void watch(ContentResolver current) {
        if (resolver != null) return;
        try {
            current.registerContentObserver(
                    MediaStore.Files.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY), true, changes);
            resolver = current;
        } catch (SecurityException error) {
            Log.w("RealmeCameraGallery", "Capture notifications unavailable");
        }
    }

    private void schedule() {
        if (!active()) return;
        blankRequested = true;
        main.removeCallbacks(this);
        main.postDelayed(this, 250);
    }

    @Override public void onWindowFocusChanged(boolean focused) {
        if (!focused) {
            generation++;
            if (signal != null) signal.cancel();
            clearIfLocked();
            return;
        }
        Activity current = activity.get();
        if (active() && current != null) {
            watch(current.getApplicationContext().getContentResolver());
            captureRefresh = false;
            schedule();
        }
    }

    private void clearIfLocked() {
        Activity current = activity.get();
        KeyguardManager keyguard = current == null ? null
                : current.getSystemService(KeyguardManager.class);
        if (keyguard != null && (keyguard.isKeyguardLocked() || keyguard.isDeviceLocked())) {
            if (view != null && displayed != null && view.getDrawable() == displayed) {
                view.setImageDrawable(null);
            }
            displayed = null;
        }
    }

    @Override public void run() {
        if (!active()) return;
        if (!busy.compareAndSet(false, true)) { again = true; return; }
        Activity current = activity.get();
        if (current == null) { busy.set(false); return; }
        signal = new CancellationSignal();
        new Thread(new Load(this, current.getApplicationContext(), signal, generation, captureRefresh),
                "CameraCaptureThumbnail").start();
    }

    public void close() {
        generation++;
        closed = true;
        again = false;
        main.removeCallbacks(this);
        if (signal != null) signal.cancel();
        if (focus != null && focus.isAlive()) focus.removeOnWindowFocusChangeListener(this);
        focus = null;
        if (resolver != null) {
            try { resolver.unregisterContentObserver(changes); }
            catch (RuntimeException error) {
                Log.w("RealmeCameraGallery", "Capture notification cleanup unavailable");
            }
        }
        resolver = null;
        clearIfLocked();
        view = null;
        displayed = null;
        blankRequested = false;
    }

    private static final class Changes extends ContentObserver {
        private final CaptureThumbnail owner;
        Changes(Handler handler, CaptureThumbnail owner) { super(handler); this.owner = owner; }
        @Override public void onChange(boolean selfChange) { owner.mediaChanged(); }
        @Override public void onChange(boolean selfChange, Uri uri) { owner.mediaChanged(); }
    }

    private static final class Cancel implements Runnable {
        private final CancellationSignal signal;
        Cancel(CancellationSignal signal) { this.signal = signal; }
        @Override public void run() { signal.cancel(); }
    }

    private static final class Load implements Runnable {
        private final CaptureThumbnail owner;
        private final Context context;
        private final CancellationSignal signal;
        private final long generation;
        private final boolean captureRefresh;
        Load(CaptureThumbnail owner, Context context, CancellationSignal signal, long generation, boolean captureRefresh) {
            this.owner = owner; this.context = context; this.signal = signal;
            this.generation = generation;
            this.captureRefresh = captureRefresh;
        }
        @Override public void run() {
            Bitmap bitmap = null;
            boolean empty = false;
            Cancel timeout = new Cancel(signal);
            owner.main.postDelayed(timeout, 5000);
            try (Cursor cursor = context.getContentResolver().query(
                    MediaStore.Files.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY),
                    new String[]{"_id", "media_type", "mime_type", "owner_package_name"},
                    GalleryShortcut.latestCaptureQuery(), signal)) {
                boolean hasCapture = cursor != null && cursor.moveToFirst();
                empty = cursor != null && !hasCapture;
                if (hasCapture && "com.oplus.camera".equals(cursor.getString(3))) {
                    int media = cursor.getInt(1);
                    Uri base = media == 1 ? MediaStore.Images.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY)
                            : media == 3 ? MediaStore.Video.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY) : null;
                    if (base != null) {
                        Uri uri = ContentUris.withAppendedId(base, cursor.getLong(0));
                        GalleryShortcut.createViewIntent(uri, cursor.getString(2)); // Validate type/URI boundary.
                        bitmap = squareThumbnail(context.getContentResolver().loadThumbnail(
                                uri, new Size(256, 256), signal));
                    }
                }
            } catch (java.io.IOException | RuntimeException error) {
                if (!signal.isCanceled()) Log.w("RealmeCameraGallery",
                        "Capture thumbnail unavailable: " + error.getClass().getSimpleName());
            } finally {
                owner.main.removeCallbacks(timeout);
                owner.main.post(new Deliver(owner, bitmap, empty, generation, captureRefresh));
            }
        }
    }

    private static final class Deliver implements Runnable {
        private final CaptureThumbnail owner;
        private final Bitmap bitmap;
        private final boolean empty;
        private final long generation;
        private final boolean captureRefresh;
        Deliver(CaptureThumbnail owner, Bitmap bitmap, boolean empty, long generation, boolean captureRefresh) {
            this.owner = owner; this.bitmap = bitmap; this.empty = empty; this.generation = generation;
            this.captureRefresh = captureRefresh;
        }
        @Override public void run() {
            owner.busy.set(false);
            if (generation == owner.generation && owner.active() && owner.view != null) {
                if (owner.gallery != null) owner.gallery.bindPopups();
                // Leave the OEM's freshly landed capture intact. Restore through
                // MediaStore on entry/resume, or when we still own the drawable.
                if (bitmap != null && (!captureRefresh || owner.view.getDrawable() == null
                        || owner.view.getDrawable() == owner.displayed)) {
                    owner.view.setScaleType(ImageView.ScaleType.CENTER_CROP);
                    owner.view.setImageBitmap(bitmap);
                    owner.displayed = owner.view.getDrawable();
                } else if (empty && owner.displayed != null && owner.view.getDrawable() == owner.displayed) {
                    owner.view.setImageDrawable(null);
                    owner.displayed = null;
                }
            }
            if (owner.again && owner.active()) { owner.again = false; owner.schedule(); }
        }
    }
}
