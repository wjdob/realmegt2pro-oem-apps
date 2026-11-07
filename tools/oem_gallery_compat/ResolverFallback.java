package realme.gallery.compat;

import android.content.Context;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.graphics.drawable.Drawable;

/** Used only when the OEM resolver helper could not produce an item. */
public final class ResolverFallback {
    public static Drawable icon(Context context, ResolveInfo info) {
        if (context == null || info == null) return null;
        PackageManager packages = context.getPackageManager();
        try {
            Drawable icon = info.loadIcon(packages);
            if (icon != null) return icon;
        } catch (android.content.res.Resources.NotFoundException | SecurityException unavailable) {
            // A package can disappear while the chooser is being populated.
        }
        return packages.getDefaultActivityIcon();
    }

    public static String label(Context context, ResolveInfo info) {
        if (info == null) return "";
        if (context != null) {
            try {
                CharSequence label = info.loadLabel(context.getPackageManager());
                if (label != null && label.length() > 0) return label.toString();
            } catch (android.content.res.Resources.NotFoundException | SecurityException unavailable) {
                // Keep a stable package label when its label resource vanishes.
            }
        }
        return info.activityInfo == null || info.activityInfo.packageName == null
                ? "" : info.activityInfo.packageName;
    }
}
