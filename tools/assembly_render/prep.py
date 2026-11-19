"""Bake every assembly group into render buffers.

For each group (see scene.groups) and each colour in it, write
data/<group>__<n>.tri (float32 position + normal per vertex, mm, enclosure frame)
and data/<group>__<n>.edg (outline segments: open edges and creases > 35°).
data/groups.json lists, per group, its meshes with their colours, and its
bounding box.
"""
import json, os, sys
import numpy as np
from scene import groups, read_stl, CAD

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
os.makedirs(OUT, exist_ok=True)


def edges(tri, crease=35.0):
    flat = tri.reshape(-1, 3)
    q = np.round(flat / 1e-3).astype(np.int64)
    _, inv = np.unique(q, axis=0, return_inverse=True)
    inv = inv.reshape(-1); F = inv.reshape(-1, 3); nV = inv.max() + 1
    V = np.zeros((nV, 3)); V[inv] = flat
    fn = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]); ln = np.linalg.norm(fn, axis=1)
    fn[ln > 1e-12] /= ln[ln > 1e-12, None]
    e = np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]]); fi = np.tile(np.arange(len(F)), 3)
    es = np.sort(e, axis=1); key = es[:, 0] * nV + es[:, 1]
    o = np.argsort(key, kind='stable'); key, es, fi = key[o], es[o], fi[o]
    starts = np.r_[0, np.nonzero(np.diff(key))[0] + 1, len(key)]
    cnt = np.diff(starts); s0 = starts[:-1]
    keep = cnt != 2
    two = cnt == 2
    c = np.einsum('ij,ij->i', fn[fi[s0[two]]], fn[fi[s0[two] + 1]])
    keep[two] = c < np.cos(np.radians(crease))
    segs = es[s0[keep]]
    return V[segs].reshape(-1, 3), fn


def write(name, tri, fn=None):
    if fn is None:
        fn = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]); fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-12)
    np.concatenate([tri.reshape(-1, 3), np.repeat(fn, 3, axis=0)], 1).astype('<f4').tofile(os.path.join(OUT, name + '.tri'))


if __name__ == '__main__':
    cache, info = {}, {}
    for gid, g in groups().items():
        bycol = {}
        for mesh, T, col in g['parts']:
            if mesh not in cache: cache[mesh] = read_stl(os.path.join(CAD, 'meshes', mesh + '.stl'))
            bycol.setdefault(tuple(round(x, 3) for x in col), []).append(cache[mesh] @ T[:3, :3].T + T[:3, 3])
        meshes, allp = [], []
        for i, (col, tris) in enumerate(sorted(bycol.items())):
            tri = np.concatenate(tris); name = f'{gid}__{i}'
            lines, fn = edges(tri)
            write(name, tri, fn); lines.astype('<f4').tofile(os.path.join(OUT, name + '.edg'))
            meshes.append(dict(mesh=name, col=list(col), ntri=len(tri))); allp.append(tri.reshape(-1, 3))
        p = np.concatenate(allp)
        info[gid] = dict(name=g['name'], meshes=meshes, lo=p.min(0).round(2).tolist(), hi=p.max(0).round(2).tolist())
        print(gid, len(g['parts']), 'parts', len(meshes), 'colours', sum(m['ntri'] for m in meshes), 'tris')
    json.dump(info, open(os.path.join(OUT, 'groups.json'), 'w'), indent=1)
