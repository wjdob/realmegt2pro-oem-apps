package realme.gallery.compat;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.os.Build;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.IOException;
import java.io.OutputStream;

public final class HeicCompatibilityTest {
    private static int cases;
    private static void require(boolean condition) { if (!condition) throw new AssertionError(); }
    private static void defaults() {
        BitmapFactory.mime="image/heif";BitmapFactory.width=4;BitmapFactory.height=3;
        Bitmap.recycled=0;Bitmap.fail=false;Bitmap.result=true;
    }
    public static void main(String[] args) throws Exception {
        Context gallery=new Context("com.coloros.gallery3d");
        if (args.length==1) {
            require(HeicCompatibility.hasBootConverter());
            require(!HeicCompatibility.available(gallery));
            System.out.println("OEM boot converter retained");return;
        }
        require(!HeicCompatibility.hasBootConverter());
        require(HeicCompatibility.available(gallery));
        require(!HeicCompatibility.available(new Context("other.app")));
        require(!HeicCompatibility.available(null));Build.VERSION.SDK_INT=30;
        require(!HeicCompatibility.available(gallery));Build.VERSION.SDK_INT=31;cases++;
        require(!HeicCompatibility.enabled(null));require(!HeicCompatibility.enabled("false"));
        require(HeicCompatibility.enabled("true"));require(!HeicCompatibility.enabled("TRUE"));cases++;
        defaults();ByteArrayOutputStream out=new ByteArrayOutputStream();
        require(HeicCompatibility.convert(new ByteArrayInputStream(new byte[]{1,2}),95,out));
        require(out.size()>0 && Bitmap.recycled==1 && Bitmap.quality==95);cases++;
        defaults();BitmapFactory.mime="image/jpeg";out.reset();
        require(!HeicCompatibility.convert(new ByteArrayInputStream(new byte[]{1}),95,out));
        require(out.size()==0 && Bitmap.recycled==0);cases++;
        defaults();BitmapFactory.width=Integer.MAX_VALUE;BitmapFactory.height=Integer.MAX_VALUE;out.reset();
        require(!HeicCompatibility.convert(new ByteArrayInputStream(new byte[]{1}),95,out));
        require(out.size()==0 && Bitmap.recycled==0);cases++;
        defaults();out.reset();
        require(!HeicCompatibility.convert(null,95,out));
        require(!HeicCompatibility.convert(new ByteArrayInputStream(new byte[]{1}),101,out));
        require(!HeicCompatibility.convert(new ByteArrayInputStream(new byte[]{1}),-1,out));cases++;
        defaults();out.reset();Thread.currentThread().interrupt();
        try { require(!HeicCompatibility.convert(new ByteArrayInputStream(new byte[]{1}),95,out)); }
        finally { Thread.interrupted(); }
        require(out.size()==0);cases++;
        defaults();out.reset();
        require(!HeicCompatibility.convert(new InputStream(){public int read() throws IOException {throw new IOException();}},95,out));
        require(out.size()==0);cases++;
        defaults();Bitmap.fail=true;out.reset();
        require(!HeicCompatibility.convert(new ByteArrayInputStream(new byte[]{1}),95,out));
        require(Bitmap.recycled==1);cases++;
        defaults();out.reset();
        InputStream tooLarge=new InputStream(){
            public int read(){return 1;}
            public int read(byte[] b,int off,int length){return length;}
        };
        require(!HeicCompatibility.convert(tooLarge,95,out));require(out.size()==0);cases++;
        defaults();out.reset();
        require(HeicCompatibility.convert(new InputStream(){
            int n;
            public int read(){return n++<2?1:-1;}
            public int read(byte[] b,int off,int len){return n==0 ? 0 : -1;}
        },95,out));cases++;
        System.out.println("HEIC cases passed: "+cases);
    }
}
