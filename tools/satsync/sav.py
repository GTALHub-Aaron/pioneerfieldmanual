import struct,zlib
class R:
    def __init__(s,b,p=0): s.b=b; s.p=p
    def i32(s): v=struct.unpack_from('<i',s.b,s.p)[0]; s.p+=4; return v
    def u32(s): v=struct.unpack_from('<I',s.b,s.p)[0]; s.p+=4; return v
    def i64(s): v=struct.unpack_from('<q',s.b,s.p)[0]; s.p+=8; return v
    def f32(s): v=struct.unpack_from('<f',s.b,s.p)[0]; s.p+=4; return v
    def f64(s): v=struct.unpack_from('<d',s.b,s.p)[0]; s.p+=8; return v
    def u8(s): v=s.b[s.p]; s.p+=1; return v
    def fs(s):
        n=s.i32()
        if n==0: return ''
        if n<0:
            n=-n; v=s.b[s.p:s.p+2*n-2].decode('utf-16le','replace'); s.p+=2*n; return v
        v=s.b[s.p:s.p+n-1].decode('latin1'); s.p+=n; return v

def decompress(raw):
    tag=bytes.fromhex('c1832a9e'); out=[]; i=raw.find(tag)
    while i>=0:
        z=raw.find(b'\x78',i+8,i+80)
        # find zlib header start (78 9c / 78 01 / 78 da)
        while z>=0 and raw[z+1] not in (0x01,0x5e,0x9c,0xda): z=raw.find(b'\x78',z+1,i+80)
        d=zlib.decompressobj(); out.append(d.decompress(raw[z:])); nxt=len(raw)-len(d.unused_data); i=raw.find(tag,nxt)
    return b''.join(out)

def read_header(r):
    t=r.i32(); h={'t':t,'cls':r.fs(),'lvl':r.fs(),'inst':r.fs()}
    if t==1:
        h['flags']=r.i32(); h['nt']=r.i32()
        h['rot']=struct.unpack_from('<4f',r.b,r.p); r.p+=16
        h['pos']=struct.unpack_from('<3f',r.b,r.p); r.p+=12
        h['scl']=struct.unpack_from('<3f',r.b,r.p); r.p+=12
        h['pil']=r.i32()
    elif t==0:
        h['flags']=r.i32(); h['outer']=r.fs()
    else: raise ValueError('bad type %d at %d'%(t,r.p))
    return h

class MultiV(list): pass
NATIVE={'Vector':24,'Rotator':24,'Quat':32,'Vector2D':16,'LinearColor':16,'Color':4,'Guid':16,'IntPoint':8,'Box':49,'Vector4':32}
def read_type(r):
    name=r.fs(); n=r.i32(); return (name,[read_type(r) for _ in range(n)])
def read_props(r,end=None):
    out={}
    while True:
        name=r.fs()
        if name=='None' or name=='': break
        t=read_type(r); size=r.i32(); flags=r.u8()
        if flags&1: r.i32()
        if flags&2: r.p+=16
        if flags&4:  # property extensions
            ext=r.u8()
            if ext&2: r.p+=1  # override operation
        st=r.p
        try: v=read_value(r,t,size,flags)
        except Exception as e: v=('err',str(e))
        r.p=st+size
        if name in out:
            prev=out[name]
            out[name]=(prev if isinstance(prev,MultiV) else MultiV([prev]))+MultiV([v])
        else: out[name]=v
    return out
def objref(r): return (r.fs(),r.fs())
def read_value(r,t,size,flags):
    tn=t[0]
    if tn=='BoolProperty': return bool(flags&0x10)
    if tn=='IntProperty': return r.i32()
    if tn=='Int64Property': return r.i64()
    if tn=='FloatProperty': return r.f32()
    if tn=='DoubleProperty': return r.f64()
    if tn in('StrProperty','NameProperty'): return r.fs()
    if tn in('ObjectProperty','SoftObjectProperty'): return objref(r)
    if tn=='EnumProperty': return r.fs()
    if tn=='ByteProperty':
        return r.fs() if size>1 else r.u8()
    if tn=='StructProperty': return read_struct(r,t[1][0][0] if t[1] else '',size)
    if tn in('ArrayProperty','SetProperty'):
        it=t[1][0]
        if tn=='SetProperty': r.i32()
        n=r.i32(); return [read_elem(r,it) for _ in range(n)]
    return None
def read_elem(r,t):
    tn=t[0]
    if tn=='StructProperty': return read_struct(r,t[1][0][0] if t[1] else '',None)
    return read_value(r,t,0,0) if tn not in('ByteProperty',) else r.u8()
def read_struct(r,sn,size):
    sn=sn.split('.')[-1]
    if sn in('Vector','Rotator'): v=struct.unpack_from('<3d',r.b,r.p); r.p+=24; return v
    if sn in('Quat','Vector4'): v=struct.unpack_from('<4d',r.b,r.p); r.p+=32; return v
    if sn=='LinearColor': v=struct.unpack_from('<4f',r.b,r.p); r.p+=16; return v
    if sn=='Color': v=tuple(r.b[r.p:r.p+4]); r.p+=4; return v
    if sn=='Guid': r.p+=16; return None
    if sn=='Vector2D': v=struct.unpack_from('<2d',r.b,r.p); r.p+=16; return v
    if sn=='IntPoint': v=struct.unpack_from('<2i',r.b,r.p); r.p+=8; return v
    if sn=='InventoryItem' and size!=0:
        r.i32(); path=r.fs(); return {'_item':path}
    if sn in NATIVE: r.p+=NATIVE[sn]; return None
    return read_props(r)

def read_lightweight(b,p,end):
    q=b.find(b'None\x00',p)+5
    r=R(b,q); r.i32(); ver=r.i32(); ncls=r.i32()
    out=[]
    for _ in range(ncls):
        r.i32(); cls=r.fs(); n=r.i32()
        for _ in range(n):
            rot=struct.unpack_from('<4d',b,r.p); pos=struct.unpack_from('<3d',b,r.p+32); r.p+=80
            r.i32(); sw=r.fs()
            for _ in range(6): r.fs()
            col=struct.unpack_from('<8f',b,r.p); r.p+=32
            r.i32(); fin=r.fs(); r.u8(); r.i32()
            rec=r.fs(); r.fs(); r.fs()
            if r.i32():
                r.i32(); r.fs(); n2=r.i32(); r.p+=n2
            r.u8(); r.i32()
            if not rec.startswith('/Game') and rec!='': raise ValueError('bad rec %r at %d cls %s idx %d'%(rec[:40],r.p,cls,len(out)))
            out.append((cls,pos,rot,sw,rec))
    return out,r.p,end
