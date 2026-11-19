// Export the CAD data the render pipeline needs, straight from the Onshape web app.
//
// Usage: open the solarian-lumi Onshape document while signed in, open the
// browser devtools console, paste this file and run:
//
//   await exportLumi()
//
// It downloads solarian-lumi-onshape-export.zip containing:
//   assembly.json        "Rev C - Complete Electronics Assembly" (occurrence transforms, mates)
//   parts.json           mesh file, part name and Onshape colour for every part in the assembly
//   meshes/<mesh>.stl    each part tessellated in its part-studio frame (mm)
//   solarian-lumi.step   STEP of the assembly
// Unzip it into tools/assembly_render/cad/ and run ./build.sh.
//
// Parts are tessellated one Part Studio (and configuration) at a time, so the
// ~360 parts of the board models cost 15 requests. The two printed parts get a
// fine tessellation, everything else a coarse one.

const CFG = {
  doc: '41a49b699bc79376fe79f90a',
  ws: '93a291c0f8792e38d9633c68',
  asm: 'e796ce46563799df341ea524',        // Rev C - Complete Electronics Assembly
  enclosure: '960877f40d97a3907268003e',  // Rev C - Native Enclosure (front body + rear lid)
};

const xsrf = () => decodeURIComponent((document.cookie.split(';').map(c => c.trim())
  .find(c => c.startsWith('XSRF-TOKEN=')) || '').split('=').slice(1).join('='));
const getJSON = async (u) => (await fetch(u, { headers: { Accept: 'application/json' } })).json();
const postJSON = async (u, body) => (await fetch(u, {
  method: 'POST', body: JSON.stringify(body),
  headers: { 'Content-Type': 'application/json', Accept: 'application/json', 'X-XSRF-TOKEN': xsrf() },
})).json();
const cleanName = (n) => n.replace(/ <\d+>$/, '').replace(/[^A-Za-z0-9_.-]+/g, '_').replace(/_+/g, '_').replace(/^_|_$/g, '');

function studio(i) {
  const wvm = i.documentId === CFG.doc ? `w/${CFG.ws}`
    : (i.documentVersion ? `v/${i.documentVersion}` : `m/${i.documentMicroversion}`);
  return `/api/partstudios/d/${i.documentId}/${wvm}/e/${i.elementId}`;
}

function stl(facets) { // binary STL in mm
  const buf = new ArrayBuffer(84 + 50 * facets.length), dv = new DataView(buf);
  dv.setUint32(80, facets.length, true);
  let o = 84;
  for (const t of facets) {
    for (let k = 0; k < 3; k++) dv.setFloat32(o + 4 * k, (t.normal || [0, 0, 0])[k], true); o += 12;
    for (const v of t.vertices) { for (let k = 0; k < 3; k++) dv.setFloat32(o + 4 * k, v[k] * 1000, true); o += 12; }
    o += 2;
  }
  return new Uint8Array(buf);
}

async function translate(url, body) {
  let st = await postJSON(url, body);
  while (st.requestState === 'ACTIVE') { await new Promise(r => setTimeout(r, 1000)); st = await getJSON(`/api/translations/${st.id}`); }
  if (st.requestState !== 'DONE') throw new Error(st.failureReason);
  return new Uint8Array(await (await fetch(`/api/documents/d/${st.resultDocumentId}/externaldata/${st.resultExternalDataIds[0]}`)).arrayBuffer());
}

function zip(files) { // store-only zip
  const T = new Uint32Array(256).map((_, n) => { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1; return c >>> 0; });
  const crc = (u) => { let c = 0xFFFFFFFF; for (const b of u) c = T[(c ^ b) & 0xFF] ^ (c >>> 8); return (c ^ 0xFFFFFFFF) >>> 0; };
  const enc = new TextEncoder(), parts = [], central = []; let off = 0;
  for (const [name, data] of files) {
    const nb = enc.encode(name), c = crc(data);
    const lh = new DataView(new ArrayBuffer(30));
    lh.setUint32(0, 0x04034b50, true); lh.setUint16(4, 20, true); lh.setUint32(14, c, true);
    lh.setUint32(18, data.length, true); lh.setUint32(22, data.length, true); lh.setUint16(26, nb.length, true);
    parts.push(new Uint8Array(lh.buffer), nb, data);
    const ch = new DataView(new ArrayBuffer(46));
    ch.setUint32(0, 0x02014b50, true); ch.setUint16(4, 20, true); ch.setUint16(6, 20, true); ch.setUint32(16, c, true);
    ch.setUint32(20, data.length, true); ch.setUint32(24, data.length, true); ch.setUint16(28, nb.length, true); ch.setUint32(42, off, true);
    central.push(new Uint8Array(ch.buffer), nb);
    off += 30 + nb.length + data.length;
  }
  const size = central.reduce((s, x) => s + x.length, 0), end = new DataView(new ArrayBuffer(22));
  end.setUint32(0, 0x06054b50, true); end.setUint16(8, files.length, true); end.setUint16(10, files.length, true);
  end.setUint32(12, size, true); end.setUint32(16, off, true);
  return new Blob([...parts, ...central, new Uint8Array(end.buffer)], { type: 'application/zip' });
}

async function exportLumi() {
  const enc = new TextEncoder(), files = [];
  const A = await getJSON(`/api/assemblies/d/${CFG.doc}/w/${CFG.ws}/e/${CFG.asm}?includeMateFeatures=true`);
  files.push(['assembly.json', enc.encode(JSON.stringify(A))]);
  const inst = [...A.rootAssembly.instances, ...(A.subAssemblies || []).flatMap(s => s.instances)].filter(i => i.type === 'Part');
  // Part Studio + configuration -> the parts used from it
  const groups = new Map();
  for (const i of inst) {
    const cfg = i.fullConfiguration || 'default', k = `${studio(i)}|${cfg}`;
    if (!groups.has(k)) groups.set(k, { base: studio(i), cfg, elementId: i.elementId, parts: new Map() });
    groups.get(k).parts.set(i.partId, i.name);
  }
  const parts = {}; // key "<elementId>|<partId>|<configuration>" -> {mesh, name, color}
  let gi = 0;
  for (const g of groups.values()) {
    const fine = g.elementId === CFG.enclosure;
    const q = `angleTolerance=${fine ? 0.05 : 0.2}&chordTolerance=${fine ? 0.00001 : 0.0001}` +
      `&outputFacetNormals=true&outputVertexNormals=false&configuration=${encodeURIComponent(g.cfg)}`;
    const bodies = await getJSON(`${g.base}/tessellatedfaces?${q}`);
    gi++;
    for (const b of bodies) {
      if (!g.parts.has(b.id)) continue;
      const name = g.parts.get(b.id);
      const mesh = fine ? cleanName(name) : `${cleanName(name)}__${g.elementId.slice(0, 6)}${b.id}${gi}`.slice(0, 120);
      const facets = (b.faces || []).flatMap(f => f.facets || []);
      files.push([`meshes/${mesh}.stl`, stl(facets)]);
      parts[`${g.elementId}|${b.id}|${g.cfg}`] = { mesh, name, color: b.color || null };
    }
    console.log('tessellated', gi, '/', groups.size, g.base, g.parts.size, 'parts');
  }
  files.push(['parts.json', enc.encode(JSON.stringify(parts, null, 1))]);
  files.push(['solarian-lumi.step', await translate(`/api/assemblies/d/${CFG.doc}/w/${CFG.ws}/e/${CFG.asm}/translations`,
    { formatName: 'STEP', storeInDocument: false, destinationName: 'solarian-lumi' })]);
  const a = document.createElement('a');
  a.href = URL.createObjectURL(zip(files)); a.download = 'solarian-lumi-onshape-export.zip';
  a.textContent = 'Download solarian-lumi-onshape-export.zip';
  Object.assign(a.style, { position: 'fixed', top: '80px', left: '50%', zIndex: 999999, background: '#d9480f', color: '#fff', padding: '12px 18px', borderRadius: '8px' });
  document.body.appendChild(a); a.click();
  return files.map(([n, d]) => `${n} ${d.length}`);
}
