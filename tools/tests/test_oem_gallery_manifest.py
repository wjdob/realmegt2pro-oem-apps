from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from oem_gallery_app import xml_chunks,string_pool
from oem_gallery_manifest import add_review_entry,add_visibility_queries,VISIBILITY_QUERIES,ACTIVITY,ACTION

ROOT=Path(__file__).resolve().parents[2]
JDK=Path('/build/lineage/prebuilts/jdk/jdk21/linux-x86/bin')


def fixture():
    strings=['name','exported','excludeFromRecents','scheme','mimeType','manifest','application']
    body=bytearray();offsets=[]
    for s in strings:
        offsets.append(len(body));body+=bytes([len(s),len(s)])+s.encode()+b'\0'
    body+=b'\0'*(-len(body)%4);start=28+4*len(strings)
    pool=struct.pack('<HH6I',1,28,start+len(body),len(strings),0,256,start,0)+struct.pack('<7I',*offsets)+body
    mapping=struct.pack('<HHI5I',0x180,8,28,0x01010003,0x01010010,0x01010017,0x01010027,0x01010026)
    def begin(i):return struct.pack('<HHIII',0x102,16,36,0,0xffffffff)+struct.pack('<II6H',0xffffffff,i,20,20,0,0,0,0)
    def end(i):return struct.pack('<HH5I',0x103,16,24,0,0xffffffff,0xffffffff,i)
    result=pool+mapping+begin(5)+begin(6)+end(6)+end(5)
    return struct.pack('<HHI',3,8,8+len(result))+result


class ReviewManifestTests(unittest.TestCase):
    def test_existing_nodes_stay_exact_and_only_restricted_entry_is_added(self):
        old=xml_chunks(fixture());new=xml_chunks(add_review_entry(fixture()));strings=string_pool(new[0][1])[0]
        self.assertIn(ACTIVITY,strings);self.assertIn(ACTION,strings)
        self.assertEqual(new[1:4],old[1:4]);self.assertEqual(new[-2:],old[-2:])
        added=new[4:-2];tags=[strings[struct.unpack_from('<I',raw,20)[0]] for kind,raw in added if kind==0x102]
        self.assertEqual(tags,['activity','intent-filter','action','category','data','data'])
        # Android's parser expects attribute resource IDs in ascending order.
        mapping=struct.unpack('<5I',new[1][1][8:])
        for kind,raw in added:
            if kind!=0x102:continue
            count=struct.unpack_from('<H',raw,28)[0]
            ids=[mapping[struct.unpack_from('<I',raw,40+20*i)[0]] for i in range(count)]
            self.assertEqual(ids,sorted(ids))
        with self.assertRaises(ValueError):add_review_entry(add_review_entry(fixture()))

    def test_resource_mapping_or_xml_corruption_is_rejected(self):
        data=fixture();chunks=xml_chunks(data);mapping=bytearray(chunks[1][1]);struct.pack_into('<I',mapping,8,0)
        body=chunks[0][1]+mapping+b''.join(raw for _,raw in chunks[2:]);bad=struct.pack('<HHI',3,8,len(body)+8)+body
        for value in [bad,data[:-1]]:
            with self.assertRaises(ValueError):add_review_entry(value)

    def test_visibility_additions_are_queries_only_and_preserve_original_nodes(self):
        old=xml_chunks(fixture());result=add_visibility_queries(fixture());new=xml_chunks(result)
        strings=string_pool(new[0][1])[0]
        self.assertEqual(new[1:3],old[1:3]);self.assertEqual(new[-3:],old[-3:])
        added=new[3:-3]
        tags=[strings[struct.unpack_from('<I',raw,20)[0]] for kind,raw in added if kind==0x102]
        self.assertEqual(tags,['queries','intent','action','data','intent','action','data','intent','action','data'])
        for action,mime in VISIBILITY_QUERIES:self.assertIn(action,strings);self.assertIn(mime,strings)
        self.assertNotIn('uses-permission',strings)
        with self.assertRaises(ValueError):add_visibility_queries(result)

    @unittest.skipUnless((JDK/'javac').exists(),'requires WSL JDK21')
    def test_review_protocol_rejects_noncontent_and_nonmedia_requests(self):
        parent=ROOT/'tmp/gallery-review-policy-tests';parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as tmp:
            p=Path(tmp);source=ROOT/'tools/oem_gallery_compat/ReviewRequest.java'
            probe=p/'Probe.java';probe.write_text('''import realme.gallery.compat.ReviewRequest;
public class Probe {public static void main(String[] args) {
 int cases=0;
 for(String action:new String[]{"android.provider.action.REVIEW","android.intent.action.VIEW",null})
 for(String scheme:new String[]{"content","file","https",null})
 for(String mime:new String[]{"image/jpeg","image/heic","video/mp4","application/pdf","image/",null}) {
  boolean expected="android.provider.action.REVIEW".equals(action)&&"content".equals(scheme)
    &&("image/jpeg".equals(mime)||"image/heic".equals(mime)||"video/mp4".equals(mime));
  if(ReviewRequest.accepts(action,scheme,mime)!=expected)throw new AssertionError();cases++;
 }System.out.print(cases);
}}''')
            subprocess.run([str(JDK/'javac'),'--release','8','-d',str(p),str(source),str(probe)],check=True,capture_output=True)
            value=subprocess.check_output([str(JDK/'java'),'-cp',str(p),'Probe'],text=True)
            self.assertEqual(value,'72')

if __name__=='__main__':unittest.main()
