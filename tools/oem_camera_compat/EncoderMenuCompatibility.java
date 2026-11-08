package realme.camera.compat;

import android.app.Activity;
import android.media.MediaCodecInfo;
import android.media.MediaCodecList;
import android.util.Log;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.io.FileInputStream;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.List;

/** Reconcile the pinned Camera's menu with encoders advertised by this platform. */
public final class EncoderMenuCompatibility {
    private static volatile Boolean reviewed;
    private static volatile Object[] observed;
    private EncoderMenuCompatibility() {}

    public static boolean apply(Activity activity) {
        if (activity == null || !"com.oplus.camera".equals(activity.getPackageName())
                || activity.isDestroyed()) return false;
        if (!reviewedApk(activity)) return false;
        try {
            ClassLoader loader = activity.getClassLoader();
            Class<?> config = Class.forName("com.oplus.camera.common.config.CameraSettingsConfig", false, loader);
            Class<?> option = Class.forName("com.oplus.camera.common.config.OptionItemConfig", false, loader);
            Class<?> util = Class.forName("com.oplus.camera.util.Util", false, loader);
            Class<?> codecs = Class.forName("c9.b", false, loader);
            Field entries = config.getDeclaredField("mOptionItemConfigList");
            Field utilCache = util.getDeclaredField("r");
            Field codecCache = codecs.getDeclaredField("a");
            if (entries.getType() != List.class || utilCache.getType() != Boolean.class
                    || codecCache.getType() != Boolean.class) return false;
            entries.setAccessible(true);utilCache.setAccessible(true);codecCache.setAccessible(true);
            Method getKey = option.getMethod("getKey");
            Method getList = option.getMethod("getSupportedList");
            Method setKey = option.getMethod("setKey", String.class);
            Method add = option.getMethod("addOptionItem", String.class);
            Object value = entries.get(null);
            if (value == null || value.getClass() != ArrayList.class) return false;
            @SuppressWarnings("unchecked") List<Object> items = (List<Object>) value;
            if (items.isEmpty()) return false;
            Object[] previous = observed;
            if (previous != null && previous.length == items.size()) {
                boolean same = true;
                for (int n = 0; n < previous.length; n++) same &= previous[n] == items.get(n);
                if (same) return true;
            }
            List<MediaCodecInfo.VideoCapabilities> avc = new ArrayList<>();
            List<MediaCodecInfo.VideoCapabilities> hevc = new ArrayList<>();
            for (MediaCodecInfo info : new MediaCodecList(MediaCodecList.ALL_CODECS).getCodecInfos()) {
                if (!info.isEncoder()) continue;
                for (String mime : info.getSupportedTypes()) {
                    if (!("video/avc".equalsIgnoreCase(mime) || "video/hevc".equalsIgnoreCase(mime))) continue;
                    MediaCodecInfo.CodecCapabilities caps = info.getCapabilitiesForType(mime);
                    boolean surface = false;
                    for (int format : caps.colorFormats)
                        surface |= format == MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface;
                    if (surface && caps.getVideoCapabilities() != null)
                        ("video/avc".equalsIgnoreCase(mime) ? avc : hevc).add(caps.getVideoCapabilities());
                }
            }
            // An incomplete codec inventory cannot justify removing any menu option.
            if (avc.isEmpty() || hevc.isEmpty()) return false;
            List<Integer> indices = new ArrayList<>();
            List<Object> originals = new ArrayList<>(), replacements = new ArrayList<>();
            for (int n = 0; n < items.size(); n++) {
                Object old = items.get(n);
                if (!option.isInstance(old)) return false;
                String key = (String) getKey.invoke(old);
                if (!("pref_video_size_key".equals(key)
                        || "pref_video_size_key_back_camera_supported".equals(key)
                        || "pref_video_size_key_front_camera_supported".equals(key))) continue;
                Object values = getList.invoke(old);
                if (!(values instanceof List)) return false;
                List<String> kept = new ArrayList<>();
                for (Object item : (List<?>) values) {
                    if (!(item instanceof String)) return false;
                    String size = (String) item;
                    int[] dimensions = dimensions(size);
                    if (dimensions == null || supports(avc, dimensions) || supports(hevc, dimensions)) kept.add(size);
                }
                if (kept.isEmpty()) return false;
                if (kept.size() == ((List<?>) values).size()) continue;
                Object next = option.getConstructor().newInstance();
                setKey.invoke(next, key);
                for (String size : kept) add.invoke(next, size);
                indices.add(n);originals.add(old);replacements.add(next);
            }
            if (indices.isEmpty()) { observed = items.toArray(); return true; }
            // All reflection, codec reads and replacement allocation precede mutation.
            // List.set retains existing iterators and old list snapshots.
            synchronized (config) {
                for (int n = 0; n < indices.size(); n++)
                    if (items.get(indices.get(n)) != originals.get(n)) return false;
                Boolean previousUtil = (Boolean) utilCache.get(null);
                Boolean previousCodec = (Boolean) codecCache.get(null);
                int changed = 0;
                try {
                    for (; changed < indices.size(); changed++) items.set(indices.get(changed), replacements.get(changed));
                    utilCache.set(null, null);codecCache.set(null, null);
                } catch (RuntimeException | IllegalAccessException failed) {
                    for (int n = changed - 1; n >= 0; n--) items.set(indices.get(n), originals.get(n));
                    utilCache.set(null, previousUtil);codecCache.set(null, previousCodec);
                    throw failed;
                }
            }
            observed = items.toArray();
            Log.i("RealmeCameraFormats", "Reconciled video menu with advertised AVC/HEVC encoders");
            return true;
        } catch (ReflectiveOperationException | RuntimeException | LinkageError unavailable) {
            Log.w("RealmeCameraFormats", "Video menu compatibility unavailable");
            return false;
        }
    }

    private static boolean reviewedApk(Activity activity) {
        if (reviewed != null) return reviewed;
        synchronized (EncoderMenuCompatibility.class) {
            if (reviewed != null) return reviewed;
            try (FileInputStream input = new FileInputStream(activity.getApplicationInfo().sourceDir)) {
                MessageDigest digest = MessageDigest.getInstance("SHA-256");
                byte[] buffer = new byte[64 * 1024];
                for (int count; (count = input.read(buffer)) != -1;) digest.update(buffer,0,count);
                StringBuilder hex = new StringBuilder();
                for (byte b : digest.digest()) hex.append(String.format(java.util.Locale.ROOT,"%02x",b & 255));
                reviewed = "9193280507b65cd35b0dda690d86a9f041392ba832fe5b036900207e409f0850".equals(hex.toString());
            } catch (Exception unavailable) { reviewed = false; }
            return reviewed;
        }
    }

    private static boolean supports(List<MediaCodecInfo.VideoCapabilities> codecs, int[] size) {
        for (MediaCodecInfo.VideoCapabilities caps : codecs)
            if (caps.isSizeSupported(size[0], size[1])) return true;
        return false;
    }

    private static int[] dimensions(String size) {
        if ("video_size_8k".equals(size)) return new int[]{7680,4320};
        if ("video_size_4kuhd".equals(size)) return new int[]{3840,2160};
        if ("video_size_2k".equals(size)) return new int[]{2560,1440};
        if ("video_size_1080p".equals(size)) return new int[]{1920,1080};
        if ("video_size_720p".equals(size)) return new int[]{1280,720};
        return null;
    }
}
