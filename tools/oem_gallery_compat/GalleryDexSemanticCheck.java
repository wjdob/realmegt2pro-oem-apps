import java.io.StringWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Map;
import java.util.TreeMap;
import org.jf.baksmali.Adaptors.ClassDefinition;
import org.jf.baksmali.BaksmaliOptions;
import org.jf.dexlib2.DexFileFactory;
import org.jf.dexlib2.Opcodes;
import org.jf.dexlib2.iface.ClassDef;
import org.jf.dexlib2.ValueType;
import org.jf.dexlib2.iface.Field;
import org.jf.dexlib2.iface.value.*;
import org.jf.util.IndentingWriter;

/** Host-only full smali comparison, including bodies/private members/annotations. */
public final class GalleryDexSemanticCheck {
    private static Map<String, ClassDef> read(String path) throws Exception {
        Map<String, ClassDef> result = new TreeMap<>();
        var container = DexFileFactory.loadDexContainer(Path.of(path).toFile(),
                Opcodes.forApi(31));
        if (container.getDexEntryNames().size() != 1)
            throw new IllegalArgumentException("Expected one DEX");
        var dex = container.getEntry(container.getDexEntryNames().get(0)).getDexFile();
        for (ClassDef value : dex.getClasses()) {
            if (result.put(value.getType(), value) != null)
                throw new IllegalArgumentException("Duplicate class");
        }
        return result;
    }
    private static int normalizedDefaults;
    private static boolean defaultValue(Field field) {
        EncodedValue value = field.getInitialValue();
        if (value == null) return false;
        return switch (value.getValueType()) {
            case ValueType.NULL -> field.getType().startsWith("L") || field.getType().startsWith("[");
            case ValueType.BOOLEAN -> !((BooleanEncodedValue)value).getValue();
            case ValueType.BYTE -> ((ByteEncodedValue)value).getValue() == 0;
            case ValueType.SHORT -> ((ShortEncodedValue)value).getValue() == 0;
            case ValueType.CHAR -> ((CharEncodedValue)value).getValue() == 0;
            case ValueType.INT -> ((IntEncodedValue)value).getValue() == 0;
            case ValueType.LONG -> ((LongEncodedValue)value).getValue() == 0;
            case ValueType.FLOAT -> Float.floatToRawIntBits(((FloatEncodedValue)value).getValue()) == 0;
            case ValueType.DOUBLE -> Double.doubleToRawLongBits(((DoubleEncodedValue)value).getValue()) == 0;
            default -> false;
        };
    }
    private static String text(ClassDef value, boolean normalizeDefaults) throws Exception {
        BaksmaliOptions options = new BaksmaliOptions();
        options.apiLevel = 31;
        StringWriter result = new StringWriter();
        IndentingWriter writer = new IndentingWriter(result);
        new ClassDefinition(options, value).writeTo(writer);
        writer.flush();
        String text = result.toString();
        if (normalizeDefaults) for (Field field : value.getStaticFields()) {
            // A DEX writer may omit trailing encoded zero/null static values.
            // They have the same runtime initialization; retain every non-default
            // value (including negative floating zero) and all other metadata.
            if (!defaultValue(field)) continue;
            String suffix = java.util.regex.Pattern.quote(field.getName()+":"+field.getType());
            String pattern = "(?m)^(\\.field [^\\n]* " + suffix + ") = [^\\n]*$";
            String changed = text.replaceAll(pattern, "$1");
            if (!changed.equals(text)) normalizedDefaults++;
            text = changed;
        }
        return text;
    }
    public static void main(String[] args) throws Exception {
        Map<String, ClassDef> before = read(args[0]), after = read(args[1]);
        if (!before.keySet().equals(after.keySet()))
            throw new IllegalArgumentException("Different class sets");
        MessageDigest first = MessageDigest.getInstance("SHA-256");
        MessageDigest second = MessageDigest.getInstance("SHA-256");
        ArrayList<String> differences = new ArrayList<>();
        boolean normalizeDefaults = args.length == 4 && args[3].equals("--normalize-defaults");
        for (String name : before.keySet()) {
            String a = text(before.get(name), normalizeDefaults), b = text(after.get(name), normalizeDefaults);
            first.update((name+"\n"+a).getBytes(StandardCharsets.UTF_8));
            second.update((name+"\n"+b).getBytes(StandardCharsets.UTF_8));
            if (!a.equals(b)) {
                differences.add(name);
                if (differences.size() == 1) {
                    Path directory = Path.of(args[2]);Files.createDirectories(directory);
                    Files.writeString(directory.resolve("first-before.smali"), a);
                    Files.writeString(directory.resolve("first-after.smali"), b);
                }
            }
        }
        String a = java.util.HexFormat.of().formatHex(first.digest());
        String b = java.util.HexFormat.of().formatHex(second.digest());
        System.out.println("{\"classes\":"+before.size()+",\"matching\":"
                +differences.isEmpty()+",\"before_sha256\":\""+a
                +"\",\"after_sha256\":\""+b+"\",\"mismatch_count\":"
                +differences.size()+",\"default_initializers_normalized\":"+normalizedDefaults+"}");
        if (!differences.isEmpty()) System.exit(1);
    }
}
