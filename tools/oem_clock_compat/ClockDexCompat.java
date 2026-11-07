import java.nio.file.Path;
import java.util.Map;
import java.util.ArrayList;
import org.jf.dexlib2.DexFileFactory;
import org.jf.dexlib2.Opcodes;
import org.jf.dexlib2.iface.DexFile;
import org.jf.dexlib2.iface.Method;
import org.jf.dexlib2.immutable.ImmutableMethod;
import org.jf.dexlib2.immutable.ImmutableMethodImplementation;
import org.jf.dexlib2.immutable.ImmutableTryBlock;
import org.jf.dexlib2.immutable.ImmutableExceptionHandler;
import org.jf.dexlib2.rewriter.MethodRewriter;
import org.jf.dexlib2.rewriter.DexRewriter;
import org.jf.dexlib2.rewriter.Rewriter;
import org.jf.dexlib2.rewriter.RewriterModule;
import org.jf.dexlib2.rewriter.Rewriters;

/** Host-only exact descriptor adaptation; reverse mode supports full semantic checks. */
public final class ClockDexCompat {
    public static void main(String[] args) throws Exception {
        if (args.length != 3 || !(args[0].equals("forward") || args[0].equals("reverse")))
            throw new IllegalArgumentException("forward|reverse input.dex output.dex");
        Map<String,String> mapping = Map.of(
                "Lcom/oplus/content/OplusFeatureConfigManager;", "Lrealme/clock/compat/FeatureConfig;",
                "Lcom/oplus/os/OplusBuild;", "Lrealme/clock/compat/OemBuild;",
                "Lcom/oplus/os/OplusBuild$VERSION;", "Lrealme/clock/compat/OemBuild$VERSION;",
                "Lcom/oplus/media/MediaFile;", "Lrealme/clock/compat/AlarmMedia;",
                "Landroid/app/OplusNotificationManager;", "Lrealme/clock/compat/OemNotifications;");
        boolean reverse = args[0].equals("reverse");
        int[] guardCount = {0};
        DexFile old = DexFileFactory.loadDexFile(Path.of(args[1]).toFile(), Opcodes.forApi(35));
        for (var cls : old.getClasses()) {
            if (mapping.containsKey(cls.getType()) || cls.getType().startsWith("Lrealme/clock/compat/"))
                throw new IllegalArgumentException("Input shadows an external/helper class");
        }
        DexRewriter rewriter = new DexRewriter(new RewriterModule() {
            @Override public Rewriter<Method> getMethodRewriter(Rewriters rewriters) {
                return new MethodRewriter(rewriters) {
                    @Override public Method rewrite(Method value) {
                        Method result = super.rewrite(value);
                        if (!value.getDefiningClass().equals("Lu5/w1;") || !value.getName().equals("D0")
                                || !value.getParameterTypes().isEmpty() || !value.getReturnType().equals("Z"))
                            return result;
                        var implementation = result.getImplementation();
                        if (implementation == null || implementation.getTryBlocks().size() != 1)
                            throw new IllegalArgumentException("Unreviewed confine query implementation");
                        var block = implementation.getTryBlocks().get(0);
                        var handlers = new ArrayList<ImmutableExceptionHandler>();
                        int fallback = -1, removed = 0;
                        for (var handler : block.getExceptionHandlers()) {
                            if ("Ljava/lang/Exception;".equals(handler.getExceptionType()))
                                fallback = handler.getHandlerCodeAddress();
                            if ("Ljava/lang/LinkageError;".equals(handler.getExceptionType())) {
                                if (!reverse) throw new IllegalArgumentException("Already guarded");
                                removed++;
                            } else handlers.add(ImmutableExceptionHandler.of(handler));
                        }
                        if (fallback < 0 || removed != (reverse ? 1 : 0))
                            throw new IllegalArgumentException("Unreviewed confine fallback handlers");
                        if (!reverse) handlers.add(new ImmutableExceptionHandler("Ljava/lang/LinkageError;", fallback));
                        guardCount[0]++;
                        var guarded = new ImmutableMethodImplementation(implementation.getRegisterCount(),
                                implementation.getInstructions(),
                                java.util.List.of(new ImmutableTryBlock(block.getStartCodeAddress(),
                                        block.getCodeUnitCount(), handlers)), implementation.getDebugItems());
                        return new ImmutableMethod(result.getDefiningClass(), result.getName(), result.getParameters(),
                                result.getReturnType(), result.getAccessFlags(), result.getAnnotations(),
                                result.getHiddenApiRestrictions(), guarded);
                    }
                };
            }
            @Override public Rewriter<String> getTypeRewriter(Rewriters ignored) {
                return type -> {
                    int dimensions = 0;
                    while (dimensions < type.length() && type.charAt(dimensions) == '[') dimensions++;
                    String base = type.substring(dimensions), replacement = null;
                    if (reverse) {
                        for (var entry : mapping.entrySet())
                            if (entry.getValue().equals(base)) replacement = entry.getKey();
                    } else replacement = mapping.get(base);
                    return replacement == null ? type : type.substring(0, dimensions) + replacement;
                };
            }
        });
        DexFileFactory.writeDexFile(args[2], rewriter.getDexFileRewriter().rewrite(old));
        // Dex writers may iterate rewritten classes more than once. The builder
        // separately locates the one original method and verifies reverse semantics.
        System.out.println("confine query rewrites: " + guardCount[0]);
    }
}
