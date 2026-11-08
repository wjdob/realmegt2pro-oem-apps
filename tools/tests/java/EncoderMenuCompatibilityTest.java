package realme.camera.compat;

import android.app.Activity;
import android.media.MediaCodecInfo;
import android.media.MediaCodecList;
import com.oplus.camera.common.config.CameraSettingsConfig;
import com.oplus.camera.common.config.OptionItemConfig;
import java.io.File;
import java.lang.reflect.Field;
import java.util.Arrays;
import java.util.ArrayList;
import java.util.List;

public final class EncoderMenuCompatibilityTest {
    private static int cases;
    private static void require(boolean condition) {if(!condition)throw new AssertionError();}
    private static void reviewed(Boolean value) throws Exception {
        Field f=EncoderMenuCompatibility.class.getDeclaredField("reviewed");f.setAccessible(true);f.set(null,value);
    }
    private static OptionItemConfig option(String key,String... sizes) {
        OptionItemConfig value=new OptionItemConfig();value.setKey(key);
        for(String size:sizes)value.addOptionItem(size);return value;
    }
    private static MediaCodecInfo codec(String mime,int width,int height) {return new MediaCodecInfo(mime,width,height);}
    private static void reset() throws Exception {
        // The real reviewed APK hash is checked independently by the package builder.
        // Set only that precondition here to exercise the actual Java menu transaction.
        reviewed(true);CameraSettingsConfig.mOptionItemConfigList.clear();
        com.oplus.camera.util.Util.r=false;c9.b.a=false;
        MediaCodecList.infos=new MediaCodecInfo[]{codec("video/avc",4096,2176),codec("video/hevc",4096,2176)};
    }
    public static void main(String[] args) throws Exception {
        Activity camera=new Activity("com.oplus.camera");
        reset();OptionItemConfig original=option("pref_video_size_key_back_camera_supported","video_size_8k","video_size_4kuhd","video_size_1080p");
        List<String> snapshot=new ArrayList<>(original.getSupportedList());CameraSettingsConfig.mOptionItemConfigList.add(original);
        require(EncoderMenuCompatibility.apply(camera));
        require(CameraSettingsConfig.mOptionItemConfigList.get(0)!=original);
        require(CameraSettingsConfig.mOptionItemConfigList.get(0).getSupportedList().equals(Arrays.asList("video_size_4kuhd","video_size_1080p")));
        require(original.getSupportedList().equals(snapshot));require(com.oplus.camera.util.Util.r==null && c9.b.a==null);cases++;
        Object kept=CameraSettingsConfig.mOptionItemConfigList.get(0);
        com.oplus.camera.util.Util.r=true;c9.b.a=true;
        require(EncoderMenuCompatibility.apply(camera));require(CameraSettingsConfig.mOptionItemConfigList.get(0)==kept);
        require(Boolean.TRUE.equals(com.oplus.camera.util.Util.r));
        CameraSettingsConfig.mOptionItemConfigList.clear();CameraSettingsConfig.mOptionItemConfigList.add(original);
        require(EncoderMenuCompatibility.apply(camera));require(!CameraSettingsConfig.mOptionItemConfigList.get(0).getSupportedList().contains("video_size_8k"));
        require(com.oplus.camera.util.Util.r==null);cases++;
        reset();original=option("pref_video_size_key","video_size_8k","video_size_4kuhd");CameraSettingsConfig.mOptionItemConfigList.add(original);
        MediaCodecList.infos=new MediaCodecInfo[]{codec("video/avc",8192,8192),codec("video/hevc",8192,8192)};
        require(EncoderMenuCompatibility.apply(camera));require(CameraSettingsConfig.mOptionItemConfigList.get(0)==original);
        require(Boolean.FALSE.equals(com.oplus.camera.util.Util.r) && Boolean.FALSE.equals(c9.b.a));cases++;
        reset();original=option("pref_video_size_key","video_size_8k","video_size_4kuhd");CameraSettingsConfig.mOptionItemConfigList.add(original);
        MediaCodecList.infos=new MediaCodecInfo[]{codec("video/avc",8192,8192),codec("video/hevc",4096,2176)};
        require(EncoderMenuCompatibility.apply(camera));require(CameraSettingsConfig.mOptionItemConfigList.get(0)==original);cases++;
        reset();original=option("pref_video_size_key","video_size_8k","video_size_4kuhd");CameraSettingsConfig.mOptionItemConfigList.add(original);
        MediaCodecList.infos=new MediaCodecInfo[]{codec("video/avc",4096,2176)};
        require(!EncoderMenuCompatibility.apply(camera));require(CameraSettingsConfig.mOptionItemConfigList.get(0)==original);cases++;
        reset();original=option("other.setting","video_size_8k");CameraSettingsConfig.mOptionItemConfigList.add(original);
        require(EncoderMenuCompatibility.apply(camera));require(CameraSettingsConfig.mOptionItemConfigList.get(0)==original);cases++;
        reset();original=option("pref_video_size_key","video_size_8k");CameraSettingsConfig.mOptionItemConfigList.add(original);
        require(!EncoderMenuCompatibility.apply(camera));require(CameraSettingsConfig.mOptionItemConfigList.get(0)==original);cases++;
        reset();original=option("pref_video_size_key","video_size_8k","future_unknown_size","video_size_1080p");CameraSettingsConfig.mOptionItemConfigList.add(original);
        require(EncoderMenuCompatibility.apply(camera));require(CameraSettingsConfig.mOptionItemConfigList.get(0).getSupportedList().contains("future_unknown_size"));cases++;
        reset();original=option("pref_video_size_key","video_size_8k","video_size_1080p");CameraSettingsConfig.mOptionItemConfigList.add(original);
        require(!EncoderMenuCompatibility.apply(new Activity("other.app")));require(CameraSettingsConfig.mOptionItemConfigList.get(0)==original);cases++;
        reset();original=option("pref_video_size_key","video_size_8k","video_size_1080p");CameraSettingsConfig.mOptionItemConfigList.add(original);
        reviewed(null);camera.getApplicationInfo().sourceDir=File.createTempFile("unknown-apk-",".fixture").getPath();
        try {require(!EncoderMenuCompatibility.apply(camera));require(CameraSettingsConfig.mOptionItemConfigList.get(0)==original);}
        finally {new File(camera.getApplicationInfo().sourceDir).delete();}cases++;
        reset();original=option("pref_video_size_key","video_size_8k","video_size_1080p");CameraSettingsConfig.mOptionItemConfigList.add(original);
        CameraSettingsConfig.mOptionItemConfigList.add(option("pref_video_size_key_front_camera_supported","video_size_8k"));
        require(!EncoderMenuCompatibility.apply(camera));require(CameraSettingsConfig.mOptionItemConfigList.get(0)==original);
        require(Boolean.FALSE.equals(com.oplus.camera.util.Util.r));cases++;
        reset();require(!EncoderMenuCompatibility.apply(camera));cases++;
        System.out.println("Encoder menu cases passed: "+cases);
    }
}
