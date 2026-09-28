"""Add the UMI gripper buckle and a collector assembly to the enclosure document.

The gripper (UMI_gripper) carries a 3-prong female buckle. Measured on that
Part Studio: 4.0 mm pin hole, center prong 3.0 mm, gaps 3.5 mm, outer prongs
4.0 mm, prong width 15 mm. The enclosure gets the male pair the GoPro uses:
3.2 mm prongs in those gaps, 3.3 mm slot over the center prong, 14 mm wide,
4.0 mm hole.

Does not create a new document. Targets the existing Lumi collector enclosure.
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path

DID = "108cf3a8fc7f47cafbc0fdef"
WID = "05d0c78d04a7f4c3d2ababfe"
EID = "d9dfdd8cc0f66ef5ce619250"

# Clearance against the measured female: 0.15 mm per side.
MOUNT_VARS = {
    "mount_prong_t": "3.2 mm",
    "mount_gap": "3.3 mm",
    "mount_prong_w": "14 mm",
    "mount_hole_d": "4 mm",
    "mount_drop": "16 mm",
}


def _load():
    path = Path(__file__).with_name("create_onshape_enclosure.py")
    spec = importlib.util.spec_from_file_location("enc", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _mm(mod, value: str) -> float:
    return mod._mm(value)


def offset_from(name: str, plane_id: str, distance_expr: str, opposite: bool) -> dict:
    return {
        "feature": {
            "btType": "BTMFeature-134",
            "featureType": "cPlane",
            "name": name,
            "parameters": [
                {
                    "btType": "BTMParameterEnum-145",
                    "parameterId": "cplaneType",
                    "enumName": "CPlaneType",
                    "value": "OFFSET",
                },
                {
                    "btType": "BTMParameterQueryList-148",
                    "parameterId": "entities",
                    "queries": [
                        {
                            "btType": "BTMIndividualQuery-138",
                            "queryString": f'query=qCreatedBy(makeId("{plane_id}"), EntityType.FACE);',
                        }
                    ],
                },
                {
                    "btType": "BTMParameterQuantity-147",
                    "parameterId": "offset",
                    "expression": distance_expr,
                    "isInteger": False,
                },
                {
                    "btType": "BTMParameterBoolean-144",
                    "parameterId": "oppositeDirection",
                    "value": opposite,
                },
            ],
        }
    }


def prong_sketch(mod, name: str, plane_id: str) -> dict:
    """Finger outline on a Right-parallel plane. Sketch +x is world +Y, +y is world +Z."""
    body_h = _mm(mod, "50.8 mm")
    wall = _mm(mod, "2.4 mm")
    grip = _mm(mod, "33.6 mm")
    pod = _mm(mod, "28 mm")
    drop = _mm(mod, MOUNT_VARS["mount_drop"])
    width = _mm(mod, MOUNT_VARS["mount_prong_w"])
    hole_d = _mm(mod, MOUNT_VARS["mount_hole_d"])

    cy = (grip + pod) / 2
    cz = -(body_h / 2 + drop - width / 2)
    z_top = -(body_h / 2 - wall / 2)
    half = width / 2

    ents = [
        mod._line("zref", 0.0, 0.0, 0.0, cz, construction=True),
        mod._line("yref", 0.0, cz, cy, cz, construction=True),
        mod._line("halfL", cy, cz, cy - half, cz, construction=True),
        mod._line("halfR", cy, cz, cy + half, cz, construction=True),
        mod._line("top", cy - half, z_top, cy + half, z_top),
        mod._line("right", cy + half, cz, cy + half, z_top),
        mod._line("left", cy - half, z_top, cy - half, cz),
        # Lower semicircle. Param 0 is +sketch x; -pi .. 0 passes through the tip.
        {
            "btType": "BTMSketchCurveSegment-155",
            "entityId": "tip",
            "startPointId": "tip.start",
            "endPointId": "tip.end",
            "startParam": -math.pi,
            "endParam": 0.0,
            "isConstruction": False,
            "geometry": {
                "btType": "BTCurveGeometryCircle-115",
                "radius": half,
                "xCenter": cy,
                "yCenter": cz,
                "xDir": 1.0,
                "yDir": 0.0,
                "clockwise": False,
            },
        },
        mod.circle("tipc", cy, cz, half, construction=True),
        mod.circle("hole", cy, cz, hole_d / 2),
    ]
    cons = [
        mod._constraint("zref.v", "VERTICAL", [mod._ref("localFirst", "zref")]),
        mod._length("zref.len", "zref", "#body_h / 2 + #mount_drop - #mount_prong_w / 2"),
        mod._constraint("yref.h", "HORIZONTAL", [mod._ref("localFirst", "yref")]),
        mod._length("yref.len", "yref", "(#grip_d + #pod_d) / 2"),
        mod._constraint(
            "yref.c",
            "COINCIDENT",
            [mod._ref("localFirst", "zref.end"), mod._ref("localSecond", "yref.start")],
        ),
        mod._constraint("halfL.h", "HORIZONTAL", [mod._ref("localFirst", "halfL")]),
        mod._length("halfL.len", "halfL", "#mount_prong_w / 2"),
        mod._constraint("halfR.h", "HORIZONTAL", [mod._ref("localFirst", "halfR")]),
        mod._length("halfR.len", "halfR", "#mount_prong_w / 2"),
        mod._constraint(
            "halfL.c",
            "COINCIDENT",
            [mod._ref("localFirst", "yref.end"), mod._ref("localSecond", "halfL.start")],
        ),
        mod._constraint(
            "halfR.c",
            "COINCIDENT",
            [mod._ref("localFirst", "yref.end"), mod._ref("localSecond", "halfR.start")],
        ),
        mod._constraint("top.h", "HORIZONTAL", [mod._ref("localFirst", "top")]),
        mod._length("top.len", "top", "#mount_prong_w"),
        mod._constraint("right.v", "VERTICAL", [mod._ref("localFirst", "right")]),
        mod._length("right.len", "right", "#wall / 2 + #mount_drop - #mount_prong_w / 2"),
        mod._constraint("left.v", "VERTICAL", [mod._ref("localFirst", "left")]),
        mod._constraint(
            "top.cr",
            "COINCIDENT",
            [mod._ref("localFirst", "top.end"), mod._ref("localSecond", "right.end")],
        ),
        mod._constraint(
            "top.cl",
            "COINCIDENT",
            [mod._ref("localFirst", "top.start"), mod._ref("localSecond", "left.start")],
        ),
        mod._constraint(
            "tip.cl",
            "COINCIDENT",
            [mod._ref("localFirst", "tip.start"), mod._ref("localSecond", "left.end")],
        ),
        mod._constraint(
            "tip.cr",
            "COINCIDENT",
            [mod._ref("localFirst", "tip.end"), mod._ref("localSecond", "right.start")],
        ),
        mod._constraint(
            "tip.halfL",
            "COINCIDENT",
            [mod._ref("localFirst", "tip.start"), mod._ref("localSecond", "halfL.end")],
        ),
        mod._constraint(
            "tip.halfR",
            "COINCIDENT",
            [mod._ref("localFirst", "tip.end"), mod._ref("localSecond", "halfR.end")],
        ),
        mod._constraint(
            "tipc.c",
            "COINCIDENT",
            [mod._ref("localFirst", "yref.end"), mod._ref("localSecond", "tipc.center")],
        ),
        mod._diameter("tipc.dia", "tipc", "#mount_prong_w"),
        mod._constraint(
            "hole.c",
            "COINCIDENT",
            [mod._ref("localFirst", "yref.end"), mod._ref("localSecond", "hole.center")],
        ),
        mod._diameter("hole.dia", "hole", "#mount_hole_d"),
    ]
    return mod.sketch(
        name,
        ents,
        cons,
        plane_query=f'query=qCreatedBy(makeId("{plane_id}"), EntityType.FACE);',
    )


def extrude_on_right(name: str, sketch_id: str, depth_expr: str, operation: str, plane_x: float, points: list[tuple[float, float]], opposite: bool) -> dict:
    """points are (world Y, world Z) and must sit in a sketch region."""
    queries = []
    for y, z in points:
        queries.append(
            {
                "btType": "BTMIndividualQuery-138",
                "queryString": (
                    "query=qContainsPoint(qSketchRegion(makeId(%r), false), vector(%r, %r, %r)*meter);"
                    % (sketch_id, plane_x, y, z)
                ),
            }
        )
    return {
        "feature": {
            "btType": "BTMFeature-134",
            "featureType": "extrude",
            "name": name,
            "parameters": [
                {
                    "btType": "BTMParameterEnum-145",
                    "parameterId": "bodyType",
                    "enumName": "ExtendedToolBodyType",
                    "value": "SOLID",
                },
                {
                    "btType": "BTMParameterEnum-145",
                    "parameterId": "operationType",
                    "enumName": "NewBodyOperationType",
                    "value": operation,
                },
                {
                    "btType": "BTMParameterQueryList-148",
                    "parameterId": "entities",
                    "queries": queries,
                },
                {
                    "btType": "BTMParameterEnum-145",
                    "parameterId": "endBound",
                    "enumName": "BoundingType",
                    "value": "BLIND",
                },
                {
                    "btType": "BTMParameterQuantity-147",
                    "parameterId": "depth",
                    "expression": depth_expr,
                    "isInteger": False,
                },
                {
                    "btType": "BTMParameterBoolean-144",
                    "parameterId": "oppositeDirection",
                    "value": opposite,
                },
            ],
        }
    }


def rect_xy(mod, prefix: str, x0: float, y0: float, x1: float, y1: float, w_expr: str, h_expr: str) -> tuple[list, list]:
    bottom, right, top, left = (f"{prefix}.{side}" for side in ("b", "r", "t", "l"))
    ents = [
        mod._line(bottom, x0, y0, x1, y0),
        mod._line(right, x1, y0, x1, y1),
        mod._line(top, x1, y1, x0, y1),
        mod._line(left, x0, y1, x0, y0),
    ]
    cons = [
        mod._constraint(f"{prefix}.h", "HORIZONTAL", [mod._ref("localFirst", bottom)]),
        mod._constraint(f"{prefix}.p", "PERPENDICULAR", [mod._ref("localFirst", bottom), mod._ref("localSecond", right)]),
        mod._constraint(f"{prefix}.par", "PARALLEL", [mod._ref("localFirst", bottom), mod._ref("localSecond", top)]),
        mod._constraint(f"{prefix}.par2", "PARALLEL", [mod._ref("localFirst", right), mod._ref("localSecond", left)]),
        mod._constraint(f"{prefix}.c0", "COINCIDENT", [mod._ref("localFirst", f"{bottom}.end"), mod._ref("localSecond", f"{right}.start")]),
        mod._constraint(f"{prefix}.c1", "COINCIDENT", [mod._ref("localFirst", f"{right}.end"), mod._ref("localSecond", f"{top}.start")]),
        mod._constraint(f"{prefix}.c2", "COINCIDENT", [mod._ref("localFirst", f"{top}.end"), mod._ref("localSecond", f"{left}.start")]),
        mod._constraint(f"{prefix}.c3", "COINCIDENT", [mod._ref("localFirst", f"{left}.end"), mod._ref("localSecond", f"{bottom}.start")]),
        mod._length(f"{prefix}.w", bottom, w_expr),
        mod._length(f"{prefix}.h", right, h_expr),
    ]
    return ents, cons


def extrude_top(name, sketch_id, depth_expr, operation, plane_z, points, opposite=False):
    queries = [
        {
            "btType": "BTMIndividualQuery-138",
            "queryString": (
                "query=qContainsPoint(qSketchRegion(makeId(%r), false), vector(%r, %r, %r)*meter);"
                % (sketch_id, x, y, plane_z)
            ),
        }
        for x, y in points
    ]
    return {
        "feature": {
            "btType": "BTMFeature-134",
            "featureType": "extrude",
            "name": name,
            "parameters": [
                {"btType": "BTMParameterEnum-145", "parameterId": "bodyType", "enumName": "ExtendedToolBodyType", "value": "SOLID"},
                {"btType": "BTMParameterEnum-145", "parameterId": "operationType", "enumName": "NewBodyOperationType", "value": operation},
                {"btType": "BTMParameterQueryList-148", "parameterId": "entities", "queries": queries},
                {"btType": "BTMParameterEnum-145", "parameterId": "endBound", "enumName": "BoundingType", "value": "BLIND"},
                {"btType": "BTMParameterQuantity-147", "parameterId": "depth", "expression": depth_expr, "isInteger": False},
                {"btType": "BTMParameterBoolean-144", "parameterId": "oppositeDirection", "value": opposite},
            ],
        }
    }


BOARD_VARS = {
    "pi_l": "65 mm",
    "pi_w": "30 mm",
    "pi_t": "1.2 mm",
    "lcd_l": "65 mm",
    "lcd_w": "30.2 mm",
    "lcd_t": "1.6 mm",
    "screen": "26 mm",
    "batt_l": "40 mm",
    "batt_w": "30 mm",
    "batt_t": "8 mm",
    "lens_od": "14 mm",
    "lens_len": "20 mm",
    "cam_t": "1.6 mm",
}


def add_boards(mod, client) -> None:
    """Datasheet envelopes for the parts that have no vendor solid.

    Placed in the enclosure frame: lens out the front bore, and behind it the
    stack battery, Pi Zero WH, then the 1.44 inch LCD HAT.
    """
    feats = client.request("GET", f"/api/v9/partstudios/d/{DID}/w/{WID}/e/{EID}/features")
    by_name = {f.get("name"): f.get("featureId") for f in feats["features"]}
    if "Battery" in by_name and "Lens" in by_name:
        print("boards already present")
        return
    micro = feats.get("sourceMicroversion")

    def ensure(label: str, feature: dict) -> str:
        nonlocal micro
        if label in by_name:
            print(f"  {label}  exists")
            return by_name[label]
        fid, micro = mod.add(client, DID, WID, EID, feature, micro)
        by_name[label] = fid
        return fid

    for name, expr in BOARD_VARS.items():
        ensure(f"# {name}", mod.variable(name, expr))

    # Stack sits behind the camera, centered on x, y from 28 to 58 mm.
    x0, x1 = -0.0325, 0.0325
    y0, y1 = 0.028, 0.058
    batt_x0, batt_x1 = -0.020, 0.020

    batt_plane = ensure("Battery plane", offset_from("Battery plane", "Top", "23 mm", True))
    ents, cons = rect_xy(mod, "batt", batt_x0, y0, batt_x1, y1, "#batt_l", "#batt_w")
    batt_sk = ensure(
        "Battery profile",
        mod.sketch("Battery profile", ents, cons, plane_query=f'query=qCreatedBy(makeId("{batt_plane}"), EntityType.FACE);'),
    )
    ensure("Battery", extrude_top("Battery", batt_sk, "#batt_t", "NEW", -0.023, [(0.0, 0.043)]))

    pi_plane = ensure("Pi plane", offset_from("Pi plane", "Top", "10 mm", True))
    ents, cons = rect_xy(mod, "pi", x0, y0, x1, y1, "#pi_l", "#pi_w")
    pi_sk = ensure(
        "Pi Zero profile",
        mod.sketch("Pi Zero profile", ents, cons, plane_query=f'query=qCreatedBy(makeId("{pi_plane}"), EntityType.FACE);'),
    )
    ensure("Pi Zero WH", extrude_top("Pi Zero WH", pi_sk, "#pi_t", "NEW", -0.010, [(0.0, 0.043)]))

    hdr_plane = ensure("Header plane", offset_from("Header plane", "Top", "8.8 mm", True))
    ents, cons = rect_xy(mod, "hdr", -0.0254, 0.053, 0.0254, 0.058, "50.8 mm", "5 mm")
    hdr_sk = ensure(
        "Header profile",
        mod.sketch("Header profile", ents, cons, plane_query=f'query=qCreatedBy(makeId("{hdr_plane}"), EntityType.FACE);'),
    )
    ensure("GPIO header", extrude_top("GPIO header", hdr_sk, "8.5 mm", "ADD", -0.0088, [(0.0, 0.0555)]))

    lcd_plane = ensure("LCD plane", offset_from("LCD plane", "Top", "0.3 mm", True))
    # 30.2 mm wide, same 30 mm bay grown by 0.1 mm each side.
    ents, cons = rect_xy(mod, "lcd", x0, 0.0279, x1, 0.0581, "#lcd_l", "#lcd_w")
    lcd_sk = ensure(
        "LCD profile",
        mod.sketch("LCD profile", ents, cons, plane_query=f'query=qCreatedBy(makeId("{lcd_plane}"), EntityType.FACE);'),
    )
    ensure("LCD HAT", extrude_top("LCD HAT", lcd_sk, "#lcd_t", "NEW", -0.0003, [(0.0, 0.043)]))

    scr_plane = ensure("Screen plane", offset_from("Screen plane", "Top", "1.3 mm", False))
    ents, cons = rect_xy(mod, "scr", -0.025, 0.030, 0.001, 0.056, "#screen", "#screen")
    scr_sk = ensure(
        "Screen profile",
        mod.sketch("Screen profile", ents, cons, plane_query=f'query=qCreatedBy(makeId("{scr_plane}"), EntityType.FACE);'),
    )
    ensure("LCD glass", extrude_top("LCD glass", scr_sk, "2.2 mm", "ADD", 0.0013, [(-0.012, 0.043)]))

    btn_plane = scr_plane
    buttons = []
    btn_cons = []
    btn_pts = []
    for i, (bx, by) in enumerate(((0.018, 0.034), (0.018, 0.043), (0.018, 0.052), (0.026, 0.043))):
        radius = 0.003 if i == 3 else 0.002
        buttons.append(mod.circle(f"btn{i}", bx, by, radius))
        expr = "6 mm" if i == 3 else "4 mm"
        btn_cons.append(mod._diameter(f"btn{i}.dia", f"btn{i}", expr))
        btn_pts.append((bx, by))
    btn_sk = ensure(
        "Button profile",
        mod.sketch("Button profile", buttons, btn_cons, plane_query=f'query=qCreatedBy(makeId("{btn_plane}"), EntityType.FACE);'),
    )
    ensure("Buttons", extrude_top("Buttons", btn_sk, "3 mm", "ADD", 0.0013, btn_pts))

    cam_plane = ensure("Camera plane", offset_from("Camera plane", "Front", "18 mm", True))
    ents, cons = mod.centered_rect("camb", 0.038, 0.038, "#cam_board", "#cam_board")
    cam_sk = ensure(
        "Camera board profile",
        mod.sketch("Camera board profile", ents, cons, plane_query=f'query=qCreatedBy(makeId("{cam_plane}"), EntityType.FACE);'),
    )
    ensure(
        "HQ camera",
        mod.extrude("HQ camera", cam_sk, "#cam_t", operation="NEW", points=[(0.0, 0.0)], opposite=True, plane_y_m=0.018),
    )
    barrel = [mod.circle("barrel", 0.0, 0.0, 0.010)]
    barrel_sk = ensure(
        "CS mount profile",
        mod.sketch(
            "CS mount profile",
            barrel,
            [mod._diameter("barrel.dia", "barrel", "20 mm")],
            plane_query=f'query=qCreatedBy(makeId("{cam_plane}"), EntityType.FACE);',
        ),
    )
    ensure(
        "CS mount",
        mod.extrude("CS mount", barrel_sk, "8 mm", operation="ADD", points=[(0.0, 0.0)], opposite=False, plane_y_m=0.018),
    )
    lens = [mod.circle("lensb", 0.0, 0.0, 0.007)]
    lens_sk = ensure(
        "Lens profile",
        mod.sketch(
            "Lens profile",
            lens,
            [mod._diameter("lensb.dia", "lensb", "#lens_od")],
            plane_query=f'query=qCreatedBy(makeId("{cam_plane}"), EntityType.FACE);',
        ),
    )
    ensure(
        "Lens",
        mod.extrude("Lens", lens_sk, "#lens_len", operation="ADD", points=[(0.0, 0.0)], opposite=False, plane_y_m=0.018),
    )


def _row_major(tx: float, ty: float, tz: float) -> list[float]:
    # Onshape occurrence transforms are row-major; translation is indices 3, 7, 11.
    return [
        1, 0, 0, tx,
        0, 1, 0, ty,
        0, 0, 1, tz,
        0, 0, 0, 1,
    ]


def build_assembly(mod, client) -> None:
    asm = "1d190ff6894af08937f63a90"
    existing = client.request("GET", f"/api/v6/assemblies/d/{DID}/w/{WID}/e/{asm}")
    already = existing.get("rootAssembly", {}).get("instances") or []
    if already:
        print(f"assembly already has {len(already)} instances")
        return
    have = set()
    parts = client.request("GET", f"/api/v6/parts/d/{DID}/w/{WID}/e/{EID}")
    # The parts list is either a list or {"parts": [...]}
    rows = parts if isinstance(parts, list) else parts.get("parts") or parts.get("value") or []
    print("parts", [(r.get("name"), r.get("partId")) for r in rows])

    def instance_part(part_id: str):
        return client.request(
            "POST",
            f"/api/v6/assemblies/d/{DID}/w/{WID}/e/{asm}/instances",
            {
                "documentId": DID,
                "workspaceId": WID,
                "elementId": EID,
                "partId": part_id,
                "isAssembly": False,
            },
        )

    def instance_assembly(element_id: str):
        return client.request(
            "POST",
            f"/api/v6/assemblies/d/{DID}/w/{WID}/e/{asm}/instances",
            {
                "documentId": DID,
                "workspaceId": WID,
                "elementId": element_id,
                "isAssembly": True,
            },
        )

    def move(instance_id: str, tx: float, ty: float, tz: float):
        client.request(
            "POST",
            f"/api/v6/assemblies/d/{DID}/w/{WID}/e/{asm}/occurrencetransforms",
            {
                "isRelative": False,
                "occurrences": [{"path": [instance_id]}],
                "transform": _row_major(tx, ty, tz),
            },
        )

    # Enclosure parts are already in the studio frame. Imported boards are not.
    if "UPS HAT (C)" not in have:
        created = instance_assembly("51b466a7578fa550e54c887f")
        iid = created.get("id") or (created.get("instances") or [{}])[-1].get("id")
        print("ups instance", created.get("id") or created.get("name"), list(created)[:8])
        if iid:
            move(iid, -0.0325, 0.028, -0.015)
    if "BNO055" not in have:
        created = instance_assembly("ba58406c4f3c1d7e585931cb")
        iid = created.get("id")
        print("bno instance", iid, created.get("name"))
        if iid:
            move(iid, -0.013, 0.033, 0.004)

    for row in rows:
        name = row.get("name") or ""
        if name in have:
            continue
        created = instance_part(row["partId"])
        print(" part", name, created.get("name"), created.get("id"))

    # Gripper top cover, shifted so its 4 mm pin lands on the enclosure prongs.
    if "top_cover" not in have and "UMI gripper" not in have:
        try:
            created = client.request(
                "POST",
                f"/api/v6/assemblies/d/{DID}/w/{WID}/e/{asm}/instances",
                {
                    "documentId": "438a442b3ca052443a0741f8",
                    "workspaceId": "7f2f9161980c77f6f1a0b09e",
                    "elementId": "a0def26668a61811c93726e4",
                    "partId": "RPED",
                    "isAssembly": False,
                },
            )
            iid = created.get("id")
            print("gripper", created.get("name"), iid)
            if iid:
                move(iid, -0.01975, 0.0368, -0.0774)
        except SystemExit as exc:
            print("gripper insert skipped", str(exc)[:300])


def main() -> None:
    mod = _load()
    client = mod.Client()
    feats = client.request("GET", f"/api/v9/partstudios/d/{DID}/w/{WID}/e/{EID}/features")
    by_name = {f.get("name"): f.get("featureId") for f in feats["features"]}
    micro = feats.get("sourceMicroversion")

    def ensure(label: str, feature: dict) -> str:
        nonlocal micro
        if label in by_name:
            print(f"  {label}  {by_name[label]}  exists")
            return by_name[label]
        fid, micro = mod.add(client, DID, WID, EID, feature, micro)
        by_name[label] = fid
        return fid

    for name, expr in MOUNT_VARS.items():
        ensure(f"# {name}", mod.variable(name, expr))

    right_plane = ensure("Right prong plane", offset_from("Right prong plane", "Right", "#mount_gap / 2", False))
    left_plane = ensure("Left prong plane", offset_from("Left prong plane", "Right", "#mount_gap / 2", True))

    body_h = _mm(mod, "50.8 mm")
    wall = _mm(mod, "2.4 mm")
    grip = _mm(mod, "33.6 mm")
    pod = _mm(mod, "28 mm")
    drop = _mm(mod, MOUNT_VARS["mount_drop"])
    width = _mm(mod, MOUNT_VARS["mount_prong_w"])
    cy = (grip + pod) / 2
    cz = -(body_h / 2 + drop - width / 2)
    z_top = -(body_h / 2 - wall / 2)
    # Above the tip circle, inside the shank. Below the hole center, inside the round end.
    shank = (cy, (z_top + (cz + width / 2)) / 2)
    cap = (cy, cz - width / 4)
    gap = _mm(mod, MOUNT_VARS["mount_gap"])
    right_x = gap / 2
    left_x = -gap / 2

    right_sk = ensure("Right prong profile", prong_sketch(mod, "Right prong profile", right_plane))
    ensure(
        "Right prong",
        extrude_on_right("Right prong", right_sk, "#mount_prong_t", "ADD", right_x, [shank, cap], False),
    )
    left_sk = ensure("Left prong profile", prong_sketch(mod, "Left prong profile", left_plane))
    ensure(
        "Left prong",
        extrude_on_right("Left prong", left_sk, "#mount_prong_t", "ADD", left_x, [shank, cap], True),
    )
    print("mount ok", micro)
    add_boards(mod, client)
    build_assembly(mod, client)


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        sys.exit(0)
