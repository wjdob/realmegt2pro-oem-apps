package realme.gallery.compat;

import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Parcelable;

/** Read URI type metadata only. Preserve the media, privacy choice and grants. */
public final class SingleShareMime {
    private SingleShareMime() {}

    public static Intent refine(Context context, Intent original) {
        if (context == null || original == null || original.getComponent() != null
                || !Intent.ACTION_SEND.equals(original.getAction())
                || !("image/*".equals(original.getType()) || "video/*".equals(original.getType())))
            return original;
        String precise = resolvedType(context, original, original.getType());
        if (precise == null || original.getType().equals(precise)) return original;
        return new Intent(original).setDataAndType(original.getData(), precise);
    }

    /** The OEM privacy/format converter replaces streams on this same intent. */
    public static void refresh(Context context, Intent original) {
        if (context == null || original == null
                || !Intent.ACTION_SEND.equals(original.getAction())) return;
        String advertised = SingleShareMimePolicy.family(original.getType());
        if (advertised == null) return;
        String precise = resolvedType(context, original, advertised);
        if (precise != null && !precise.equals(original.getType()))
            original.setDataAndType(original.getData(), precise);
    }

    private static String resolvedType(Context context, Intent original, String advertised) {
        try {
            Parcelable stream = original.getParcelableExtra(Intent.EXTRA_STREAM);
            if (!(stream instanceof Uri)) return null;
            Uri uri = (Uri) stream;
            if (!"content".equals(uri.getScheme())) return null;
            String resolved = context.getContentResolver().getType(uri);
            String precise = SingleShareMimePolicy.choose(original.getAction(), advertised, resolved);
            return advertised.equals(precise) ? null : precise;
        } catch (RuntimeException unavailable) {
            // Optional metadata can be unavailable; preserve the OEM intent.
            return null;
        }
    }
}
