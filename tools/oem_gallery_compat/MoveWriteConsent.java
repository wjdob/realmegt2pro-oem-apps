package realme.gallery.compat;

import android.app.Activity;
import android.app.Fragment;
import android.app.KeyguardManager;
import android.content.ContentUris;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.os.Process;
import android.os.SystemClock;
import android.provider.MediaStore;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.function.LongSupplier;

/** Prototype for the pinned MoveToOperation only; never writes a file or grants itself access. */
public final class MoveWriteConsent {
    private static final int REQUEST = 19767;
    private static final int MAX_BATCH = 1000;
    private static final Handler MAIN = new Handler(Looper.getMainLooper());

    private static boolean cancelled(Object operation) throws ReflectiveOperationException {
        return operation.getClass().getField("m").getBoolean(operation);
    }

    private static boolean unlocked(Context context) {
        KeyguardManager manager = context.getSystemService(KeyguardManager.class);
        return manager != null && !manager.isDeviceLocked();
    }

    private static Uri uri(MoveMediaItem item) {
        Uri collection = item.type == 1 ? MediaStore.Images.Media.getContentUri(item.volume)
                : MediaStore.Video.Media.getContentUri(item.volume);
        return ContentUris.withAppendedId(collection, item.id);
    }

    private static boolean writable(Context context, Uri uri, String owner) {
        return context.getPackageName().equals(owner)
                || context.checkUriPermission(uri, Process.myPid(), Process.myUid(),
                        Intent.FLAG_GRANT_WRITE_URI_PERMISSION) == PackageManager.PERMISSION_GRANTED;
    }

    private static String verifyRow(Context context, MoveMediaItem item) throws Exception {
        String[] columns = {"_id", "_data", "owner_package_name"};
        try (Cursor cursor = context.getContentResolver().query(uri(item), columns, null, null, null)) {
            if (cursor == null || !cursor.moveToFirst() || !item.matches(cursor.getLong(0), cursor.getString(1))) {
                throw new IllegalArgumentException("selected media changed");
            }
            String owner = cursor.getString(2);
            if (cursor.moveToNext()) throw new IllegalArgumentException("ambiguous media row");
            return owner;
        }
    }

    /** Called on the existing worker, before its global move lock or first batch mutation. */
    public static boolean authorize(Context input, final Object operation, List<?> batch) {
        if (Build.VERSION.SDK_INT < 30) return true;
        if (input == null || operation == null || batch == null || batch.size() > MAX_BATCH
                || Looper.myLooper() == Looper.getMainLooper()) return false;
        if (batch.isEmpty()) return true;
        final Context context = input.getApplicationContext();
        if (context == null) return false;
        final ArrayList<MoveMediaItem> items = new ArrayList<>();
        final ArrayList<Uri> requests = new ArrayList<>();
        ConsentFragment fragment = null;
        try {
            if (cancelled(operation) || !unlocked(context)) return false;
            // Preserve an already authorized OEM installation's original route.
            // This helper never requests or enables all-files/media-management access.
            if (Environment.isExternalStorageManager()) return true;
            Set<String> volumes = MediaStore.getExternalVolumeNames(context);
            Set<Uri> unique = new HashSet<>();
            String primary = Environment.getExternalStorageDirectory().getCanonicalPath();
            for (Object record : batch) {
                if (record == null || !record.getClass().getName().equals("com.oplus.aiunit.vision.u11")) return false;
                Class<?> type = record.getClass();
                MoveMediaItem item = new MoveMediaItem(type.getField("b").getInt(record),
                        type.getField("k").getInt(record), (String) type.getField("d").get(record),
                        (String) type.getField("n").get(record), primary, volumes);
                Uri uri = uri(item);
                if (!unique.add(uri)) return false;
                if (!writable(context, uri, verifyRow(context, item))) requests.add(uri);
                items.add(item);
            }
            if (!requests.isEmpty()) {
                final MoveConsentState state = new MoveConsentState(new LongSupplier() {
                    public long getAsLong() { return SystemClock.elapsedRealtime(); }
                }, 180000);
                final ConsentFragment prompt = new ConsentFragment();
                fragment = prompt;
                prompt.state = state;
                prompt.requests = requests;
                MAIN.post(new Runnable() {
                    public void run() {
                        try {
                            if (!state.pending() || cancelled(operation) || !unlocked(context)) {
                                state.finish(false); return;
                            }
                            // This public getter is proven in the pinned md9 operation.
                            Object host = operation.getClass().getMethod("x").invoke(operation);
                            if (!(host instanceof Activity)) { state.finish(false); return; }
                            Activity activity = (Activity) host;
                            if (activity.isFinishing() || activity.isDestroyed()
                                    || activity.getFragmentManager().isStateSaved()) {
                                state.finish(false); return;
                            }
                            activity.getFragmentManager().beginTransaction().add(prompt,
                                    "realme-gallery-move-" + System.identityHashCode(prompt)).commitNow();
                        } catch (Exception failure) { state.finish(false); }
                    }
                });
                if (!state.await()) return false;
            }
            if (cancelled(operation) || !unlocked(context)) return false;
            // Revalidate row identity and actual write grants after consent, before caller mutates.
            for (MoveMediaItem item : items) {
                if (!writable(context, uri(item), verifyRow(context, item))) return false;
            }
            // The selected OEM records must still describe this exact snapshot.
            if (batch.size() != items.size()) return false;
            for (int i = 0; i < items.size(); i++) {
                Object record = batch.get(i); MoveMediaItem item = items.get(i);
                if (record == null || !record.getClass().getName().equals("com.oplus.aiunit.vision.u11")) return false;
                Class<?> type = record.getClass();
                if (type.getField("b").getInt(record) != item.id || type.getField("k").getInt(record) != item.type
                        || !item.source.equals(type.getField("d").get(record))
                        || !item.target.equals(type.getField("n").get(record))) return false;
            }
            return true;
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt(); return false;
        } catch (Exception failure) { return false;
        } finally {
            if (fragment != null) {
                final ConsentFragment cleanup = fragment;
                MAIN.post(new Runnable() { public void run() { cleanup.close(); } });
            }
        }
    }

    /** Internal, non-exported fragment: rotation retains the request; process death cannot resume a move. */
    public static final class ConsentFragment extends Fragment {
        private MoveConsentState state;
        private ArrayList<Uri> requests;
        private boolean launched;

        public ConsentFragment() {}

        @Override public void onCreate(Bundle saved) {
            super.onCreate(saved);
            setRetainInstance(true);
        }

        @Override public void onResume() {
            super.onResume();
            if (state == null || !state.pending()) { close(); return; }
            if (launched) return;
            launched = true;
            try {
                if (!unlocked(getActivity())) { state.finish(false); return; }
                startIntentSenderForResult(MediaStore.createWriteRequest(
                        getActivity().getContentResolver(), requests).getIntentSender(),
                        REQUEST, null, 0, 0, 0, null);
            } catch (Exception failure) { state.finish(false); }
        }

        @Override public void onActivityResult(int request, int result, Intent data) {
            super.onActivityResult(request, result, data);
            if (request == REQUEST && state != null) {
                state.finish(result == Activity.RESULT_OK);
                close();
            }
        }

        @Override public void onDestroy() {
            if (state != null) state.finish(false);
            super.onDestroy();
        }

        private void close() {
            if (state != null) state.finish(false);
            requests = null;
            if (isAdded()) {
                getFragmentManager().beginTransaction().remove(this).commitAllowingStateLoss();
            }
        }
    }
}
