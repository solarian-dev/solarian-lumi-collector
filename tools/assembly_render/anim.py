"""The LUMI build sequence: which groups are in, what moves in per step, and the camera path.

Frame: enclosure frame from the Onshape assembly, mm. +z runs from the front
face (z = 0, camera bore) to the rear lid (z = 39); the GoPro-style mount is on
+y. The viewer's up axis is +z, so the open enclosure faces up and parts drop
in from above, like building it face-down on the bench.
"""
import json, sys, os
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
FPS = 30
G = json.load(open(os.path.join(HERE, 'data', 'groups.json')))

PLA = (0.93, 0.93, 0.92); ORANGE = (0.98, 0.6, 0.2); STEEL = (0.32, 0.33, 0.36)
# Printed parts get print colours; everything bought keeps its Onshape colour.
RECOLOR = {'front_body_native_1': PLA, 'rear_lid_native_1': ORANGE}


def trans(v):
    M = np.eye(4); M[:3, 3] = v; return M


def rot_z(a, c=(0, 0, 0)):
    M = np.eye(4); M[:2, :2] = [[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]]
    return trans(c) @ M @ trans(-np.asarray(c, float))


def place(seat, d):
    d = np.asarray(d, float); d /= np.linalg.norm(d); z = -d
    a = np.array([1., 0, 0]) if abs(z[0]) < 0.9 else np.array([0, 1., 0])
    x = np.cross(a, z); x /= np.linalg.norm(x); y = np.cross(z, x)
    M = np.eye(4); M[:3, 0] = x; M[:3, 1] = y; M[:3, 2] = z; M[:3, 3] = seat; return M


# item key -> dict(meshes=[(mesh, col)], M=rest pose, dir=insertion direction or None)
ITEM = {}
for gid, g in G.items():
    ITEM[gid] = dict(meshes=[(m['mesh'], RECOLOR.get(gid, tuple(m['col']))) for m in g['meshes']], M=np.eye(4), dir=None)


def addf(key, mesh, seat, d):
    ITEM[key] = dict(meshes=[(mesh, STEEL)], M=place(seat, d), dir=np.asarray(d, float) / np.linalg.norm(d))


# Fasteners the CAD leaves out.
PADS = [(x, y) for x in (-29.0, 29.0) for y in (-11.5, 11.5)]
for x, y in PADS: addf(f'lcd_scr_{x:+.0f}_{y:+.1f}', 'scr_M2.5x5', [x, y, 37.8], [0, 0, -1])
for x, y in ((-31.9, -18.6), (31.9, -18.6), (-31.9, 20.0), (31.9, 20.0)): addf(f'lid_scr_{x:+.0f}_{y:+.1f}', 'pan_M2x8', [x, y, 38.6], [0, 0, -1])


def sel(prefix): return sorted(k for k in ITEM if k.startswith(prefix))


# CAD screws: their insertion direction, from the mesh profiles (head end -> tip).
for k in sel('ph_pan_head_screw_m2x0_40'): ITEM[k]['dir'] = np.array([0, 0, -1.])   # camera: from inside, towards the front wall
for k in sel('ph_pan_head_screw_m2_5'): ITEM[k]['dir'] = np.array([0, 0, 1.])       # standoffs: from the front face

BODY = ['front_body_native_1']
CAMERA = ['raspberry_pi_hq_camera_1']
CAMSCR = sel('ph_pan_head_screw_m2x0_40')
LENS = ['cs_c_m12_1', 'fisheye_lens_1']
IMU = ['hiletgo_gy_521_1']
SO_FRONT = sel('m2_5_x_4_p_4_mm')
FRONTSCR = sel('ph_pan_head_screw_m2_5')
UPS = ['waveshare_ups_hat_c_detailed_1', 'compact_waveshare_battery_body_1']
SO_FF = sel('m2_5_x_10_mm_hex_standoff_female')
PI = ['raspberry_pi_zero_2_w_1']
SO_MF = sel('m2_5_x_10_p_4_mm')
LCD = ['waveshare_1_44inch_lcd_hat_1']
LCDSCR = sel('lcd_scr')
LID = ['rear_lid_native_1']
LIDSCR = sel('lid_scr')
ALL = BODY + CAMERA + CAMSCR + LENS + IMU + SO_FRONT + FRONTSCR + UPS + SO_FF + PI + SO_MF + LCD + LCDSCR + LID + LIDSCR
assert sorted(ALL) == sorted(ITEM), set(ITEM) ^ set(ALL)


def ease(t): t = min(max(t, 0), 1); return t * t * (3 - 2 * t)
def lerp(a, b, t): return np.asarray(a, float) * (1 - t) + np.asarray(b, float) * t


# Events are offset keyframes [(t, [dx,dy,dz]), ...] applied on top of the item's pose, with an
# optional spin (radians about the optical axis x = y = 0) eased alongside. An item appears at its
# first key, fading in.
def ev(key, kf, spin=0.0): return dict(key=key, kf=[(float(t), list(map(float, o))) for t, o in kf], spin=spin)
def parts_in(keys, t0, t1, off, stagger=0.0, spin=0.0): return [ev(k, [(t0 + i * stagger, off), (t1 + i * stagger, [0, 0, 0])], spin) for i, k in enumerate(keys)]
def fast_in(keys, t0, dt=0.5, stagger=0.12, dist=18):
    return [ev(k, [(t0 + i * stagger, -ITEM[k]['dir'] * dist), (t0 + i * stagger + dt, [0, 0, 0])], spin=-4 * np.pi) for i, k in enumerate(keys)]


STEPS = []
def step(name, title, dur, base, events, cam0, cam1, camfn=None):
    STEPS.append(dict(name=name, title=title, dur=dur, base=base, events=events, cam0=cam0, cam1=cam1, camfn=camfn))
cam = lambda tgt, yaw, pitch, dist: dict(target=tgt, yaw=yaw, pitch=pitch, dist=dist, fov=30)

C = [0, 4, 18]
# 1 IMU first: it lies flat against the front wall, under the camera board
e = parts_in(BODY, 0.0, 0.6, [0, 0, -20]) + parts_in(IMU, 0.9, 2.3, [0, 0, 60])
step('01_imu', 'Lay the GY-521 IMU in its pocket by the mount', 4.0, [], e,
     cam([0, 12, 8], -68, 60, 210), cam([0, 12, 8], -82, 64, 200))
# 2 camera board, screwed to the four bosses from inside
e = parts_in(CAMERA, 0.3, 1.6, [0, 0, 70]) + fast_in(CAMSCR, 2.0, stagger=0.2)
step('02_camera', 'Screw the HQ camera into the front body (4× M2×4)', 4.5, BODY + IMU, e,
     cam(C, -62, 52, 260), cam(C, -74, 58, 250))
# 3 CS-to-M12 adapter and the fisheye lens, from the front
e = parts_in(['cs_c_m12_1'], 0.3, 1.8, [0, 0, -45], spin=3 * np.pi) + parts_in(['fisheye_lens_1'], 2.2, 3.7, [0, 0, -40], spin=4 * np.pi)
step('03_lens', 'Screw on the CS-to-M12 adapter, then the M12 fisheye lens', 5.0, BODY + IMU + CAMERA + CAMSCR, e,
     cam([0, 4, 5], -60, -30, 250), cam([0, 4, 5], -75, -38, 240))
BUILT = BODY + IMU + CAMERA + CAMSCR + LENS
# 4 short standoffs on the bosses, screws from the front face
e = parts_in(SO_FRONT, 0.2, 1.2, [0, 0, 45], stagger=0.15) + fast_in(FRONTSCR, 3.0, stagger=0.2, dist=22)
step('04_front_standoffs', 'Fix four 4 + 4 mm standoffs with M2.5×5 screws from the front', 5.5,
     BUILT, e, cam(C, -62, 52, 260), cam(C, -62, -40, 260), camfn=lambda t: ease((t - 1.6) / 1.2))
BUILT = BUILT + SO_FRONT + FRONTSCR
# 5 UPS HAT (C) with its battery
e = parts_in(UPS, 0.3, 1.8, [0, 0, 70])
step('05_ups', 'Put the UPS HAT (C) with its battery on the standoff studs', 3.5, BUILT, e,
     cam(C, -60, 45, 260), cam(C, -72, 50, 250))
# 6 10 mm female-female standoffs clamp the UPS HAT
e = parts_in(SO_FF, 0.2, 1.2, [0, 0, 40], stagger=0.2, spin=-4 * np.pi)
step('06_ups_standoffs', 'Clamp it with four 10 mm female-female standoffs', 3.5, BUILT + UPS, e,
     cam(C, -72, 50, 250), cam(C, -64, 55, 250))
BUILT = BUILT + UPS + SO_FF
# 7 Pi Zero 2 W, held by the 10 + 4 mm male-female standoffs
e = parts_in(PI, 0.3, 1.8, [0, 0, 60]) + parts_in(SO_MF, 2.3, 3.2, [0, 0, 35], stagger=0.2, spin=-4 * np.pi)
step('07_pi', 'Lay the Pi Zero 2 W on top and fix it with four 10 + 4 mm standoffs', 5.0, BUILT, e,
     cam(C, -55, 45, 260), cam(C, -68, 52, 250))
BUILT = BUILT + PI + SO_MF
# 8 LCD HAT onto the header, four screws
e = parts_in(LCD, 0.3, 1.8, [0, 0, 55]) + fast_in(LCDSCR, 2.3, stagger=0.15)
step('08_lcd', 'Plug the 1.44" LCD HAT onto the header and screw it down (4× M2.5×5)', 4.5, BUILT, e,
     cam(C, -68, 52, 250), cam(C, -58, 58, 250))
BUILT = BUILT + LCD + LCDSCR
# 9 lid, four screws into the corner posts
e = parts_in(LID, 0.3, 1.6, [0, 0, 45]) + fast_in(LIDSCR, 2.0, stagger=0.15)
step('09_lid', 'Close the rear lid (4× M2×8 into the corner posts)', 4.0, BUILT, e,
     cam(C, -58, 58, 250), cam(C, -66, 50, 260))
step('10_done', 'Done: the LUMI collector', 7.0, ALL, [],
     cam(C, -60, 40, 270), cam(C, 300, -30, 270), camfn=lambda t: t / 7.0)


def kf_offset(kf, t):
    if t <= kf[0][0]: return np.asarray(kf[0][1]), 0.0
    for (t0, o0), (t1, o1) in zip(kf, kf[1:]):
        if t <= t1: u = ease((t - t0) / max(t1 - t0, 1e-6)); return lerp(o0, o1, u), 1 - u
    return np.asarray(kf[-1][1]), 0.0


def item_parts(k, T, alpha=1.0):
    out = []
    for mesh, col in ITEM[k]['meshes']:
        d = dict(mesh=mesh, M=[float(x) for x in T.ravel()], col=list(col))
        if alpha < 1: d['alpha'] = alpha
        out.append(d)
    return out


def frames(st):
    n = int(st['dur'] * FPS); shots = []
    for i in range(n):
        t = i / FPS; u = ease(i / (n - 1)); uc = float(st['camfn'](t)) if st['camfn'] else u
        parts = []
        for k in st['base']: parts += item_parts(k, ITEM[k]['M'])
        for e in st['events']:
            k = e['key']; t0 = e['kf'][0][0]
            if t < t0: continue
            off, rem = kf_offset(e['kf'], t)
            if not e['spin']:
                T = trans(off) @ ITEM[k]['M']
            elif k in G:  # CAD group: spin about its own vertical (optical / standoff) axis
                lo, hi = np.array(G[k]['lo']), np.array(G[k]['hi'])
                T = trans(off) @ rot_z(e['spin'] * rem, (lo + hi) / 2) @ ITEM[k]['M']
            else:         # drawn screw: spin about its own shaft
                T = trans(off) @ ITEM[k]['M'] @ rot_z(e['spin'] * rem)
            fade = min(1.0, (t - t0) / 0.25)
            parts += item_parts(k, T, fade)
        c0, c1 = st['cam0'], st['cam1']
        c = dict(target=[float(x) for x in lerp(c0['target'], c1['target'], uc)], yaw=float(lerp(c0['yaw'], c1['yaw'], uc)),
                 pitch=float(lerp(c0['pitch'], c1['pitch'], uc)), dist=float(lerp(c0['dist'], c1['dist'], uc)), fov=30)
        shots.append(dict(file=f'{i:04d}.png', cam=c, parts=parts, bg=[1, 1, 1]))
    return shots


if __name__ == '__main__':
    only = sys.argv[1:]
    for st in STEPS:
        if only and st['name'] not in only: continue
        json.dump(frames(st), open(f'shots_{st["name"]}.json', 'w')); print(st['name'], int(st['dur'] * FPS))
    json.dump([dict(name=s['name'], title=s['title'], dur=s['dur']) for s in STEPS], open('steps.json', 'w'), indent=1)
