"""Run the actual native helper against controlled metadata/property/I/O boundaries."""
import os
import io
import json
import re
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import zipfile

import oem_addons as addon


class RecoveryMapperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which('g++'):
            raise unittest.SkipTest('host g++ unavailable')
        cls.work = tempfile.TemporaryDirectory()
        root = Path(cls.work.name)
        (root/'liblp').mkdir(); (root/'sys').mkdir()
        (root/'sys/system_properties.h').write_text('#define PROP_VALUE_MAX 92\nextern "C" int __system_property_get(const char*, char*);\n')
        (root/'liblp/liblp.h').write_text('''#include <memory>
#include <string>
#include <vector>
#define LP_PARTITION_ATTR_DISABLED 4
namespace android::fs_mgr {
struct LpMetadataPartition {std::string name; unsigned num_extents=1, attributes=0;};
struct LpMetadata {std::vector<LpMetadataPartition> partitions;};
inline std::string GetPartitionName(const LpMetadataPartition& p) {return p.name;}
}
''')
        fixture = root/'fixture.cpp'
        fixture.write_text(r'''
#include <liblp/liblp.h>
#include <sys/stat.h>
#include <cstring>
#include <cstdlib>
#include <cerrno>
#include <cstdio>
#include <string>
static bool mapped=false;
static std::string sysname;
static std::string mode(){const char* p=getenv("STUB_CASE");return p?p:"missing";}
extern "C" unsigned __wrap_getuid(){return 0;}
extern "C" int __system_property_get(const char* key,char* v){
 std::string k=key,s;
 if(k=="ro.bootmode")s=mode()=="android"?"normal":"recovery";
 if(k=="ro.product.model")s=mode()=="model"?"OTHER":"RMX3301";
 if(k=="ro.bionic.arch")s="arm64";
 if(k=="ro.boot.vbmeta.device_state")s=mode()=="locked"?"locked":"unlocked";
 if(k=="ro.boot.dynamic_partitions")s="true";
 if(k=="ro.boot.slot_suffix")s=mode()=="slot"?"_x":mode()=="slot_b"?"_b":"_a";
 strcpy(v,s.c_str());return s.size();
}
extern "C" int __wrap_open(const char* p,int,...){
 std::string path=p;
 if(path.find("/sys/class/block/")==0){return 5;}
 return 4;
}
extern "C" int __wrap_close(int){return 0;}
extern "C" long __wrap_pread(int,void* p,unsigned long n,long off){
 const unsigned char bytes[]={2,176,10,116,86,0,0};
 if(n!=7||off!=32768)return -1;
 memcpy(p,bytes,7);if(mode()=="vab")((char*)p)[5]=1;
 return mode()=="shortread"?6:7;
}
extern "C" long __wrap_read(int,void* p,unsigned long n){
 auto text=(mode()=="identity"?"wrong":sysname)+"\n";
 if(text.size()>n)return -1;memcpy(p,text.data(),text.size());return text.size();
}
extern "C" int __wrap_stat(const char*,struct stat* st){st->st_mode=S_IFBLK;return 0;}
extern "C" char* __wrap_realpath(const char* p,char* out){
 sysname=std::string(p).substr(std::string(p).find_last_of('/')+1);
 strcpy(out,"/dev/block/dm-17");return out;
}
extern "C" int __wrap_lstat(const char* p,struct stat*){
 std::string s=p,m=mode();
 if(m=="errno"){errno=EACCES;return -1;}
 if(mapped||m=="present"||m=="identity"||(m=="partial"&&s.find("system_a")!=s.npos)||
    (m=="other"&&s.find("other_a")!=s.npos))return 0;
 errno=ENOENT;return -1;
}
namespace android::fs_mgr {
std::unique_ptr<LpMetadata> ReadCurrentMetadata(const std::string&){
 if(mode()=="metadata")return nullptr;
 auto p=std::make_unique<LpMetadata>();
 for(const char* n:{"system","product","system_ext","vendor","odm"})p->partitions.push_back({std::string(n)+(mode()=="slot_b"?"_b":"_a")});
 if(mode()=="disabled")p->partitions[0].attributes=4;
 if(mode()=="other")p->partitions.push_back({"other_a"});
 return p;
}
bool CreateLogicalPartitions(const LpMetadata&,const std::string&){
 puts("MAP_CALL");if(mode()=="mapfail")return false;mapped=true;return true;
}
}
''')
        cls.binary = root/'mapper'
        wrap = ['getuid','open','close','pread','read','stat','lstat','realpath']
        subprocess.run(['g++','-std=c++20','-I'+str(root),
            str(addon.ROOT/'tools/oem_addons/map_logical.cpp'),str(fixture),
            *['-Wl,--wrap='+n for n in wrap],'-o',str(cls.binary)],check=True,capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def test_missing_uses_standard_api_once_and_present_is_noop(self):
        for case, count in [('missing',1),('slot_b',1),('present',0)]:
            p=subprocess.run([str(self.binary),'--prepare-missing'],env=dict(os.environ,STUB_CASE=case),capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stderr)
            self.assertEqual(p.stdout.count('MAP_CALL'),count)

    def test_unsafe_states_refuse_before_any_mapping(self):
        for case in ['android','model','locked','slot','vab','shortread','metadata',
                     'disabled','partial','identity','errno','other']:
            with self.subTest(case=case):
                p=subprocess.run([str(self.binary),'--prepare-missing'],env=dict(os.environ,STUB_CASE=case),capture_output=True,text=True)
                self.assertNotEqual(p.returncode,0)
                self.assertNotIn('MAP_CALL',p.stdout)

    def test_failed_standard_mapping_is_not_retried(self):
        p=subprocess.run([str(self.binary),'--prepare-missing'],env=dict(os.environ,STUB_CASE='mapfail'),capture_output=True,text=True)
        self.assertNotEqual(p.returncode,0)
        self.assertEqual(p.stdout.count('MAP_CALL'),1)

    def test_unknown_operation_refuses(self):
        p=subprocess.run([str(self.binary),'--anything'],capture_output=True,text=True)
        self.assertNotEqual(p.returncode,0)
        self.assertNotIn('MAP_CALL',p.stdout)

    def test_recovery_support_hash_scope_and_installer_binding(self):
        helper=bytearray(4096);helper[:6]=b'\x7fELF\x02\x01';helper[18:20]=(183).to_bytes(2,'little');helper=bytes(helper)
        libraries={name:'a'*64 for name in ['libfs_mgr.so','liblp.so','libbase.so','libc++.so','libc.so','libdl.so']}
        profile=dict(id='camera',recovery_helper=dict(sha256=addon.sha(helper),libraries=libraries))
        members, digest, binding=addon.recovery_support(profile,helper)
        self.assertEqual(digest,addon.sha(helper));self.assertEqual(binding,addon.sha(members['recovery/libraries.sha256']))
        with self.assertRaises(ValueError):addon.recovery_support(profile,helper+b'x')
        with self.assertRaises(ValueError):addon.recovery_support({},helper)
        with self.assertRaises(ValueError):addon.recovery_support(dict(profile,recovery_helper=dict(sha256=digest,libraries={})),helper)

    def test_signed_archive_support_is_separate_from_installed_files_and_tampering_refuses(self):
        helper=bytearray(4096);helper[:6]=b'\x7fELF\x02\x01';helper[18:20]=(183).to_bytes(2,'little');helper=bytes(helper)
        profile=dict(json.loads(addon.CONFIG.read_text())['profiles']['photos'],id='photos',isolated_photos_alpha=True)
        profile['recovery_helper']=dict(sha256=addon.sha(helper),libraries={name:'a'*64 for name in
            ['libfs_mgr.so','liblp.so','libbase.so','libc++.so','libc.so','libdl.so']})
        apk=io.BytesIO()
        with zipfile.ZipFile(apk,'w'):pass
        files=addon.payload(profile,apk.getvalue())
        self.assertFalse(any(path.startswith('recovery/') for path in files))
        archive=addon.archive_bytes(profile,files,'check',b'certificate',recovery_helper=helper)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'check.zip';path.write_bytes(archive)
            addon.inspect_archive(path,profile)
            with zipfile.ZipFile(io.BytesIO(archive)) as z:members={n:z.read(n) for n in z.namelist()}
            for name in ['recovery/prepare-logical','recovery/libraries.sha256','META-INF/com/google/android/update-binary']:
                changed=dict(members)
                changed[name]=(changed[name]+b'x' if not name.endswith('update-binary') else changed[name].replace(b"ISOLATED_PHOTOS='1'",b"ISOLATED_PHOTOS='0'"))
                with zipfile.ZipFile(path,'w') as z:
                    for n,data in changed.items():z.writestr(n,data)
                with self.assertRaises(ValueError):addon.inspect_archive(path,profile)

    def test_gallery_alpha_refuses_camera_sdk_but_removal_remains_available(self):
        script=addon.TEMPLATE.read_text()
        fragment=script[script.index('if [ "$ISOLATED_PHOTOS" = 1 ]'):script.index('if [ "$APP_PARTITION" = system ]')]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);system=root/'system';product=root/'product';system.mkdir();product.mkdir()
            env=dict(os.environ,SYSTEM=str(system),PRODUCT=str(product),PROFILE='photos',ISOLATED_PHOTOS='1',ACTION='install')
            def run():return subprocess.run(['sh','-c',"die() { echo \"$*\"; exit 1; };\n"+fragment],env=env,capture_output=True,text=True)
            self.assertEqual(run().returncode,0)
            for partition,rel in [(system,'app/RealmeStockCamera'),(system,'priv-app/RealmeStockCamera'),
                                  (product,'framework/realme-camera-unit-sdk.jar'),(system,'etc/permissions/realme-camera-unit-sdk.xml')]:
                p=partition/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('owned by Camera')
                self.assertNotEqual(run().returncode,0)
                env['ACTION']='remove';self.assertEqual(run().returncode,0);env['ACTION']='install'
                self.assertEqual(p.read_text(),'owned by Camera');p.unlink()
            link=system/'framework/realme-camera-unit-sdk.jar';link.parent.mkdir(exist_ok=True);link.symlink_to(root/'absent')
            self.assertNotEqual(run().returncode,0)
