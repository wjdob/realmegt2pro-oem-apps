package realme.clock.compat;

import android.content.Context;
import android.media.RingtoneManager;
import android.net.Uri;

/** Preserve an accessible OEM default sound, otherwise use Android's alarm setting. */
public final class AlarmMedia {
    private AlarmMedia() { }
    public static Uri getDefaultAlarmUri(Context context) {
        try {
            Object value = Class.forName("com.oplus.media.MediaFile")
                    .getMethod("getDefaultAlarmUri", Context.class).invoke(null, context);
            if (value instanceof Uri) return (Uri) value;
        } catch (ReflectiveOperationException | LinkageError | SecurityException unavailable) { }
        Uri selected = RingtoneManager.getActualDefaultRingtoneUri(context, RingtoneManager.TYPE_ALARM);
        return selected != null ? selected : RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM);
    }
}
