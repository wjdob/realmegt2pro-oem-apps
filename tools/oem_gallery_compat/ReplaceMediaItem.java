package realme.gallery.compat;

import java.io.File;
import java.io.IOException;
import java.util.Set;
import java.util.Objects;

/** Immutable selected-row identity; never opens, moves, writes or deletes media. */
public final class ReplaceMediaItem {
    public final long id;
    public final int type;
    public final String path;
    public final String volume;
    public final String owner;

    public ReplaceMediaItem(long id, int type, String path, String volume, String owner,
                            String primary, Set<String> mounted) throws IOException {
        if (id <= 0 || (type != 1 && type != 3) || volume == null || mounted == null
                || !mounted.contains(volume) || !volume.matches("[A-Za-z0-9_-]+")
                || volume.equals("external") || volume.equals("internal")) {
            throw new IllegalArgumentException("not a concrete local media row");
        }
        this.path = canonical(path);
        String root = volume.equals("external_primary") ? canonical(primary) : "/storage/" + volume;
        if (!this.path.startsWith(root + "/")) {
            throw new IllegalArgumentException("media path/volume mismatch");
        }
        this.id = id;
        this.type = type;
        this.volume = volume;
        this.owner = owner;
    }

    private static String canonical(String value) throws IOException {
        if (value == null || value.isEmpty() || value.indexOf('\0') >= 0
                || !new File(value).isAbsolute()) throw new IllegalArgumentException("invalid media path");
        String result = new File(value).getCanonicalPath();
        if (!value.equals(result)) throw new IllegalArgumentException("media path alias");
        return result;
    }

    public boolean matches(long otherId, int otherType, String otherPath,
                           String otherVolume, String otherOwner) throws IOException {
        return id == otherId && type == otherType && path.equals(canonical(otherPath))
                && volume.equals(otherVolume) && Objects.equals(owner, otherOwner);
    }
}
