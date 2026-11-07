package realme.gallery.compat;

/** Exact single-media types avoid unrelated aliases in an implicit chooser. */
public final class SingleShareMimePolicy {
    private SingleShareMimePolicy() {}

    public static String family(String type) {
        if (type == null) return null;
        if (type.startsWith("image/")) return "image/*";
        if (type.startsWith("video/")) return "video/*";
        return null;
    }

    public static String choose(String action, String advertised, String resolved) {
        if (!"android.intent.action.SEND".equals(action)
                || !("image/*".equals(advertised) || "video/*".equals(advertised))
                || resolved == null || !resolved.matches("[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+"))
            return advertised;
        String family = advertised.substring(0, advertised.indexOf('/')) + "/";
        return resolved.startsWith(family) ? resolved : advertised;
    }
}
