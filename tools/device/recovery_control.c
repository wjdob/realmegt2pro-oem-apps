// Separate engineering recovery control. No block writes, BCB, slot or erase API.
#define _GNU_SOURCE
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <sys/file.h>
#include <sys/system_properties.h>
#include <mntent.h>
#include <dirent.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int fail(const char *message) {
    fprintf(stderr,"recovery control refused: %s (errno=%d)\n",message,errno);
    return 1;
}
static int property_is(const char *key,const char *expected) {
    char value[PROP_VALUE_MAX]={0};
    return __system_property_get(key,value)>0 && !strcmp(value,expected);
}
static int idle_vab(void) {
    unsigned char data[7]={0};
    const unsigned char expected[]={2,176,10,116,86,0};
    int fd=open("/dev/block/by-name/misc",O_RDONLY|O_CLOEXEC);
    if(fd<0)return 0;
    ssize_t count=pread(fd,data,sizeof(data),32768);close(fd);
    return count==sizeof(data) && !memcmp(data,expected,sizeof(expected)) && data[6]<=1;
}
static int installer_busy(void) {
    DIR *dir=opendir("/proc");if(!dir)return 1;
    struct dirent *entry;int busy=0;
    while((entry=readdir(dir))) {
        if(entry->d_name[0]<'0'||entry->d_name[0]>'9')continue;
        char path[300],name[128]={0};
        snprintf(path,sizeof(path),"/proc/%s/comm",entry->d_name);
        FILE *f=fopen(path,"r");if(!f)continue;
        if(fgets(name,sizeof(name),f)) {
            if(strstr(name,"minadbd")||strstr(name,"update_engine")||
               strstr(name,"update-binary")||strstr(name,"updater"))busy=1;
        }
        fclose(f);
    }
    closedir(dir);return busy;
}
// -1 means inspection failure; 0 unmounted; 1 a matching read-only system mount.
static int mounted(const char *device) {
    FILE *f=setmntent("/proc/mounts","r");if(!f)return -1;
    struct mntent *entry;int result=0;
    while((entry=getmntent(f))) {
        if(strcmp(entry->mnt_dir,"/mnt/system"))continue;
        struct stat expected,actual;
        result=stat(device,&expected)||stat(entry->mnt_fsname,&actual)||
            !S_ISBLK(actual.st_mode)||expected.st_rdev!=actual.st_rdev||
            !hasmntopt(entry,"ro") ? -1:1;
    }
    endmntent(f);return result;
}
static int identity(const char *device,const char *expected) {
    char resolved[4096],path[4096],name[128]={0};struct stat st;
    if(stat(device,&st)||!S_ISBLK(st.st_mode)||!realpath(device,resolved))return 0;
    const char *base=strrchr(resolved,'/');if(!base)return 0;
    if(snprintf(path,sizeof(path),"/sys/class/block/%s/dm/name",base+1)>=(int)sizeof(path))return 0;
    FILE *f=fopen(path,"r");if(!f)return 0;
    int ok=fgets(name,sizeof(name),f)!=NULL;fclose(f);
    name[strcspn(name,"\n")]=0;
    return ok&&!strcmp(name,expected);
}
static int any_mappings(void) {
    DIR *dir=opendir("/sys/class/block");if(!dir)return -1;
    struct dirent *entry;int found=0;
    while((entry=readdir(dir)))if(!strncmp(entry->d_name,"dm-",3))found=1;
    closedir(dir);return found;
}
int main(int argc,char **argv) {
    if(argc!=2 || (strcmp(argv[1],"status") && strcmp(argv[1],"mount-system-ro") &&
                  strcmp(argv[1],"unmount-system") && strcmp(argv[1],"sideload-addon") &&
                  strcmp(argv[1],"sideload-ota")))return fail("unknown operation");
    if(getuid() || !property_is("ro.bootmode","recovery") ||
       !property_is("ro.product.model","RMX3301") || !property_is("ro.build.version.sdk","37") ||
       !property_is("ro.lineage.recovery.engineering","1") ||
       !property_is("ro.adb.secure","1") || !property_is("ro.adb.secure.recovery","1") ||
       !property_is("ro.boot.vbmeta.device_state","unlocked"))return fail("engineering recovery identity/authentication");
    const char *suffix=property_is("ro.boot.slot_suffix","_a")?"_a":
                       property_is("ro.boot.slot_suffix","_b")?"_b":NULL;
    if(!suffix||!idle_vab())return fail("slot or active/unknown virtual A/B state");
    int lock=open("/tmp/realme-recovery-control.lock",O_CREAT|O_RDWR|O_CLOEXEC|O_NOFOLLOW,0600);
    if(lock<0||flock(lock,LOCK_EX|LOCK_NB))return fail("control busy");
    char device[128],name[32];
    snprintf(name,sizeof(name),"system%s",suffix);
    snprintf(device,sizeof(device),"/dev/block/mapper/%s",name);
    int mount_state=mounted(device);
    if(!strcmp(argv[1],"status")) {
        printf("engineering=1 sdk=37 slot=%s system_mount=%d installer_busy=%d mappings=%d\n",
               suffix,mount_state,installer_busy(),any_mappings());return 0;
    }
    if(installer_busy()||mount_state<0)return fail("installer busy or unexpected mount");
    if(!strcmp(argv[1],"mount-system-ro")) {
        if(!identity(device,name))return fail("prepare exact current-slot logical mapping first");
        if(mount_state==1){puts("system already mounted read-only");return 0;}
        struct stat st;
        if(lstat("/mnt/system",&st)) {
            if(errno!=ENOENT)return fail("cannot inspect mountpoint");
            if(lstat("/mnt",&st)||!S_ISDIR(st.st_mode))return fail("unexpected mountpoint parent");
            // Native21's ramdisk has /mnt but no /mnt/system. Create only this
            // fixed RAM directory, just as the normal recovery mount path does.
            if(mkdir("/mnt/system",0755)&&errno!=EEXIST)return fail("RAM mountpoint creation");
            if(lstat("/mnt/system",&st))return fail("cannot inspect created mountpoint");
        }
        if(!S_ISDIR(st.st_mode))return fail("mountpoint is not a directory");
        // noload prevents ext4 journal replay on a diagnostic read-only mount.
        if(mount(device,"/mnt/system","ext4",MS_RDONLY|MS_NODEV|MS_NOSUID,"noload"))return fail("read-only mount");
        if(mounted(device)!=1)return fail("mount verification");
        puts("system mounted read-only");return 0;
    }
    if(!strcmp(argv[1],"unmount-system")) {
        if(!mount_state){puts("system already unmounted");return 0;}
        if(umount("/mnt/system")||mounted(device)!=0)return fail("unmount");
        puts("system unmounted; logical mappings retained");return 0;
    }
    if(mount_state)return fail("unmount system before sideload");
    if(!strcmp(argv[1],"sideload-ota") && any_mappings()!=0)
        return fail("ROM OTA requires fresh recovery without logical mappings");
    // Existing Lineage recovery listener handles 's', retains verification and
    // restores normal ADB after the normal sideload lifecycle ends.
    int socket_fd=socket(AF_UNIX,SOCK_STREAM|SOCK_CLOEXEC,0);
    if(socket_fd<0)return fail("recovery socket");
    struct sockaddr_un address={.sun_family=AF_UNIX};
    strcpy(address.sun_path,"/dev/socket/recovery");
    if(connect(socket_fd,(struct sockaddr *)&address,sizeof(address)) ||
       send(socket_fd,"s",1,MSG_NOSIGNAL)!=1){close(socket_fd);return fail("sideload request");}
    close(socket_fd);puts("sideload requested; host must verify sideload transport before sending");
    return 0;
}
