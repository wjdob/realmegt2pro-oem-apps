package realme.gallery.compat;

import java.io.File;
import java.io.IOException;
import java.util.Set;

/** Read-only snapshot validation for the version-bound OEM move record. */
public final class MoveMediaItem {
    public final long id;
    public final int type;
    public final String source;
    public final String target;
    public final String volume;

    public MoveMediaItem(long id, int type, String source, String target,
                         String primaryRoot, Set<String> externalVolumes) throws IOException {
        if (id <= 0 || (type != 1 && type != 3)) {
            throw new IllegalArgumentException("not a local image/video record");
        }
        this.id = id;
        this.type = type;
        this.source = canonical(source);
        this.target = canonical(target);
        this.volume = volume(this.source, canonical(primaryRoot), externalVolumes);
        // Cross-volume moves use a separate copy/delete path and are not covered.
        if (!volume.equals(volume(this.target, canonical(primaryRoot), externalVolumes))
                || this.source.equals(this.target)) {
            throw new IllegalArgumentException("unsupported move boundary");
        }
    }

    private static String canonical(String path) throws IOException {
        if (path == null || path.isEmpty() || path.indexOf('\0') >= 0
                || !new File(path).isAbsolute()) throw new IllegalArgumentException("invalid media path");
        String result = new File(path).getCanonicalPath();
        // Never silently normalize a traversal or alias into a different selected item.
        if (!path.equals(result)) throw new IllegalArgumentException("noncanonical media path");
        return result;
    }

    private static String volume(String path, String primary, Set<String> volumes) {
        if (path.startsWith(primary + "/") && volumes.contains("external_primary")) {
            return "external_primary";
        }
        for (String volume : volumes) {
            if (volume == null || volume.equals("external_primary")
                    || !volume.matches("[A-Za-z0-9_-]+")) continue;
            if (path.startsWith("/storage/" + volume + "/")) return volume;
        }
        throw new IllegalArgumentException("path outside the current user's mounted media");
    }

    public boolean matches(long rowId, String rowPath) throws IOException {
        return id == rowId && source.equals(canonical(rowPath));
    }
}
