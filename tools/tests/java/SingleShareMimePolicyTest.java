import java.util.Objects;
import realme.gallery.compat.SingleShareMimePolicy;

/** Execute the production policy over matching and unsupported input boundaries. */
public final class SingleShareMimePolicyTest {
    public static void main(String[] args) {
        String send = "android.intent.action.SEND";
        String[][] cases = {
            {send, "image/*", "image/jpeg", "image/jpeg"},
            {send, "image/*", "image/png", "image/png"},
            {send, "image/*", "image/heic", "image/heic"},
            {send, "image/*", "image/heif", "image/heif"},
            {send, "image/*", "image/avif", "image/avif"},
            {send, "image/*", "image/x-adobe-dng", "image/x-adobe-dng"},
            {send, "video/*", "video/mp4", "video/mp4"},
            {send, "video/*", "video/webm", "video/webm"},
            {send, "image/*", null, "image/*"},
            {send, "image/*", "image/*", "image/*"},
            {send, "image/*", "video/mp4", "image/*"},
            {send, "video/*", "image/jpeg", "video/*"},
            {send, "image/*", "text/plain", "image/*"},
            {send, "image/*", "image/jpeg; charset=x", "image/*"},
            {send, "image/*", " image/jpeg", "image/*"},
            {send, "image/*", "image/jpeg\n", "image/*"},
            {send, "image/*", "image/", "image/*"},
            {send, "image/jpeg", "image/png", "image/jpeg"},
            {send, "*/*", "image/jpeg", "*/*"},
            {"android.intent.action.SEND_MULTIPLE", "image/*", "image/jpeg", "image/*"},
            {"android.intent.action.VIEW", "image/*", "image/jpeg", "image/*"},
            {null, "image/*", "image/jpeg", "image/*"},
            {send, null, "image/jpeg", null}
        };
        for (int i = 0; i < cases.length; i++) {
            String[] value = cases[i];
            String result = SingleShareMimePolicy.choose(value[0], value[1], value[2]);
            if (!Objects.equals(result, value[3]))
                throw new AssertionError("Case " + i + " expected " + value[3] + " got " + result);
        }
        String[][] families = {{null, null}, {"*/*", null}, {"text/plain", null},
            {"image/*", "image/*"}, {"image/heic", "image/*"},
            {"image/jpeg", "image/*"}, {"video/*", "video/*"}, {"video/mp4", "video/*"}};
        for (String[] entry : families)
            if (!Objects.equals(SingleShareMimePolicy.family(entry[0]), entry[1]))
                throw new AssertionError("Family conversion failed: " + entry[0]);
        if (!"image/jpeg".equals(SingleShareMimePolicy.choose(send,
                SingleShareMimePolicy.family("image/heic"), "image/jpeg")))
            throw new AssertionError("Converted HEIC-to-JPEG stream kept stale type");
        if (!"image/*".equals(SingleShareMimePolicy.choose(send,
                SingleShareMimePolicy.family("image/heic"), null)))
            throw new AssertionError("Unavailable converted metadata changed fallback");
        System.out.println("cases=" + (cases.length + families.length + 2));
    }
}
