# assembly_render

Scripts that turn the Onshape assembly into the build assets in this repo:

- `STL/Individual/*.stl`: the front body and rear lid, rotated onto their best face for printing
- `STL/Plates/lumi_enclosure_plate_220x220.stl`: both on one plate
- `docs/media/assembly/*.mp4|webp`: the step-by-step assembly animations
- `docs/media/*.png`: hero, back, exploded and plate renders

Everything is rendered headless with WebGL in Chromium, so no Blender or GPU
is needed. Same pipeline as `solarian-gripper1`, adapted to an assembly whose
board models have hundreds of parts and whose standoffs and screws are
modelled.

## 1. Export from Onshape

Open the `solarian-lumi` Onshape document while signed in, open the browser
devtools console, paste [`onshape_export.js`](onshape_export.js) and run
`await exportLumi()`. It downloads `solarian-lumi-onshape-export.zip` (≈ 40 MB) with:

| File | What |
| --- | --- |
| `assembly.json` | "Rev C - Complete Electronics Assembly" (occurrence transforms + mates) |
| `parts.json` | mesh file, name and Onshape colour of every part |
| `meshes/*.stl` | each part tessellated in its part-studio frame (mm); fine for the two printed parts, coarse for the rest |
| `solarian-lumi.step` | STEP of the whole assembly (not committed, ≈ 20 MB) |

Unzip it into `cad/` (git-ignored, it is large). Update the IDs at the top of
`onshape_export.js` if the assembly or the enclosure Part Studio is recreated.
[`STEP/solarian-lumi-enclosure.step`](../../STEP/solarian-lumi-enclosure.step)
is a STEP export of the "Rev C - Native Enclosure" Part Studio (front body + rear lid).

## 2. Build

```bash
cd tools/assembly_render
./build.sh
```

Requirements: Python 3 with `numpy` and `pillow`, Node with `playwright`
(Chrome or Chromium with WebGL via SwiftShader is fine; set `CHROME` to use an
installed Chrome), and `ffmpeg` with `libx264` and `libwebp`.

## How it works

| File | Role |
| --- | --- |
| `scene.py` | Loads the export and groups every part under its top-level assembly instance (a board, a standoff, a screw, a printed part), in the enclosure frame: +z from the front face to the lid. |
| `prep.py` | Bakes each group into one mesh per colour (`data/<group>__<n>.tri`) with outline edges (`.edg`, open edges + creases > 35°), and writes `data/groups.json`. |
| `fasteners.py` | Procedural screws for what the CAD leaves out: the LCD HAT's M2.5×5 and the lid's M2×8; plus the bed plate. |
| `anim.py` | The build sequence: per step, which groups are already in, offset keyframes (and spins) for what moves in, and the camera path. Edit this to change the guide. |
| `viewer.html` + `render.mjs` | Minimal WebGL renderer (shaded + outlines, clipping plane; lights flip when the camera looks from below) driven by Playwright; renders a JSON list of shots to PNGs. |
| `encode.py` | Adds step titles and encodes per-step MP4s, a full MP4 and 640 px animated WebP previews. |
| `orient.py` | Picks a print orientation per part (max bed contact, min overhang) and writes `STL/Individual`. `out=mesh` renames, `:x,y,z` forces a down direction. |
| `plates.py` | Packs the oriented parts into the plate STL. |
| `stills.py` | Hero, back, exploded and plate renders. |
