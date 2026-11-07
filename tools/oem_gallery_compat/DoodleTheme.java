package realme.gallery.compat;

import android.content.Context;
import android.content.res.Configuration;
import android.util.TypedValue;
import android.view.ContextThemeWrapper;

/** Pinned Gallery toolkit resources; preserve an already configured OEM theme. */
public final class DoodleTheme {
    public static Context context(Context original) {
        TypedValue value = new TypedValue();
        if (original.getTheme().resolveAttribute(0x7f04055c, value, true)
                && value.type != TypedValue.TYPE_ATTRIBUTE
                && value.type != TypedValue.TYPE_NULL) {
            return original;
        }
        boolean night = (original.getResources().getConfiguration().uiMode
                & Configuration.UI_MODE_NIGHT_MASK) == Configuration.UI_MODE_NIGHT_YES;
        return new ContextThemeWrapper(original, night ? 0x7f1402ba : 0x7f1402b9);
    }
}
