import java.nio.file.Path;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import org.jf.dexlib2.DexFileFactory;
import org.jf.dexlib2.Opcodes;
import org.jf.dexlib2.iface.ClassDef;
import org.jf.dexlib2.immutable.ImmutableDexFile;

/** Move one reviewed class to a new DEX without growing the original string pool. */
public final class GalleryDexRelocate {
    private static Map<String, ClassDef> read(String path) throws Exception {
        Map<String, ClassDef> result = new TreeMap<>();
        for (ClassDef value : DexFileFactory.loadDexFile(Path.of(path).toFile(), Opcodes.forApi(31)).getClasses())
            if (result.put(value.getType(), value) != null)
                throw new IllegalArgumentException("Duplicate class");
        return result;
    }
    public static void main(String[] args) throws Exception {
        if (args.length != 7) throw new IllegalArgumentException("original remaining moved target adapter helpers helperType");
        Map<String, ClassDef> original = read(args[0]);
        Map<String, ClassDef> moved = read(args[4]);
        Map<String, ClassDef> helpers = read(args[5]);
        if (!original.containsKey(args[3]) || !moved.keySet().equals(Set.of(args[3]))
                || !helpers.keySet().equals(Set.of(args[6])) || original.containsKey(args[6])
                || !args[6].startsWith("Lrealme/gallery/compat/"))
            throw new IllegalArgumentException("Unexpected move/helper set");
        original.remove(args[3]);
        moved.putAll(helpers);
        DexFileFactory.writeDexFile(args[1], new ImmutableDexFile(Opcodes.forApi(31), original.values()));
        DexFileFactory.writeDexFile(args[2], new ImmutableDexFile(Opcodes.forApi(31), moved.values()));
    }
}
