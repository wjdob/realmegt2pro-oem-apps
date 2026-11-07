package realme.gallery.compat;

/** Small protocol boundary shared by the adapter and host-side negative tests. */
public final class ReviewRequest {
    public static boolean accepts(String action, String scheme, String mime) {
        return "android.provider.action.REVIEW".equals(action)
                && "content".equals(scheme) && mime != null
                && ((mime.startsWith("image/") && mime.length() > 6)
                    || (mime.startsWith("video/") && mime.length() > 6));
    }
}
