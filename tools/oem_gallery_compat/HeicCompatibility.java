package realme.gallery.compat;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.ColorSpace;
import android.os.Build;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.lang.reflect.Modifier;

/** Public decoder fallback for the optional Gallery's existing export pipeline. */
public final class HeicCompatibility {
    private static final int MAX_INPUT = 64 * 1024 * 1024;
    private static final long MAX_PIXELS = 100_000_000L;
    private HeicCompatibility() {}

    public static boolean available(Context context) {
        return context != null && "com.coloros.gallery3d".equals(context.getPackageName())
                && Build.VERSION.SDK_INT >= 31 && !hasBootConverter();
    }

    static boolean hasBootConverter() {
        try {
            // Resolve only the platform boot class path. An app compatibility JAR
            // with the same name does not establish a working OEM platform API.
            Class<?> type = Class.forName("com.oplus.media.OplusHeifConverter", false, null);
            java.lang.reflect.Method method = type.getMethod("convertHeifToJpegFromStream",
                    InputStream.class, int.class, OutputStream.class);
            return method.getReturnType() == boolean.class && Modifier.isStatic(method.getModifiers());
        } catch (ReflectiveOperationException | LinkageError absent) {
            return false;
        }
    }

    /** The separate OEM user switch remains opt-in on the public fallback. */
    public static boolean enabled(String saved) {
        return "true".equals(saved);
    }

    public static boolean convert(InputStream input, int quality, OutputStream output) {
        if (input == null || output == null || quality < 0 || quality > 100) return false;
        Bitmap bitmap = null;
        try {
            byte[] encoded = readBounded(input);
            BitmapFactory.Options bounds = new BitmapFactory.Options();
            bounds.inJustDecodeBounds = true;
            BitmapFactory.decodeByteArray(encoded, 0, encoded.length, bounds);
            if (!isHeic(bounds.outMimeType) || bounds.outWidth <= 0 || bounds.outHeight <= 0)
                return false;
            long pixels = (long) bounds.outWidth * bounds.outHeight;
            Runtime runtime = Runtime.getRuntime();
            long available = runtime.maxMemory() - (runtime.totalMemory() - runtime.freeMemory());
            // Keep full dimensions. Refuse excessive allocation instead of silently shrinking.
            if (pixels > MAX_PIXELS || pixels * 6L + encoded.length + 32L * 1024 * 1024 > available)
                return false;
            BitmapFactory.Options options = new BitmapFactory.Options();
            options.inPreferredConfig = Bitmap.Config.ARGB_8888;
            options.inPreferredColorSpace = ColorSpace.get(ColorSpace.Named.SRGB);
            bitmap = BitmapFactory.decodeByteArray(encoded, 0, encoded.length, options);
            if (bitmap == null || Thread.currentThread().isInterrupted()) return false;
            // BitmapFactory may retain RGBA_1010102 for a 10-bit source. The platform
            // JPEG encoder converts that bitmap to an ordinary 8-bit JPEG stream.
            // EXIF/privacy migration and temporary-file lifetime remain with Gallery.
            return bitmap.compress(Bitmap.CompressFormat.JPEG, quality, output)
                    && !Thread.currentThread().isInterrupted();
        } catch (IOException | RuntimeException | OutOfMemoryError failure) {
            return false;
        } finally {
            if (bitmap != null) bitmap.recycle();
        }
    }

    static boolean isHeic(String mime) {
        return "image/heic".equalsIgnoreCase(mime) || "image/heif".equalsIgnoreCase(mime);
    }

    private static byte[] readBounded(InputStream input) throws IOException {
        ByteArrayOutputStream data = new ByteArrayOutputStream();
        byte[] buffer = new byte[32 * 1024];
        int length = 0;
        for (int count; (count = input.read(buffer)) != -1;) {
            if (Thread.currentThread().isInterrupted()) throw new IOException("cancelled");
            if (count == 0) {
                int value = input.read();
                if (value == -1) break;
                if (++length > MAX_INPUT) throw new IOException("input limit");
                data.write(value);
            } else {
                if (count > MAX_INPUT - length) throw new IOException("input limit");
                length += count;
                data.write(buffer, 0, count);
            }
        }
        return data.toByteArray();
    }
}
