package realme.gallery.compat;

import android.app.AppOpsManager;
import android.app.KeyguardManager;
import android.content.ContentUris;
import android.content.ContentValues;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.os.Process;
import android.provider.MediaStore;
import java.util.Collections;
import java.util.Map;

/** Pre-save authorization for the exact QueryOriginFileTask/SaveData ABI. */
public final class ReplaceOriginalAccess {
    private static final String SCOPE = "realme.gallery.compat.media_scope";
    private static final String PROCESS = "KEY_ORIGIN_FILE_PROCESS_TYPE";
    private static final String ORIGIN = "KEY_ORIGIN_CONTENT_VALUES";

    @SuppressWarnings("unchecked")
    private static Map<String, Object> values(Object data) throws ReflectiveOperationException {
        if (data == null || !data.getClass().getName().equals("com.oplus.aiunit.vision.fid")) {
            throw new IllegalArgumentException("unreviewed save data");
        }
        Object map = data.getClass().getField("a").get(data);
        if (!(map instanceof Map)) throw new IllegalArgumentException("missing save data map");
        return (Map<String, Object>) map;
    }

    /** Capture the existing IO coroutine scope, not a new or uncancellable job. */
    public static void attach(Object data, Object scope) {
        if (Build.VERSION.SDK_INT < 31) return;
        try {
            Map<String, Object> values = values(data);
            Object previous = values.get(SCOPE);
            if (scope == null || (previous != null && previous != scope)) {
                throw new IllegalArgumentException("save scope changed");
            }
            values.put(SCOPE, scope);
        } catch (ReflectiveOperationException failure) {
            throw new IllegalStateException("save scope unavailable", failure);
        }
    }

    private static String process(Map<String, Object> values) {
        Object value = values.get(PROCESS);
        if (!(value instanceof Enum)
                || !value.getClass().getName().equals(
                        "com.oplus.gallery.photoeditor.editingvvm.output.OriginFileProcessType")) {
            throw new IllegalArgumentException("unreviewed original-file operation");
        }
        String name = ((Enum<?>) value).name();
        if (!name.equals("NONE") && !name.equals("RECYCLE") && !name.equals("DELETE")) {
            throw new IllegalArgumentException("unknown original-file operation");
        }
        return name;
    }

    private static Uri uri(ReplaceMediaItem item) {
        Uri collection = item.type == 1 ? MediaStore.Images.Media.getContentUri(item.volume)
                : MediaStore.Video.Media.getContentUri(item.volume);
        return ContentUris.withAppendedId(collection, item.id);
    }

    private static ReplaceMediaItem row(Context context, ContentValues original) throws Exception {
        Long selected = original.getAsLong("media_id");
        String expectedPath = original.getAsString("_data");
        if (selected == null || selected <= 0 || expectedPath == null) {
            throw new IllegalArgumentException("missing original media identity");
        }
        String[] columns = {"_id", "media_type", "_data", "volume_name", "owner_package_name",
                "is_pending", "is_trashed"};
        Uri aggregate = ContentUris.withAppendedId(MediaStore.Files.getContentUri("external"), selected);
        try (Cursor cursor = context.getContentResolver().query(aggregate, columns, null, null, null)) {
            if (cursor == null || !cursor.moveToFirst() || cursor.getLong(0) != selected
                    || !expectedPath.equals(cursor.getString(2))
                    || cursor.getInt(5) != 0 || cursor.getInt(6) != 0) {
                throw new IllegalArgumentException("selected original changed or is not public media");
            }
            ReplaceMediaItem result = new ReplaceMediaItem(cursor.getLong(0), cursor.getInt(1),
                    cursor.getString(2), cursor.getString(3), cursor.getString(4),
                    Environment.getExternalStorageDirectory().getCanonicalPath(),
                    MediaStore.getExternalVolumeNames(context));
            if (cursor.moveToNext()) throw new IllegalArgumentException("ambiguous original row");
            return result;
        }
    }

    private static boolean writable(Context context, ReplaceMediaItem item) {
        if (context.getPackageName().equals(item.owner)
                || context.checkUriPermission(uri(item), Process.myPid(), Process.myUid(),
                        Intent.FLAG_GRANT_WRITE_URI_PERMISSION) == PackageManager.PERMISSION_GRANTED) return true;
        // Preserve an existing OEM gallery/file-manager authorization. This code only
        // queries this app's effective access; it never assigns a role or app-op.
        if (Environment.isExternalStorageManager()) return true;
        AppOpsManager ops = context.getSystemService(AppOpsManager.class);
        String operation = item.type == 1 ? "android:write_media_images" : "android:write_media_video";
        return ops != null && ops.unsafeCheckOpNoThrow(operation, Process.myUid(),
                context.getPackageName()) == AppOpsManager.MODE_ALLOWED;
    }

    /** Runs after the original source query closes its cursor, before the save tasks. */
    public static void authorize(Object data) {
        if (Build.VERSION.SDK_INT < 31) return;
        try {
            final Map<String, Object> values = values(data);
            final String action = process(values);
            if (action.equals("NONE")) return; // Save copy needs no access to replace the original.
            Object rawContext = values.get("KEY_CONTEXT");
            Object rawOriginal = values.get(ORIGIN);
            if (!(rawContext instanceof Context) || !(rawOriginal instanceof ContentValues)) {
                throw new IllegalArgumentException("missing original-file query result");
            }
            final Context context = ((Context) rawContext).getApplicationContext();
            if (context == null || !"com.coloros.gallery3d".equals(context.getPackageName())) {
                throw new IllegalArgumentException("unreviewed Gallery package");
            }
            final Object scope = values.get(SCOPE);
            final ContentValues original = new ContentValues((ContentValues) rawOriginal);
            final ReplaceMediaItem selected = row(context, original);
            final MediaWriteConsent.Check check = () -> {
                try {
                    if (!MediaSaveScope.active(scope)) return false;
                    KeyguardManager keyguard = context.getSystemService(KeyguardManager.class);
                    Object current = values.get(ORIGIN);
                    if (keyguard == null || keyguard.isDeviceLocked() || !action.equals(process(values))
                            || values.get(SCOPE) != scope || !(current instanceof ContentValues)) return false;
                    ContentValues actual = (ContentValues) current;
                    return original.getAsLong("media_id").equals(actual.getAsLong("media_id"))
                            && selected.path.equals(actual.getAsString("_data"));
                } catch (Exception unavailable) { return false; }
            };
            if (!check.ready()) throw new IllegalStateException("save cancelled or source changed");
            if (!writable(context, selected)
                    && !MediaWriteConsent.request(context, Collections.singletonList(uri(selected)), check)) {
                throw new IllegalStateException("media request not approved");
            }
            ReplaceMediaItem after = row(context, original);
            if (!check.ready() || !selected.matches(after.id, after.type, after.path, after.volume, after.owner)
                    || !writable(context, after)) throw new IllegalStateException("media access changed");
        } catch (Exception unavailable) {
            // The original task runner catches this through its normal failed-save path.
            // No replacement file, recycle mutation or success result has been produced here.
            throw new SecurityException("Media change was not authorized. Save copy is still available.", unavailable);
        }
    }
}
