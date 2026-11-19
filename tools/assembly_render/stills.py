"""Hero, back, exploded and print-plate renders -> stills.json (render with render.mjs)."""
import json, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import anim
from anim import ITEM, ALL, trans, item_parts
from prep import edges, write, OUT
from scene import read_stl

HERE = os.path.dirname(os.path.abspath(__file__))
# Product pose for the hero shots: lens towards the viewer, mount underneath, like a GoPro.
RX = np.array([[1, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0], [0, 0, 0, 1.]])


def parts(keys=ALL, offsets=None, pose=np.eye(4)):
    out = []
    for k in keys:
        T = pose @ trans((offsets or {}).get(k, [0, 0, 0])) @ ITEM[k]['M']
        out += item_parts(k, T)
    return out


shots = []
shots.append(dict(file='hero.png', cam=dict(target=[0, -8, 0], yaw=-58, pitch=18, dist=230, fov=30), parts=parts(pose=RX), bg=[1, 1, 1]))
RZ = np.diag([-1., -1, 1, 1])  # half turn about the vertical: the lid faces the camera and the key light
shots.append(dict(file='hero_back.png', cam=dict(target=[0, -8, 0], yaw=-58, pitch=22, dist=230, fov=30), parts=parts(pose=RZ @ RX), bg=[1, 1, 1]))

# exploded along the build axis, in build order
Z = {}
layers = [(anim.LENS[1:], -46), (anim.LENS[:1], -28), (anim.FRONTSCR, -16), (anim.IMU, 44), (anim.CAMERA, 58), (anim.CAMSCR, 72),
          (anim.SO_FRONT, 84), (anim.UPS, 96), (anim.SO_FF, 110), (anim.PI, 124), (anim.SO_MF, 138), (anim.LCD, 154), (anim.LCDSCR, 168),
          (anim.LID, 180), (anim.LIDSCR, 192)]
for keys, dz in layers:
    for k in keys: Z[k] = [0, 0, dz]
# laid on its side (product pose), so the build axis runs across the image
shots.append(dict(file='exploded.png', cam=dict(target=[0, 78, 2], yaw=-30, pitch=22, dist=400, fov=30), parts=parts(offsets=Z, pose=RX), bg=[1, 1, 1]))

# the print plate on a 220 x 220 bed
plate = read_stl(os.path.join(HERE, '..', '..', 'STL', 'Plates', 'lumi_enclosure_plate_220x220.stl'))
lines, fn = edges(plate); write('plate_enclosure', plate, fn); lines.astype('<f4').tofile(os.path.join(OUT, 'plate_enclosure.edg'))
I = [1., 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
shots.append(dict(file='plate.png', cam=dict(target=[0, 0, 10], yaw=-60, pitch=38, dist=340, fov=30),
                  parts=[dict(mesh='bedplate', M=I, col=[0.85, 0.87, 0.9]), dict(mesh='plate_enclosure', M=I, col=list(anim.PLA))], bg=[1, 1, 1]))
json.dump(shots, open('stills.json', 'w'))
