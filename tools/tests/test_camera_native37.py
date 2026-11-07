"""Check buffer ownership/error paths independently of Android and OEM libraries."""
from pathlib import Path
import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_camera_native37 as builder

SOURCE = Path(__file__).resolve().parents[1] / 'oem_camera_compat/native/NativeWinBufferExchange37.cpp'

FAKE_ANDROID = r'''
#pragma once
#include <cassert>
struct ANativeWindow { virtual ~ANativeWindow() = default; };
struct AHardwareBuffer { void* buffer; };
namespace android {
constexpr int OK=0, BAD_VALUE=-22, NO_INIT=-19;
template<class T> struct sp {
 T* p=nullptr;
 sp()=default;
 sp(decltype(nullptr)) {}
 explicit sp(T* value):p(value) {}
 static sp fromExisting(T* p) { sp value;value.p=p;return value; }
 explicit operator bool() const { return p!=nullptr; }
 T* operator->() const { return p; }
};
struct GraphicBuffer {};
struct Fence { static sp<Fence> NO_FENCE; };
struct IGraphicBufferProducer {
 virtual ~IGraphicBufferProducer()=default;
 virtual int allowAllocation(bool)=0;
};
struct IGraphicBufferConsumer {
 virtual ~IGraphicBufferConsumer()=default;
 virtual int attachBuffer(int*,const sp<GraphicBuffer>&)=0;
 virtual int releaseBuffer(int,unsigned long,const sp<Fence>&)=0;
};
// Distinct base objects exercise the source's ANativeWindow-to-Surface cast.
struct Prefix { virtual ~Prefix()=default; long padding=0; };
struct Surface : Prefix, ANativeWindow {
 IGraphicBufferProducer* producer=nullptr;
 GraphicBuffer* image=nullptr;
 int detachResult=0;
 sp<IGraphicBufferProducer> getIGraphicBufferProducer() const {
   return sp<IGraphicBufferProducer>::fromExisting(producer);
 }
 virtual int detachNextBuffer(sp<GraphicBuffer>* b,sp<Fence>* f) {
   *b=sp<GraphicBuffer>::fromExisting(image);*f=Fence::NO_FENCE;return detachResult;
 }
};
inline GraphicBuffer* AHardwareBuffer_to_GraphicBuffer(AHardwareBuffer* p) {
 return static_cast<GraphicBuffer*>(p->buffer);
}
}
'''

PROGRAM = r'''
#include "SOURCE"
using namespace android;
sp<Fence> Fence::NO_FENCE;
struct Producer : IGraphicBufferProducer {
 bool enabled=false;int calls=0,result=-31;
 int allowAllocation(bool value) override { enabled=value;++calls;return result; }
};
struct Consumer : IGraphicBufferConsumer {
 GraphicBuffer* expected=nullptr;
 int attaches=0,releases=0,attachResult=0,releaseResult=0;
 int attachBuffer(int* slot,const sp<GraphicBuffer>& b) override {
   assert(b.p==expected);++attaches;*slot=11;return attachResult;
 }
 int releaseBuffer(int slot,unsigned long frame,const sp<Fence>& f) override {
   assert(slot==11 && frame==0 && f.p==Fence::NO_FENCE.p);++releases;
   return releaseResult;
 }
};
int main() {
 Producer producer;Consumer consumer;Surface surface;GraphicBuffer image;
 surface.producer=&producer;surface.image=&image;consumer.expected=&image;
 void* window=static_cast<ANativeWindow*>(&surface);
 assert(setBufferQAllocEnable(nullptr,true,nullptr)==BAD_VALUE);
 surface.producer=nullptr;
 assert(setBufferQAllocEnable(window,true,nullptr)==NO_INIT);
 surface.producer=&producer;
 assert(setBufferQAllocEnable(window,true,nullptr)==OK);
 assert(producer.calls==1 && producer.enabled); // Original zero-return contract.
 assert(setBufferQAllocEnable(window,false,nullptr)==OK && !producer.enabled);
 assert(exchangeHardwareBufferBetweenBufferQ(nullptr,window,1,2,nullptr)==-1);
 assert(exchangeHardwareBufferBetweenBufferQ(&consumer,nullptr,1,2,nullptr)==-1);
 surface.detachResult=-41;
 assert(exchangeHardwareBufferBetweenBufferQ(&consumer,window,1,2,nullptr)==-41);
 assert(consumer.attaches==0 && consumer.releases==0);
 surface.detachResult=0;consumer.attachResult=-42;
 assert(exchangeHardwareBufferBetweenBufferQ(&consumer,window,1,2,nullptr)==-42);
 assert(consumer.attaches==1 && consumer.releases==0);
 consumer.attachResult=0;consumer.releaseResult=-43;
 assert(exchangeHardwareBufferBetweenBufferQ(&consumer,window,1,2,nullptr)==-43);
 assert(consumer.attaches==2 && consumer.releases==1);
 consumer.releaseResult=0;
 assert(exchangeHardwareBufferBetweenBufferQ(&consumer,window,1,2,nullptr)==0);
 assert(consumer.attaches==3 && consumer.releases==2);
 assert(attachHardwareBufferToBufferQ(nullptr,&image,nullptr)==BAD_VALUE);
 assert(attachHardwareBufferToBufferQ(&consumer,nullptr,nullptr)==BAD_VALUE);
 AHardwareBuffer hardware{nullptr};
 assert(attachHardwareBufferToBufferQ(&consumer,&hardware,nullptr)==BAD_VALUE);
 hardware.buffer=&image;consumer.attachResult=-44;
 assert(attachHardwareBufferToBufferQ(&consumer,&hardware,nullptr)==-44);
 assert(consumer.attaches==4 && consumer.releases==2);
 consumer.attachResult=0;consumer.releaseResult=-45;
 assert(attachHardwareBufferToBufferQ(&consumer,&hardware,nullptr)==-45);
 assert(consumer.attaches==5 && consumer.releases==3);
 consumer.releaseResult=0;
 assert(attachHardwareBufferToBufferQ(&consumer,&hardware,nullptr)==0);
 assert(consumer.attaches==6 && consumer.releases==4);
}
'''


@unittest.skipUnless(shutil.which('c++'), 'requires a 64-bit host C++ compiler')
class BufferEventTests(unittest.TestCase):
    def test_detach_attach_release_and_failure_ownership(self):
        with tempfile.TemporaryDirectory(prefix='camera-native37-') as directory:
            root = Path(directory)
            (root / 'fake_android.h').write_text(FAKE_ANDROID)
            for name in ['gui/IGraphicBufferConsumer.h', 'gui/Surface.h',
                         'private/android/AHardwareBufferHelpers.h', 'ui/Fence.h',
                         'utils/Errors.h']:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('#include "fake_android.h"\n')
            test = root / 'test.cpp'
            test.write_text(PROGRAM.replace('SOURCE', SOURCE.as_posix()))
            executable = root / 'test'
            subprocess.run(['c++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                            '-fsanitize=address,undefined', '-I', str(root),
                            str(test), '-o', str(executable)], check=True, capture_output=True)
            subprocess.run([str(executable)], check=True, capture_output=True)


class CompileRecordTests(unittest.TestCase):
    def test_review_requires_complete_packaged_and_bionic_providers(self):
        profile = json.loads(builder.PROFILE.read_text())
        builder.validate_profile(profile)
        for member in profile['provider_sha256']:
            changed = copy.deepcopy(profile)
            del changed['provider_sha256'][member]
            with self.assertRaisesRegex(ValueError, 'incomplete API37'):
                builder.validate_profile(changed)
        for field, value in [('sdk', 36), ('output_sha256', 'missing')]:
            changed = dict(profile, **{field: value})
            with self.assertRaises(ValueError):
                builder.validate_profile(changed)

    def test_changed_provider_and_extracted_runtime_bytes_are_refused(self):
        for member in ('SYSTEM/lib64/libnativewindow.so', 'runtime/lib64/bionic/libc.so'):
            profile = {'provider_sha256': {member: builder.digest(b'reviewed')}}
            self.assertEqual(builder.checked_provider(profile, member, b'reviewed'), b'reviewed')
            with self.assertRaisesRegex(ValueError, 'unreviewed native provider'):
                builder.checked_provider(profile, member, b'different')
            with self.assertRaisesRegex(ValueError, 'unreviewed native provider'):
                builder.checked_provider(profile, member + '.other', b'reviewed')

    def test_transitive_header_drift_changes_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'transitive.h').write_text('original')
            deps = root / 'object.d'
            deps.write_text('object.o: ' + builder.SOURCE.as_posix() + ' transitive.h\n')
            before = builder.header_inventory(root, deps)
            self.assertEqual(before, {'transitive.h': builder.digest(b'original')})
            (root / 'transitive.h').write_text('changed')
            self.assertNotEqual(builder.header_inventory(root, deps), before)

    def test_failed_or_unrelated_command_refused(self):
        for record in [{'exit_code': 1, 'command': 'anything'},
                       {'exit_code': 0, 'command': 'sh -c anything'}]:
            with self.assertRaises(ValueError):
                builder.normalized_compile(record)

    def test_cached_outputs_and_pgo_inputs_are_removed(self):
        command = ('PWD=/proc/self/cwd '
                   'prebuilts/clang/host/linux-x86/clang-r596125/bin/clang++ '
                   '-c -O2 -MD -MF out/original.d -o out/original.o '
                   '-fprofile-sample-use=out/profile.afdo -fsample-profile-use-profi '
                   'frameworks/native/libs/gui/Surface.cpp')
        result = builder.normalized_compile({'exit_code': 0, 'command': command})
        self.assertEqual(result, [command.split()[1], '-O2'])

    def test_unreviewed_arguments_refused_before_output_or_tool_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = root / 'command.json'
            record.write_text('{"exit_code":0,"command":"PWD=/proc/self/cwd '
                              'prebuilts/clang/host/linux-x86/clang-r596125/bin/clang++ '
                              '-Xclang -load -Xclang evil.so '
                              'frameworks/native/libs/gui/Surface.cpp"}')
            output = root / 'candidate'
            with self.assertRaisesRegex(ValueError, 'unreviewed platform compile'):
                builder.prepare(root, root, root / 'missing.zip', record, output)
            self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
