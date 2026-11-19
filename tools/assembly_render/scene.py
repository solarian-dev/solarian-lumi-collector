"""Load the Onshape export and place every part in the enclosure frame (mm).

Each top-level instance of the assembly (a board, a standoff, a screw, the two
printed parts) becomes one *group*. Board sub-assemblies have hundreds of
small parts, so a group is drawn as one mesh per colour: `merge()` bakes the
parts' transforms into data/<group>__<colour>.tri/.edg, and the renderer only
moves whole groups.
"""
import base64, json, os, re, struct
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
CAD = os.environ.get('LUMI_CAD', os.path.join(HERE, 'cad'))
A = json.load(open(os.path.join(CAD, 'assembly.json')))
PARTS = json.load(open(os.path.join(CAD, 'parts.json')))
INST = {i['id']: i for i in A['rootAssembly']['instances'] + [x for s in A.get('subAssemblies', []) for x in s['instances']]}


def slug(name):
    s = re.sub(r' <\d+>$', '', name)
    s = s.replace('—', '').replace('+', 'p')
    return re.sub(r'[^A-Za-z0-9]+', '_', s).strip('_').lower()


def color(c):
    if not c: return (0.6, 0.6, 0.6)
    r, g, b, _ = base64.b64decode(c)
    return (r / 255, g / 255, b / 255)


def read_stl(p):
    d = open(p, 'rb').read(); n = struct.unpack('<I', d[80:84])[0]
    return np.frombuffer(d[84:84 + n * 50], dtype=np.dtype([('n', '<f4', 3), ('v', '<f4', (3, 3)), ('a', '<u2')]))['v'].astype(np.float64)


def occurrences():
    """[(top-level instance name, part key, 4x4 world transform in mm)] for every part."""
    out = []
    for o in A['rootAssembly']['occurrences']:
        leaf = INST[o['path'][-1]]
        if leaf['type'] != 'Part': continue
        T = np.array(o['transform']).reshape(4, 4); T[:3, 3] *= 1000
        key = f"{leaf['elementId']}|{leaf['partId']}|{leaf.get('fullConfiguration') or 'default'}"
        out.append((o['path'][0], key, T))
    return out


def groups():
    """group id -> dict(name, parts=[(mesh, T, colour)]). The id is the top-level
    instance name made unique (two root instances can share a name)."""
    G, ids = {}, {}
    for top, key, T in occurrences():
        if top not in ids:
            base = slug(INST[top]['name']) + '_' + re.search(r'<(\d+)>$', INST[top]['name'] + ' <1>').group(1)
            gid = base; n = 2
            while gid in G: gid = f'{base}b{n}'; n += 1
            ids[top] = gid; G[gid] = dict(name=INST[top]['name'], parts=[])
        p = PARTS[key]
        G[ids[top]]['parts'].append((p['mesh'], T, color(p['color'])))
    return G


def world_tris(group):
    return np.concatenate([read_stl(os.path.join(CAD, 'meshes', m + '.stl')) @ T[:3, :3].T + T[:3, 3] for m, T, _ in group])


if __name__ == '__main__':
    for gid, g in groups().items():
        t = world_tris(g['parts']).reshape(-1, 3); lo, hi = t.min(0), t.max(0)
        print(f'{gid:42s} n={len(g["parts"]):3d} lo={np.round(lo,1)} hi={np.round(hi,1)}')
