import java.nio.file.*;
import java.io.*;
import java.util.*;
import org.jf.dexlib2.DexFileFactory;
import org.jf.dexlib2.Opcodes;
import org.jf.dexlib2.iface.*;
import org.jf.dexlib2.iface.instruction.ReferenceInstruction;
import org.jf.dexlib2.iface.reference.*;

/** Host-only inventory of definitions and actual instruction references.
 * No claim of reachability, hidden-API access or runtime compatibility.
 */
public final class AppDexInventory {
    static String method(MethodReference r) {
        StringBuilder s=new StringBuilder(r.getDefiningClass()+"->"+r.getName()+"(");
        for (CharSequence p:r.getParameterTypes()) s.append(p);
        return s+")"+r.getReturnType();
    }
    static String field(FieldReference r) {
        return r.getDefiningClass()+"->"+r.getName()+":"+r.getType();
    }
    public static void main(String[] args) throws Exception {
        if(args.length<2||args.length>3) throw new IllegalArgumentException("input APK/JAR/DEX, new output and optional dump class required");
        try(var writer=Files.newBufferedWriter(Path.of(args[1]),java.nio.charset.StandardCharsets.UTF_8,
                StandardOpenOption.CREATE_NEW)) {
            var container=DexFileFactory.loadDexContainer(Path.of(args[0]).toFile(),Opcodes.forApi(35));
            var rows=new TreeSet<String>();
            for(String entry:container.getDexEntryNames()) {
                for(ClassDef c:container.getEntry(entry).getDexFile().getClasses()) {
                    if(args.length==3) {
                        if(c.getType().equals(args[2])) {
                            var options=new org.jf.baksmali.BaksmaliOptions();
                            var indent=new org.jf.util.IndentingWriter(writer);
                            new org.jf.baksmali.Adaptors.ClassDefinition(options,c).writeTo(indent);
                            indent.flush();return;
                        }
                        continue;
                    }
                    rows.add("class\t"+c.getType()+"\t"+Objects.toString(c.getSuperclass(),"")+"\t"+String.join(",",c.getInterfaces()));
                    for(Field f:c.getStaticFields()) rows.add("field\t"+field(f));
                    for(Field f:c.getInstanceFields()) rows.add("field\t"+field(f));
                    for(Method m:c.getMethods()) {
                        String caller=method(m);rows.add("method\t"+caller);
                        if(m.getImplementation()==null)continue;
                        for(var instruction:m.getImplementation().getInstructions()) {
                            if(!(instruction instanceof ReferenceInstruction r))continue;
                            var ref=r.getReference();String kind=null,value=null;
                            if(ref instanceof MethodReference v){kind="call";value=method(v);}
                            else if(ref instanceof FieldReference v){kind="fieldref";value=field(v);}
                            else if(ref instanceof TypeReference v){kind="typeuse";value=v.getType();}
                            else if(ref instanceof StringReference v && (v.getString().startsWith("android.permission.")
                                    ||v.getString().startsWith("com.oplus.permission.")
                                    ||v.getString().startsWith("com.coloros.permission."))) {
                                kind="permission-string";value=v.getString();
                            }
                            if(kind!=null)rows.add(kind+"\t"+value+"\t"+caller);
                        }
                    }
                }
            }
            if(args.length==3)throw new IllegalArgumentException("Requested class absent");
            for(String row:rows){writer.write(row);writer.newLine();}
        }
    }
}
