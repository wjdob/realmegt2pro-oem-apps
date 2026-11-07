"""Exercise actual bridge packet forwarding without executing OEM libraries."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_camera_bridge37 as bridge

PROGRAM = r'''
#include <cassert>
#include <cstdint>
#include "SOURCE"
static const void* seen;
static void* queue1;
static void* queue2;
static int first;
static int second;
static bool enabled;
static int calls[4];
extern "C" void heifProcess(int w,int h,int stride,int scanline,int fmt,int rot,
 unsigned char* buf,const unsigned char* exif,unsigned int exifSize,
 unsigned char* debug,unsigned int debugSize,int fd,const void* parameters) {
 ++calls[0]; seen=parameters;
 assert(w==640 && h==480 && stride==672 && scanline==496 && fmt==10 && rot==270);
 assert(buf==reinterpret_cast<unsigned char*>(0x1000));
 assert(exif==reinterpret_cast<const unsigned char*>(0x2000) && exifSize==71);
 assert(debug==reinterpret_cast<unsigned char*>(0x3000) && debugSize==93 && fd==42);
}
extern "C" int setBufferQAllocEnable(void* q,bool e,const void* p) {
 ++calls[1];queue1=q;enabled=e;seen=p;return -31;
}
extern "C" int exchangeHardwareBufferBetweenBufferQ(void* a,void* b,int x,int y,const void* p) {
 ++calls[2];queue1=a;queue2=b;first=x;second=y;seen=p;return -32;
}
extern "C" int attachHardwareBufferToBufferQ(void* a,void* b,const void* p) {
 ++calls[3];queue1=a;queue2=b;seen=p;return -33;
}
template<class T> long long address(T* p) { return reinterpret_cast<std::intptr_t>(p); }
int main() {
 assert(JNI_OnLoad(nullptr,nullptr)==0x00010006);
 HeifPacket h{640,480,672,496,10,270,reinterpret_cast<unsigned char*>(0x1000),
   reinterpret_cast<const unsigned char*>(0x2000),71,
   reinterpret_cast<unsigned char*>(0x3000),93,42,{11,22,33}};
 Java_android_hardware_camera2_OplusCameraManager_nativtSendToProcessHeif(nullptr,nullptr,address(&h));
 assert(calls[0]==1 && seen==h.parameters && h.parameters[2]==33);
 int q=1,b=2;
 AllocatePacket a{&q,true,{44,55,66}};
 assert(Java_android_hardware_camera2_OplusCameraManager_nativeSendToBufQAllocEnableEvent(
   nullptr,nullptr,address(&a))==-31);
 assert(calls[1]==1 && queue1==&q && enabled && seen==a.parameters);
 ExchangePacket e{&q,&b,7,9,{77,88,99}};
 assert(Java_android_hardware_camera2_OplusCameraManager_nativeSendToExchgHWBufBtwBufQEvent(
   nullptr,nullptr,address(&e))==-32);
 assert(calls[2]==1 && queue1==&q && queue2==&b && first==7 && second==9 && seen==e.parameters);
 AttachPacket t{&q,&b,{111,222,333}};
 assert(Java_android_hardware_camera2_OplusCameraManager_nativeSendToAttachHWBufToBufQEvent(
   nullptr,nullptr,address(&t))==-33);
 assert(calls[3]==1 && queue1==&q && queue2==&b && seen==t.parameters);
}
'''


class BridgeInputTests(unittest.TestCase):
    def test_buffer_cohort_drift_requires_another_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            changed = root / 'changed.json'
            changed.write_text('{}')
            with mock.patch.object(bridge, 'BUFFER_PROFILE', changed), \
                    mock.patch.object(bridge.subprocess, 'run') as run:
                with self.assertRaisesRegex(ValueError, 'cohort changed'):
                    bridge.prepare(root / 'native', root / 'compiler', root / 'out')
                run.assert_not_called()
                self.assertFalse((root / 'out').exists())

    def test_source_drift_fails_before_compilation_or_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'changed.cpp'
            source.write_text('// changed')
            with mock.patch.object(bridge, 'SOURCE', source), \
                    mock.patch.object(bridge.subprocess, 'run') as run:
                with self.assertRaisesRegex(ValueError, 'source'):
                    bridge.prepare(root / 'native', root / 'compiler', root / 'out')
                run.assert_not_called()
                self.assertFalse((root / 'out').exists())

    def test_wrong_helper_fails_before_compiler_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'libHeifEncoderWrapper.so').write_bytes(b'wrong')
            with mock.patch.object(bridge.subprocess, 'run') as run:
                with self.assertRaisesRegex(ValueError, 'helper'):
                    bridge.prepare(root, root / 'compiler', root / 'out')
                run.assert_not_called()
                self.assertFalse((root / 'out').exists())


@unittest.skipUnless(shutil.which('c++'), 'requires a 64-bit host C++ compiler')
class PacketForwardingTests(unittest.TestCase):
    def test_all_four_events_preserve_fields_pointers_and_return_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'probe.cpp'
            source.write_text(PROGRAM.replace('SOURCE', bridge.SOURCE.as_posix()))
            binary = root / 'probe'
            subprocess.run(['c++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                            '-fsanitize=address,undefined', str(source), '-o', str(binary)],
                           check=True, capture_output=True)
            subprocess.run([str(binary)], check=True, capture_output=True)


if __name__ == '__main__':
    unittest.main()
