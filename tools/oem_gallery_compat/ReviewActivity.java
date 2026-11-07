package realme.gallery.compat;

import android.app.Activity;
import android.app.KeyguardManager;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.os.UserManager;

/** Add the standard review entry without changing the OEM viewer's old entry. */
public final class ReviewActivity extends Activity {
    @Override protected void onCreate(Bundle state) {
        super.onCreate(state);
        try {
            KeyguardManager keyguard = getSystemService(KeyguardManager.class);
            UserManager users = getSystemService(UserManager.class);
            Intent incoming = getIntent();
            Uri uri = incoming == null ? null : incoming.getData();
            if (users == null || !users.isUserUnlocked()
                    || keyguard == null || keyguard.isDeviceLocked()
                    || incoming == null || !"android.provider.action.REVIEW".equals(incoming.getAction())
                    || uri == null || !"content".equals(uri.getScheme())) return;
            String mime = incoming.getType();
            if (mime == null) mime = getContentResolver().getType(uri);
            if (!ReviewRequest.accepts(incoming.getAction(), uri.getScheme(), mime)) return;
            // Forward only the ordinary public-media review contract, with one
            // URI and a temporary read grant. No secure/private OEM extras.
            Intent review = new Intent("com.android.camera.action.REVIEW")
                    .setClassName(getPackageName(), "com.oppo.gallery3d.app.ViewGallery")
                    .setDataAndType(uri, mime)
                    .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
            startActivity(review);
        } catch (android.content.ActivityNotFoundException | SecurityException unavailable) {
            // Refuse an unavailable or unreadable route, never broaden a grant.
        } finally {
            finish();
        }
    }
}
