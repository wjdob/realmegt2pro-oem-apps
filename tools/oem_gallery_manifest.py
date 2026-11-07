"""Append a restricted modern-review trampoline to the pinned Gallery manifest."""
import struct
from oem_gallery_app import ANDROID, append_string, string_pool, xml_chunks

ACTIVITY='realme.gallery.compat.ReviewActivity'
ACTION='android.provider.action.REVIEW'
NONE=0xffffffff
VISIBILITY_QUERIES=(('android.service.wallpaper.CROP_AND_SET_WALLPAPER','image/*'),
                    ('android.intent.action.SEND','video/*'),
                    ('android.intent.action.SEND_MULTIPLE','video/*'))


def add_review_entry(data):
    chunks=xml_chunks(data);pools=[i for i,(kind,_) in enumerate(chunks) if kind==1]
    if len(pools)!=1:raise ValueError('one manifest string pool required')
    pool_index=pools[0];pool=chunks[pool_index][1];strings=string_pool(pool)[0]
    if ACTIVITY in strings or ACTION in strings:raise ValueError('review entry already present or unexpected input')
    needed=['activity','intent-filter','action','category','data','application',ANDROID,
            'name','exported','excludeFromRecents','scheme','mimeType',
            'android.intent.category.DEFAULT','content','image/*','video/*']
    for value in needed:
        if value not in strings:pool,_=append_string(pool,value)
    for value in [ACTIVITY,ACTION]:pool,_=append_string(pool,value)
    strings=string_pool(pool)[0];idx={s:i for i,s in enumerate(strings)}
    maps=[raw for kind,raw in chunks if kind==0x180]
    if len(maps)!=1:raise ValueError('one resource map required')
    ids=struct.unpack('<'+'I'*((len(maps[0])-8)//4),maps[0][8:])
    for name,expected in [('name',0x01010003),('exported',0x01010010),('excludeFromRecents',0x01010017),('scheme',0x01010027),('mimeType',0x01010026)]:
        if idx[name]>=len(ids) or ids[idx[name]]!=expected:raise ValueError('attribute resource map differs')
    def attr(name,value,typ=3):
        item=idx[value] if typ==3 else value
        return struct.pack('<IIIHBBI',idx[ANDROID],idx[name],item if typ==3 else NONE,8,0,typ,item)
    def start(tag,attributes=()):
        attributes=sorted(attributes,key=lambda a:ids[struct.unpack_from('<I',a,4)[0]])
        return struct.pack('<HHIII',0x102,16,36+20*len(attributes),0,NONE)+struct.pack('<II6H',NONE,idx[tag],20,20,len(attributes),0,0,0)+b''.join(attributes)
    def end(tag):return struct.pack('<HHIIIII',0x103,16,24,0,NONE,NONE,idx[tag])
    def leaf(tag,attrs):return start(tag,attrs)+end(tag)
    entry=start('activity',[attr('name',ACTIVITY),attr('exported',NONE,0x12),attr('excludeFromRecents',NONE,0x12)])
    entry+=start('intent-filter')+leaf('action',[attr('name',ACTION)])
    entry+=leaf('category',[attr('name','android.intent.category.DEFAULT')])
    entry+=leaf('data',[attr('scheme','content'),attr('mimeType','image/*')])
    entry+=leaf('data',[attr('mimeType','video/*')])+end('intent-filter')+end('activity')
    stack=[];position=None
    for i,(kind,raw) in enumerate(chunks):
        if kind not in (0x102,0x103):continue
        name_id=struct.unpack_from('<I',raw,20)[0]
        if name_id>=len(string_pool(chunks[pool_index][1])[0]):raise ValueError('unknown original node')
        name=strings[name_id]
        if kind==0x102:stack.append(name)
        else:
            if not stack or stack.pop()!=name:raise ValueError('unbalanced manifest')
            if name=='application':
                if position is not None:raise ValueError('multiple applications')
                position=i
    if stack or position is None:raise ValueError('missing application')
    chunks[pool_index]=(1,pool)
    body=b''.join(raw for _,raw in chunks[:position])+entry+b''.join(raw for _,raw in chunks[position:])
    result=struct.pack('<HHI',3,8,8+len(body))+body
    xml_chunks(result)
    return result


def add_visibility_queries(data):
    """Declare the cropper/video handlers the app queries; grant no permission."""
    chunks=xml_chunks(data);pools=[i for i,(kind,_) in enumerate(chunks) if kind==1]
    if len(pools)!=1:raise ValueError('one manifest string pool required')
    pi=pools[0];pool=chunks[pi][1];original=string_pool(pool)[0]
    if VISIBILITY_QUERIES[0][0] in original:raise ValueError('visibility query already present or unexpected input')
    needed=['queries','intent','action','data','name','mimeType',ANDROID]
    needed += [value for row in VISIBILITY_QUERIES for value in row]
    for value in needed:
        if value not in string_pool(pool)[0]:pool,_=append_string(pool,value)
    strings=string_pool(pool)[0];idx={s:i for i,s in enumerate(strings)}
    maps=[raw for kind,raw in chunks if kind==0x180]
    if len(maps)!=1:raise ValueError('one resource map required')
    ids=struct.unpack('<'+'I'*((len(maps[0])-8)//4),maps[0][8:])
    for name,want in [('name',0x01010003),('mimeType',0x01010026)]:
        if idx[name]>=len(ids) or ids[idx[name]]!=want:raise ValueError('attribute resource map differs')
    def start(tag,attrs=()):
        raw=[]
        for name,value in sorted(attrs,key=lambda row:ids[idx[row[0]]]):
            raw.append(struct.pack('<IIIHBBI',idx[ANDROID],idx[name],idx[value],8,0,3,idx[value]))
        return struct.pack('<HHIII',0x102,16,36+20*len(raw),0,NONE)+struct.pack('<II6H',NONE,idx[tag],20,20,len(raw),0,0,0)+b''.join(raw)
    def end(tag):return struct.pack('<HHIIIII',0x103,16,24,0,NONE,NONE,idx[tag])
    entry=b''
    for action,mime in VISIBILITY_QUERIES:
        entry+=start('intent')+start('action',[('name',action)])+end('action')
        entry+=start('data',[('mimeType',mime)])+end('data')+end('intent')
    stack=[];qp=None;ap=None
    for i,(kind,raw) in enumerate(chunks):
        if kind not in (0x102,0x103):continue
        ni=struct.unpack_from('<I',raw,20)[0]
        if ni>=len(original):raise ValueError('unknown node name')
        name=original[ni]
        if kind==0x102:
            if name=='application' and stack==['manifest']:
                if ap is not None:raise ValueError('multiple applications')
                ap=i
            stack.append(name)
        else:
            if not stack or stack.pop()!=name:raise ValueError('unbalanced manifest')
            if name=='queries' and stack==['manifest']:
                if qp is not None:raise ValueError('multiple query blocks')
                qp=i
    if stack or ap is None:raise ValueError('missing application')
    pos=qp if qp is not None else ap
    if qp is None:entry=start('queries')+entry+end('queries')
    chunks[pi]=(1,pool)
    body=b''.join(raw for _,raw in chunks[:pos])+entry+b''.join(raw for _,raw in chunks[pos:])
    result=struct.pack('<HHI',3,8,8+len(body))+body;xml_chunks(result);return result
