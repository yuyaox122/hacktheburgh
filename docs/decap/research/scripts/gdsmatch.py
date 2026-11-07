"""Check that netlist instance names appear in TT08 GDS files (GDS property 61).

Usage, from the repo root:  python3 docs/decap/research/scripts/gdsmatch.py decap/data/gds
Reads decap/data/tt08/<chip>/netlist.v via decap's core.netlist and every <chip>.gds in the given folder.
"""
import sys, struct, os
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','..','..'))
sys.path.insert(0,os.path.join(ROOT,'decap'))
from core import netlist
def srefs(path):
    d=open(path,'rb').read(); i=0; out={}; cur=None
    while i+4<=len(d):
        ln,rt,dt=struct.unpack('>HBB',d[i:i+4])
        if ln<4: break
        body=d[i+4:i+ln]
        if rt==0x0A: cur={'xy':None,'props':[]}
        elif rt==0x12 and cur is not None: cur['sname']=body.rstrip(b'\0').decode()
        elif rt==0x10 and cur is not None and cur['xy'] is None: cur['xy']=struct.unpack('>ii',body[:8])
        elif rt==0x2C and cur is not None: cur['props'].append(body.rstrip(b'\0').decode(errors='replace'))
        elif rt==0x11 and cur is not None:
            for p in cur['props']: out[p]=(cur.get('sname'),cur['xy'])
            cur=None
        i+=ln
    return out
GDS=sys.argv[1] if len(sys.argv)>1 else os.path.join(ROOT,'decap','data','gds')
for f in sorted(os.listdir(GDS)):
    chip=f[:-4]; p=os.path.join(ROOT,'decap','data','tt08',chip,'netlist.v')
    if not os.path.exists(p): continue
    try:
        nl=netlist.load(p); g=srefs(os.path.join(GDS,f))
        names=set(nl._orig.values()); hit=sum(1 for n in names if n in g or n.replace('\\','') in g)
        print(chip, 'logic',len(names),'found',hit,'gds_props',len(g))
    except Exception as e: print(chip,'ERR',repr(e)[:120])
