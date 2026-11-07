import java.nio.file.Path;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import org.jf.dexlib2.DexFileFactory;
import org.jf.dexlib2.Opcodes;
import org.jf.dexlib2.iface.ClassDef;
import org.jf.dexlib2.immutable.ImmutableDexFile;

/** Preserve original class definitions, substituting exactly the named adapter. */
public final class GalleryDexMerge {
    private static Map<String, ClassDef> read(String path) throws Exception {
        Map<String, ClassDef> result = new TreeMap<>();
        for (ClassDef value : DexFileFactory.loadDexFile(Path.of(path).toFile(), Opcodes.forApi(31)).getClasses())
            if (result.put(value.getType(), value) != null)
                throw new IllegalArgumentException("Duplicate class");
        return result;
    }
    public static void main(String[] args) throws Exception {
        if (args.length < 4) throw new IllegalArgumentException("original output target adapter [helpers]");
        Map<String, ClassDef> classes = read(args[0]);
        Map<String, ClassDef> adapter = read(args[3]);
        if (!classes.containsKey(args[2]) || !adapter.keySet().equals(Set.of(args[2])))
            throw new IllegalArgumentException("Unexpected replacement set");
        classes.put(args[2], adapter.get(args[2]));
        for (int i = 4; i < args.length; i++) {
            for (ClassDef value : read(args[i]).values()) {
                if (!value.getType().startsWith("Lrealme/gallery/compat/")
                        || classes.putIfAbsent(value.getType(), value) != null)
                    throw new IllegalArgumentException("Unexpected helper or duplicate");
            }
        }
        DexFileFactory.writeDexFile(args[1], new ImmutableDexFile(Opcodes.forApi(31), classes.values()));
    }
}
