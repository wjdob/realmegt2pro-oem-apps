package realme.camera.compat;

import android.app.Activity;
import android.app.KeyguardManager;
import android.content.ActivityNotFoundException;
import android.content.ContentResolver;
import android.content.ContentUris;
import android.content.Context;
import android.content.Intent;
import android.database.Cursor;
import android.net.Uri;
import android.os.Bundle;
import android.os.CancellationSignal;
import android.os.Handler;
import android.os.Looper;
import android.provider.MediaStore;
import android.util.Log;
import android.view.View;
import android.view.ViewTreeObserver;
import android.view.inspector.WindowInspector;
import android.widget.Toast;
import java.lang.ref.WeakReference;
import java.util.concurrent.atomic.AtomicBoolean;

/** Optional, pinned Camera thumbnail bridge to ordinary Android media viewers. */
public final class GalleryShortcut implements View.OnClickListener, ViewTreeObserver.OnGlobalLayoutListener, Runnable {
    private static final String CAMERA = "com.oplus.camera";
    private static final int THUMBNAIL = 0x7f09062f;
    private static final int POPUP_THUMBNAIL = 0x7f090497;
    private final WeakReference<Activity> activity;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final AtomicBoolean busy = new AtomicBoolean();
    private final CaptureThumbnail captureThumbnail;
    private volatile Uri result;
    private volatile String mime;
    private volatile String failure;
    private ViewTreeObserver layout;
    private volatile long generation;
    private volatile long resultGeneration;
    private boolean closed;

    public GalleryShortcut(Activity activity) {
        this.activity = new WeakReference<>(activity);
        this.captureThumbnail = new CaptureThumbnail(activity, this);
    }

    public void bind() {
        Activity current = activity.get();
        if (current == null || current.isDestroyed() || !CAMERA.equals(current.getPackageName())) {
            close(); return;
        }
        ViewTreeObserver observer=current.getWindow().getDecorView().getViewTreeObserver();
        if (layout != observer) {
            close();
            if (observer.isAlive()) { layout=observer; observer.addOnGlobalLayoutListener(this); }
        }
        View thumbnail = current.findViewById(THUMBNAIL);
        if (thumbnail == null) return;
        // Fail closed if a future APK reuses the numeric ID for a different resource.
        if (!"com.oplus.camera:id/thumbnail".equals(
                current.getResources().getResourceName(thumbnail.getId()))) return;
        closed = false;
        thumbnail.setOnClickListener(this);
        captureThumbnail.bind(thumbnail);
        bindPopups();
    }

    /** The OEM's post-capture popup is a separate app window, not an Activity child. */
    void bindPopups() {
        Activity current = activity.get();
        if (closed || !unlocked(current)) return;
        Object token = current.getWindow().getDecorView().getApplicationWindowToken();
        if (token == null) return;
        for (View root : WindowInspector.getGlobalWindowViews()) {
            if (!token.equals(root.getApplicationWindowToken())) continue;
            View target = root.findViewById(POPUP_THUMBNAIL);
            if (target == null || !target.isShown()) continue;
            if (!"com.oplus.camera:id/popup_thumbnail".equals(
                    current.getResources().getResourceName(target.getId()))) continue;
            target.setOnClickListener(this);
        }
    }

    @Override public void onGlobalLayout() { bind(); }

    public void close() {
        closed = true;
        generation++;
        captureThumbnail.close();
        if (layout != null && layout.isAlive()) layout.removeOnGlobalLayoutListener(this);
        layout=null;
    }

    private static boolean unlocked(Activity current) {
        if (current == null || current.isFinishing() || current.isDestroyed()) return false;
        KeyguardManager keyguard = current.getSystemService(KeyguardManager.class);
        return keyguard != null && !keyguard.isKeyguardLocked() && !keyguard.isDeviceLocked()
                && current.getWindow().getDecorView().hasWindowFocus();
    }

    @Override public void onClick(View view) {
        Activity current = activity.get();
        if (closed || !unlocked(current) || !busy.compareAndSet(false, true)) return;
        Context context = current.getApplicationContext();
        new Thread(new Lookup(context, this, generation), "CameraGalleryLookup").start();
    }

    /** Build only a MediaStore photo/video grant. No OEM target or arbitrary URI. */
    public static Intent createViewIntent(Uri uri, String type) {
        if (uri == null || !"content".equals(uri.getScheme()) || !"media".equals(uri.getAuthority())
                || uri.getQuery() != null || uri.getFragment() != null
                || uri.getPath() == null || type == null
                || !((uri.getPath().matches("/external_primary/images/media/[0-9]+") && type.startsWith("image/"))
                     || (uri.getPath().matches("/external_primary/video/media/[0-9]+") && type.startsWith("video/")))) {
            throw new IllegalArgumentException("Expected a primary MediaStore photo/video");
        }
        Intent intent = new Intent(Intent.ACTION_VIEW).setDataAndType(uri, type);
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        return intent;
    }

    /** Match Lineage Aperture: review the capture/album, with plain-view fallback. */
    public static Intent createReviewIntent(Uri uri, String type) {
        return createViewIntent(uri, type).setAction(MediaStore.ACTION_REVIEW);
    }

    static Bundle latestCaptureQuery() {
        Bundle query = new Bundle();
        query.putString(ContentResolver.QUERY_ARG_SQL_SELECTION,
                "owner_package_name=? AND media_type IN (1,3) AND is_pending=0 AND is_trashed=0");
        query.putStringArray(ContentResolver.QUERY_ARG_SQL_SELECTION_ARGS, new String[]{CAMERA});
        // Structured multi-column direction becomes "date_added, _id DESC".
        query.putString(ContentResolver.QUERY_ARG_SQL_SORT_ORDER, "date_added DESC, _id DESC");
        query.putInt(ContentResolver.QUERY_ARG_LIMIT, 1);
        return query;
    }

    @Override public void run() {
        busy.set(false);
        if (closed || resultGeneration != generation) return;
        Activity current = activity.get();
        if (!unlocked(current)) return; // Check again after the asynchronous query.
        if (result == null) {
            Toast.makeText(current, failure == null ? "Take a photo or video first" : failure,
                    Toast.LENGTH_SHORT).show();
            return;
        }
        try {
            // Android applies the existing default, or displays its normal resolver.
            try {
                current.startActivity(createReviewIntent(result, mime));
            } catch (ActivityNotFoundException unsupportedReview) {
                current.startActivity(createViewIntent(result, mime));
            }
        } catch (ActivityNotFoundException error) {
            Toast.makeText(current, "Install a photo or video viewer", Toast.LENGTH_SHORT).show();
        } catch (SecurityException error) {
            Log.w("RealmeCameraGallery", "Media viewer denied the capture grant");
            Toast.makeText(current, "Unable to open this capture", Toast.LENGTH_SHORT).show();
        }
    }

    private static final class Cancel implements Runnable {
        private final CancellationSignal signal;
        Cancel(CancellationSignal signal) { this.signal = signal; }
        @Override public void run() { signal.cancel(); }
    }

    private static final class Lookup implements Runnable {
        private final Context context;
        private final GalleryShortcut owner;
        private final long generation;
        Lookup(Context context, GalleryShortcut owner, long generation) {
            this.context = context; this.owner = owner; this.generation = generation;
        }
        @Override public void run() {
            CancellationSignal signal = new CancellationSignal();
            Cancel timeout = new Cancel(signal);
            owner.main.postDelayed(timeout, 5000);
            owner.result = null;
            owner.mime = null;
            owner.failure = null;
            Bundle query = latestCaptureQuery();
            String[] columns = {"_id", "media_type", "mime_type", "owner_package_name"};
            Uri collection = MediaStore.Files.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY);
            try (Cursor cursor = context.getContentResolver().query(collection, columns, query, signal)) {
                if (cursor != null && cursor.moveToFirst() && CAMERA.equals(cursor.getString(3))) {
                    int media = cursor.getInt(1);
                    String type = cursor.getString(2);
                    if ((media == 1 && type != null && type.startsWith("image/"))
                            || (media == 3 && type != null && type.startsWith("video/"))) {
                        Uri base = media == 1
                                ? MediaStore.Images.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY)
                                : MediaStore.Video.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY);
                        owner.result = ContentUris.withAppendedId(base, cursor.getLong(0));
                        owner.mime = type;
                    }
                }
            } catch (RuntimeException error) {
                Log.w("RealmeCameraGallery", "Capture lookup failed: " + error.getClass().getSimpleName());
                owner.failure = "Unable to find a saved Camera capture";
            } finally {
                owner.main.removeCallbacks(timeout);
                owner.resultGeneration = generation;
                owner.main.post(owner);
            }
        }
    }
}
