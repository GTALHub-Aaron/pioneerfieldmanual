#!/usr/bin/env python3
"""Turn a Satisfactory 1.1 save (.sav) into a compact JSON layer for the PFM map.

Usage: extract.py save.sav out.json
No third-party packages: runs anywhere Python 3.8+ is installed.
"""
import sys, json, math, struct, time, re
from sav import R, decompress, read_header, read_props, objref, read_lightweight

def qrot(q, v):
    x, y, z, w = q; vx, vy, vz = v
    tx = 2*(y*vz-z*vy); ty = 2*(z*vx-x*vz); tz = 2*(x*vy-y*vx)
    return (vx+w*tx+(y*tz-z*ty), vy+w*ty+(z*tx-x*tz), vz+w*tz+(x*ty-y*tx))

def yaw(q):
    x, y, z, w = q
    return math.degrees(math.atan2(2*(w*z+x*y), 1-2*(y*y+z*z)))

def short(path):
    return path.rsplit('.', 1)[-1] if path else ''

def r10(v):  # world cm -> integer decimetres
    return int(round(v/10))

def hermite(p0, m0, p1, m1, n):
    out = []
    for k in range(1, n+1):
        t = k/n; t2 = t*t; t3 = t2*t
        a = 2*t3-3*t2+1; bb = t3-2*t2+t; c = -2*t3+3*t2; d = t3-t2
        out.append(tuple(a*p0[i]+bb*m0[i]+c*p1[i]+d*m1[i] for i in range(3)))
    return out

def spline_world(h, pts):
    """Sample a spline (local points + tangents) into world-space XY(Z) points."""
    q, pos = h['rot'], h['pos']
    loc = [pts[0]['Location']]
    for a, b in zip(pts, pts[1:]):
        p0, p1 = a['Location'], b['Location']
        m0, m1 = a['LeaveTangent'], b['ArriveTangent']
        ch = [p1[i]-p0[i] for i in range(3)]
        L = math.sqrt(sum(c*c for c in ch)) or 1
        def dev(m):
            ml = math.sqrt(sum(c*c for c in m)) or 1
            return 1-sum(m[i]*ch[i] for i in range(3))/(ml*L)
        straight = dev(m0) < 1e-3 and dev(m1) < 1e-3
        n = 1 if straight else max(2, min(12, int(L/250)+2))
        loc += hermite(p0, m0, p1, m1, n)
    out = []
    for p in loc:
        w = qrot(q, p)
        out.append((pos[0]+w[0], pos[1]+w[1], pos[2]+w[2]))
    return out

SKIP = re.compile(r'Sign|Ladder|VehiclePath|ConveyorPole|PipelineSupport|PipeHyperSupport|'
                  r'WallHole|CeilingAttachment|FoundationPassthrough|FlowIndicator|StreetLight|'
                  r'PowerTowerPlatform|ConveyorMonitor|Potty|PowerPoleWall')


COLLECT = re.compile(r'^(BP_Crystal|BP_WAT|BP_DropPod)')

def found_collectibles(b, hs, objs, P):
    """Names of slugs, Somersloops, Mercer Spheres and crash sites that are picked up or looted.
    Picked-up actors are listed in each level's destroyed-actor lists; opened crash sites keep a flag."""
    def refs(r, end):
        st = r.p
        try:
            n = r.i32(); out = [(r.fs(), r.fs()) for _ in range(n)]
            if r.p == end: return out
        except Exception: pass
        r.p = st
        try:
            m = r.i32(); out = []
            for _ in range(m):
                r.fs(); n = r.i32(); out += [(r.fs(), r.fs()) for _ in range(n)]
            if r.p == end: return out
        except Exception: pass
        return None
    gone = []
    for m in re.finditer(rb'\x1a\x00\x00\x00[0-9A-Z]{25}\x00', b):
        p = m.end()
        try:
            L = struct.unpack_from('<q', b, p)[0]
            if not (0 < L < 5_000_000): continue
            r = R(b, p+8); base = r.p; n = r.i32()
            if not (0 <= n < 200000): continue
            for _ in range(n): read_header(r)
            c = refs(r, base+L)
            if c is None: continue
            gone += c
            r.p = base+L; OL = r.i64(); r.p += OL
            n2 = r.i32()
            if 0 <= n2 < 5000:
                try: gone += [(r.fs(), r.fs()) for _ in range(n2)]
                except Exception: pass
        except Exception:
            continue
    # the persistent level's own list sits right after its headers
    i = b.find(b'$\x00\x00\x00/Script/FactoryGame.FGWorldSettings\x00\x11\x00\x00\x00Persistent_Level')
    if i >= 0:
        r = R(b, i-16); L = r.i64(); base = r.p; n = r.i32()
        for _ in range(n): read_header(r)
        c = refs(r, base+L)
        if c: gone += c
    names = {x[1].rsplit('.', 1)[-1] for x in gone}
    for i, h in enumerate(hs):
        if 'DropPod' in h['cls'] and h['t'] == 1:
            pr = P(i)
            if pr.get('mHasBeenLooted') or pr.get('mHasBeenOpened'): names.add(h['inst'].rsplit('.', 1)[-1])
    return sorted(n for n in names if COLLECT.match(n))

def find_persistent(b):
    i = b.find(b'$\x00\x00\x00/Script/FactoryGame.FGWorldSettings\x00\x11\x00\x00\x00Persistent_Level')
    if i < 0:
        raise SystemExit('persistent level not found')
    r = R(b, i-4-12)
    r.i64(); n = r.i32()
    hs = [read_header(r) for _ in range(n)]
    r.i32(); r.fs(); c = r.i32()
    for _ in range(c): r.fs(); r.fs()
    r.i64(); on = r.i32()
    objs = []
    for _ in range(on):
        r.i32(); r.i32(); sz = r.i32(); objs.append((r.p, sz)); r.p += sz+4
    return hs, objs

def main(src, dst):
    raw = open(src, 'rb').read()
    b = decompress(raw)
    hs, objs = find_persistent(b)
    idx = {h['inst']: i for i, h in enumerate(hs)}
    cache = {}

    def P(i):
        if i in cache: return cache[i]
        h = hs[i]; p, sz = objs[i]; r = R(b, p)
        if h['t'] == 1:
            r.fs(); r.fs(); n = r.i32()
            for _ in range(n): objref(r)
        r.u8()
        try: pr = read_props(r)
        except Exception: pr = {}
        pr['_end'] = r.p
        cache[i] = pr
        return pr

    def comp(inst, name):
        return idx.get(inst+'.'+name)

    def inv_summary(ci, limit=12, slots=False):
        if ci is None: return None
        pr = P(ci)
        st = pr.get('mInventoryStacks') or []
        tot = {}; used = 0
        for s in st:
            if not isinstance(s, dict): continue
            it = s.get('Item'); n = s.get('NumItems') or 0
            path = it.get('_item') if isinstance(it, dict) else None
            if path and n: tot[short(path)] = tot.get(short(path), 0)+n; used += 1
        items = sorted(tot.items(), key=lambda kv: -kv[1])[:limit]
        if slots: return items, used, len(st)
        return items or None

    def allowed(ci):
        if ci is None: return None
        a = [short(x[1]) for x in (P(ci).get('mAllowedItemDescriptors') or []) if isinstance(x, tuple) and x[1].startswith('/Game')]
        return a or None

    classes = []; cidx = {}
    def C(name):
        if name not in cidx: cidx[name] = len(classes); classes.append(name)
        return cidx[name]

    blds, belts, pipes, tubes, wires, players = [], [], [], [], [], []
    for i, h in enumerate(hs):
        if h['t'] != 1: continue
        cls = short(h['cls']); inst = h['inst']
        if cls == 'Char_Player_C':
            pr = P(i)
            players.append({'n': pr.get('mCachedPlayerName') or 'Pioneer',
                            'x': r10(h['pos'][0]), 'y': r10(h['pos'][1]), 'z': r10(h['pos'][2])})
            continue
        if '/Buildable/' not in h['cls'] or SKIP.search(cls): continue
        if 'ConveyorBelt' in cls or cls.startswith('Build_Pipeline') and 'Pump' not in cls and 'Junction' not in cls or cls == 'Build_PipeHyper_C':
            pr = P(i); sp = pr.get('mSplineData')
            if not sp: continue
            pts = spline_world(h, sp)
            flat = []
            for p in pts:
                q = [r10(p[0]), r10(p[1])]
                if flat[-2:] != q: flat += q
            z = r10(sum(p[2] for p in pts)/len(pts))
            if 'ConveyorBelt' in cls:
                m = re.search(r'Mk(\d)', cls); belts.append([int(m.group(1)) if m else 1, z, flat])
            elif 'Hyper' in cls:
                tubes.append([0, z, flat])
            else:
                pipes.append([2 if 'MK2' in cls else 1, z, flat])
            continue
        if cls == 'Build_PowerLine_C':
            pr = P(i); wi = pr.get('mWireInstances')
            try:
                a, c = wi[0]['Locations']
                wires.append([r10(a[0]), r10(a[1]), r10(c[0]), r10(c[1])])
            except Exception:
                pass
            continue
        x, y, z = h['pos']
        rec = [C(cls), r10(x), r10(y), r10(z), int(round(yaw(h['rot'])))]
        info = {}
        pr = P(i)
        if 'mCurrentRecipe' in pr and isinstance(pr['mCurrentRecipe'], tuple):
            info['r'] = short(pr['mCurrentRecipe'][1])
        if 'mCurrentPotential' in pr:
            info['c'] = round(pr['mCurrentPotential']*100, 1)
        elif 'mPendingPotential' in pr:
            info['c'] = round(pr['mPendingPotential']*100, 1)
        if 'mCurrentProductionBoost' in pr:
            info['s'] = round(pr['mCurrentProductionBoost']*100)
        d = pr.get('mLastProductivityMeasurementDuration')
        if d:
            info['p'] = round(100*(pr.get('mLastProductivityMeasurementProduceDuration') or 0)/d)
        if pr.get('mIsProductionPaused'): info['off'] = 1
        pot = comp(inst, 'InventoryPotential')
        if pot is not None:
            items = dict(inv_summary(pot) or [])
            if items.get('Desc_CrystalShard_C'): info['sh'] = items['Desc_CrystalShard_C']
            if items.get('Desc_WAT1_C'): info['sl'] = items['Desc_WAT1_C']
        pwi = comp(inst, 'powerInfo')
        if pwi is not None and 'Generator' not in cls:
            tc = P(pwi).get('mTargetConsumption')
            if isinstance(tc, float) and tc > 0: info['pw'] = round(tc, 2)
        if 'mCurrentRecipe' in pr:
            ii, oi = comp(inst, 'InputInventory'), comp(inst, 'OutputInventory')
            a, b2 = inv_summary(ii), inv_summary(oi)
            if a: info['in'] = a
            if b2: info['out'] = b2
            aa, ob = allowed(ii), allowed(oi)
            if aa: info['ia'] = aa
            if ob: info['oa'] = ob
        if 'Storage' in cls or 'CentralStorage' in cls:
            r3 = inv_summary(comp(inst, 'StorageInventory') or comp(inst, 'inventory'), 24, True)
            if r3:
                items, used, total = r3
                if items: info['i'] = items
                if total: info['slots'] = [used, total]
        elif 'Generator' in cls:
            pi = comp(inst, 'powerInfo')
            if pi is not None:
                cap = P(pi).get('mDynamicProductionCapacity') or P(pi).get('mBaseProduction')
                if cap: info['mw'] = round(cap, 1)
            s = inv_summary(comp(inst, 'FuelInventory') or comp(inst, 'InputInventory'))
            if s: info['i'] = s
        if 'Miner' in cls or 'Pump' in cls and 'Pipeline' not in cls or 'Fracking' in cls:
            ex = pr.get('mExtractableResource')
            if isinstance(ex, tuple):
                ri = idx.get(ex[1])
                if ri is not None:
                    rp = P(ri)
                    if 'mResourceClass' in rp and isinstance(rp['mResourceClass'], tuple):
                        info['res'] = short(rp['mResourceClass'][1])
                    if 'mPurity' in rp: info['pur'] = str(rp['mPurity']).split('_')[-1]
        if info: rec.append(info)
        blds.append(rec)

    # lightweight buildables (foundations, walls, ramps, beams ...)
    found = []
    for i, h in enumerate(hs):
        if h['inst'].endswith('LightweightBuildableSubsystem'):
            p, sz = objs[i]
            lw, _, _ = read_lightweight(b, p, p+sz+4)
            for cls, pos, rot, sw, recipe in lw:
                c = short(cls)
                if re.search(r'Railing|Beam|Pillar|Barrier|Catwalk|Shelf|Frame', c): continue
                found.append([C(c), r10(pos[0]), r10(pos[1]), r10(pos[2]), int(round(yaw(rot)))])
            break

    try: got = found_collectibles(b, hs, objs, P)
    except Exception as e: got = []; print('could not read collected items:', e)
    out = {'v': 1, 'synced': int(time.time()), 'cls': classes, 'b': blds, 'f': found, 'found': got,
           'belts': belts, 'pipes': pipes, 'tubes': tubes, 'wires': wires, 'players': players}
    js = json.dumps(out, separators=(',', ':'))
    open(dst, 'w').write(js)
    print(f'{len(blds)} buildings, {len(found)} foundations/walls, {len(belts)} belts, {len(pipes)} pipes, '
          f'{len(wires)} wires, {len(players)} players, {len(got)} collectibles found -> {len(js)//1024} KB')

if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
