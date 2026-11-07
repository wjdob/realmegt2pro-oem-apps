import java.nio.file.Path;
import java.util.Arrays;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.TreeSet;
import org.jf.dexlib2.DexFileFactory;
import org.jf.dexlib2.Opcodes;
import org.jf.dexlib2.iface.ClassDef;
import org.jf.dexlib2.immutable.ImmutableDexFile;

/** Host writer: remove exact reviewed adapters, or join only adapters and new helpers. */
public final class GalleryDexMediaAssembly {
    private static Map<String, ClassDef> read(String file) throws Exception {
        Map<String, ClassDef> result = new TreeMap<>();
        for (ClassDef cls : DexFileFactory.loadDexFile(Path.of(file).toFile(), Opcodes.forApi(31)).getClasses()) {
            if (result.put(cls.getType(), cls) != null) throw new IllegalArgumentException("duplicate class");
        }
        return result;
    }

    public static void main(String[] args) throws Exception {
        if (args.length < 4) throw new IllegalArgumentException("mode input output other/targets...");
        Map<String, ClassDef> original = read(args[1]);
        if (args[0].equals("remove")) {
            Set<String> targets = new TreeSet<>(Arrays.asList(Arrays.copyOfRange(args, 3, args.length)));
            if (targets.size() != args.length - 3 || !original.keySet().containsAll(targets)) {
                throw new IllegalArgumentException("unreviewed removal set");
            }
            for (String target : targets) original.remove(target);
        } else if (args[0].equals("join")) {
            if (args.length < 5) throw new IllegalArgumentException("join needs adapters/helpers/targets");
            Set<String> targets = new TreeSet<>(Arrays.asList(Arrays.copyOfRange(args, 4, args.length)));
            if (!original.keySet().equals(targets)) throw new IllegalArgumentException("unexpected adapters");
            Map<String, ClassDef> helpers = read(args[3]);
            if (helpers.isEmpty()) throw new IllegalArgumentException("missing helpers");
            for (String type : helpers.keySet()) {
                if (!type.startsWith("Lrealme/gallery/compat/") || original.containsKey(type)) {
                    throw new IllegalArgumentException("helper shadows an adapter");
                }
            }
            original.putAll(helpers);
        } else throw new IllegalArgumentException("unknown mode");
        DexFileFactory.writeDexFile(args[2], new ImmutableDexFile(Opcodes.forApi(31), original.values()));
    }
}
