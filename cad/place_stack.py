"""Place the real Pi, UPS HAT, LCD HAT, and HQ camera in the Lumi enclosure.

The lid is the back of the enclosure (+Y). The LCD faces that lid so the
glass, three buttons, and joystick come through it. The camera barrel points
out the front face (Y = 0). Between the boards, M2.5 standoffs sit on the
Pi hole pattern (58 x 23 mm):

    lid
    LCD board bottom at Y = 59.8 mm   controls through the lid
    8.1 mm standoffs
    Pi board top at Y = 51.7 mm       header plugs into the LCD
    Pi board bottom at Y = 50.1 mm
    8.0 mm standoffs, filled by the UPS pogo pins
    UPS board top at Y = 42.1 mm

Board models are rotated so their +Z (component side) becomes enclosure +Y.
The UPS PCB origin is a corner; the Pi and LCD origins are the board center.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

DID = "108cf3a8fc7f47cafbc0fdef"
WID = "05d0c78d04a7f4c3d2ababfe"
EID = "d9dfdd8cc0f66ef5ce619250"
ASM = "1d190ff6894af08937f63a90"

# Millimetres, enclosure frame. See the module docstring.
UPS_TOP = 42.1
PI_BOTTOM = 50.1
PI_TOP = 51.7
LCD_BOTTOM = 59.8
CAM_TIP = -1.0  # lens tip, just proud of the front face
# HQ camera barrel reaches local z = 13 mm.
CAM_TY = CAM_TIP + 13.0

PI = {
    "did": "e9e648cb8f8821ad7dca28d5",
    "vid": "22e2aeac446ed03d2bb0e3d3",
    "eid": "16bf1e40d6e5722044ec32a7",
}
LCD = {
    "did": "d0f9c53ffc8e7326262e01a6",
    "vid": "b40dd2ad1bc3a5f9f52f28c2",
    "eid": "81504f5118c98ff694d50130",
}
CAM = {
    "did": "1fd4a99615d8c05af239f57c",
    "vid": "6f1d4727245b64f063a09f6f",
    "eid": "b6da445fd9c0f931ead9788f",
}
UPS = {
    "did": "6fec3efc37550ba85c320cb8",
    "vid": "e81cac976004fb4a1d234ffc",
    "eid": "08c7c31823d53658356cbcee",
}

# Placeholder solids from the first assembly pass. The shell, lid, and prongs stay.
DROP = {
    "Battery plane",
    "Battery profile",
    "Battery",
    "Pi plane",
    "Pi Zero profile",
    "Pi Zero WH",
    "Header plane",
    "Header profile",
    "GPIO header",
    "LCD plane",
    "LCD profile",
    "LCD HAT",
    "Screen plane",
    "Screen profile",
    "LCD glass",
    "Button profile",
    "Buttons",
    "Camera plane",
    "Camera board profile",
    "HQ camera",
    "CS mount profile",
    "CS mount",
    "Lens profile",
    "Lens",
}

# Assembly instances that are not the shell, the lid, or the new linked models.
RETIRE_PREFIXES = (
    "Battery",
    "Pi Zero WH",
    "1.44 LCD HAT",
    "HQ camera",
    "16mm Lens",
    "PCB <",
    "UPS HAT (C)",
    "Enclosure <2>",
)


def _load():
    path = Path(__file__).with_name("create_onshape_enclosure.py")
    spec = importlib.util.spec_from_file_location("enc", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _mm(value: float) -> str:
    return f"{value:.1f} mm"


def _m(value: float) -> float:
    return value / 1000.0


def _board_transform(ty_mm: float, tx_mm: float = 0.0, tz_mm: float = 0.0) -> list[float]:
    # Proper rotation (det +1). Model +Z, the component side, becomes enclosure +Y
    # so the LCD faces the lid. Model +Y becomes enclosure -Z; the hole pattern
    # and the button row are symmetric, so the lid cuts still meet them.
    return [
        1, 0, 0, _m(tx_mm),
        0, 0, 1, _m(ty_mm),
        0, -1, 0, _m(tz_mm),
        0, 0, 0, 1,
    ]


def _camera_transform() -> list[float]:
    # Model +Z (the barrel) -> enclosure -Y, out the front face.
    return [
        1, 0, 0, 0.0,
        0, 0, -1, _m(CAM_TY),
        0, 1, 0, 0.0,
        0, 0, 0, 1,
    ]


def _features(client):
    return client.request("GET", f"/api/v9/partstudios/d/{DID}/w/{WID}/e/{EID}/features")


def _update_bore(mod, client) -> None:
    feats = _features(client)
    micro = feats.get("sourceMicroversion")
    feature = next(f for f in feats["features"] if f.get("name") == "# lens_bore")
    for param in feature["parameters"]:
        if param.get("parameterId") == "lengthValue" and param.get("expression") == "31 mm":
            print("lens bore already 31 mm")
            return
        if param.get("parameterId") == "lengthValue":
            param["expression"] = "31 mm"
    # The HQ barrel is about 29 mm across; 31 mm lets it pass the front wall.
    body = {
        "feature": {
            "btType": "BTMFeature-134",
            "featureType": "assignVariable",
            "name": "# lens_bore",
            "parameters": feature["parameters"],
        },
        "sourceMicroversion": micro,
    }
    client.request(
        "POST",
        f"/api/v9/partstudios/d/{DID}/w/{WID}/e/{EID}/features/featureid/{feature['featureId']}",
        body,
    )
    print("lens bore -> 31 mm")


def _delete_placeholders(client) -> None:
    feats = _features(client)
    micro = feats.get("sourceMicroversion")
    ordered = [f for f in feats["features"] if f.get("name") in DROP]
    for feature in reversed(ordered):
        fid = feature["featureId"]
        client.request(
            "DELETE",
            f"/api/v9/partstudios/d/{DID}/w/{WID}/e/{EID}/features/featureid/{fid}?sourceMicroversion={micro}",
        )
        print("deleted", feature.get("name"))
        micro = _features(client).get("sourceMicroversion")


def _standoff_sketch(mod, name: str, plane_id: str):
    # 5 mm body, 2.7 mm through-hole, on the Pi Zero hole pattern.
    ents = []
    cons = []
    points = []
    dx = mod._mm("58 mm") / 2
    dy = mod._mm("23 mm") / 2
    for i, (sx, sy) in enumerate(((1, 1), (1, -1), (-1, 1), (-1, -1))):
        cx, cz = sx * dx, sy * dy
        outer, inner = f"od{i}", f"id{i}"
        ents.append(mod.circle(outer, cx, cz, 0.0025))
        ents.append(mod.circle(inner, cx, cz, 0.00135))
        cons.append(mod._diameter(f"{outer}.dia", outer, "5 mm"))
        cons.append(mod._diameter(f"{inner}.dia", inner, "2.7 mm"))
        points.append((cx + 0.002, cz))
    sketch = mod.sketch(
        name,
        ents,
        cons,
        plane_query=f'query=qCreatedBy(makeId("{plane_id}"), EntityType.FACE);',
    )
    return sketch, points


def _add_hardware(mod, client) -> None:
    feats = _features(client)
    by_name = {f.get("name"): f.get("featureId") for f in feats["features"]}
    micro = feats.get("sourceMicroversion")

    def ensure(label: str, feature: dict) -> str:
        nonlocal micro
        if label in by_name:
            print(f"  {label}  exists")
            return by_name[label]
        fid, micro = mod.add(client, DID, WID, EID, feature, micro)
        by_name[label] = fid
        return fid

    ups_plane = ensure(
        "UPS standoff plane",
        mod.offset_plane("UPS standoff plane", _mm(UPS_TOP)),
    )
    sketch, points = _standoff_sketch(mod, "UPS standoff profile", ups_plane)
    ups_sk = ensure("UPS standoff profile", sketch)
    ensure(
        "Standoffs UPS to Pi",
        mod.extrude(
            "Standoffs UPS to Pi",
            ups_sk,
            _mm(PI_BOTTOM - UPS_TOP),
            operation="NEW",
            points=points,
            opposite=True,
            plane_y_m=_m(UPS_TOP),
        ),
    )

    pi_plane = ensure(
        "LCD standoff plane",
        mod.offset_plane("LCD standoff plane", _mm(PI_TOP)),
    )
    sketch, points = _standoff_sketch(mod, "LCD standoff profile", pi_plane)
    pi_sk = ensure("LCD standoff profile", sketch)
    ensure(
        "Standoffs Pi to LCD",
        mod.extrude(
            "Standoffs Pi to LCD",
            pi_sk,
            _mm(LCD_BOTTOM - PI_TOP),
            operation="NEW",
            points=points,
            opposite=True,
            plane_y_m=_m(PI_TOP),
        ),
    )

    _replace_overlapping_buttons(client)
    feats = _features(client)
    by_name = {f.get("name"): f.get("featureId") for f in feats["features"]}
    micro = feats.get("sourceMicroversion")

    lid_plane = by_name["Lid plane"]
    # LCD glass is about x -18..11, z -14..14 once the board is centered.
    # Each button is 8 x 3.5 mm; a 9 x 5 mm slot clears it. The 5.7 mm pitch
    # would make round holes overlap and leave chips in the lid.
    win = (-0.019, -0.015, 0.013, 0.015)
    ents, cons = _rect(mod, "win", *win, "32 mm", "30 mm")
    cuts = list(ents)
    cut_cons = list(cons)
    cut_pts = [(-0.003, 0.0)]
    for i, z in enumerate((0.0, -0.0057, 0.0057)):
        slot_ents, slot_cons = _rect(
            mod, f"btn{i}", -0.02925, z - 0.0025, -0.02025, z + 0.0025, "9 mm", "5 mm"
        )
        cuts.extend(slot_ents)
        cut_cons.extend(slot_cons)
        cut_pts.append((-0.02475, z))
    cuts.append(mod.circle("joy", 0.02555, 0.0, 0.006))
    cut_cons.append(mod._diameter("joy.dia", "joy", "12 mm"))
    cut_pts.append((0.02555, 0.0))
    lid_y = mod._mm("33.6 mm") + mod._mm("28 mm")
    cut_sk = ensure(
        "Lid controls",
        mod.sketch(
            "Lid controls",
            cuts,
            cut_cons,
            plane_query=f'query=qCreatedBy(makeId("{lid_plane}"), EntityType.FACE);',
        ),
    )
    ensure(
        "Lid control openings",
        mod.extrude(
            "Lid control openings",
            cut_sk,
            "#lid_t",
            operation="REMOVE",
            points=cut_pts,
            opposite=True,
            plane_y_m=lid_y,
        ),
    )

    # The slide-switch actuator hangs off the UPS PCB's low-X edge, which lands
    # on the enclosure's -X wall. The slot is the actuator plus a finger clearance.
    asm = _load_asm()
    slot_plane = ensure(
        "Switch slot plane",
        asm.offset_from("Switch slot plane", "Right", "#body_w / 2 - #wall", True),
    )
    slot_ents, slot_cons = _rect(mod, "slot", 0.041, -0.003, 0.046, 0.003, "5 mm", "6 mm")
    slot_sk = ensure(
        "Switch slot profile",
        mod.sketch(
            "Switch slot profile",
            slot_ents,
            slot_cons,
            plane_query=f'query=qCreatedBy(makeId("{slot_plane}"), EntityType.FACE);',
        ),
    )
    ensure(
        "Switch slot",
        asm.extrude_on_right(
            "Switch slot",
            slot_sk,
            "3 mm",
            "REMOVE",
            -_m(33.5),
            [(0.0435, 0.0)],
            True,
        ),
    )


def _replace_overlapping_buttons(client) -> None:
    feats = _features(client)
    controls = next((f for f in feats["features"] if f.get("name") == "Lid controls"), None)
    if controls is None:
        return
    entity_ids = {e.get("entityId") for e in controls.get("entities") or []}
    if "btn0" not in entity_ids:
        return
    micro = feats.get("sourceMicroversion")
    for name in ("Lid control openings", "Lid controls"):
        feature = next(f for f in feats["features"] if f.get("name") == name)
        client.request(
            "DELETE",
            f"/api/v9/partstudios/d/{DID}/w/{WID}/e/{EID}/features/featureid/{feature['featureId']}?sourceMicroversion={micro}",
        )
        print("replaced", name)
        feats = _features(client)
        micro = feats.get("sourceMicroversion")


def _load_asm():
    path = Path(__file__).with_name("add_collector_assembly.py")
    spec = importlib.util.spec_from_file_location("asm", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _rect(mod, prefix, x0, y0, x1, y1, w_expr, h_expr):
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


def _instances(client) -> list[dict]:
    asm = client.request("GET", f"/api/v6/assemblies/d/{DID}/w/{WID}/e/{ASM}")
    return asm["rootAssembly"]["instances"]


def _move(client, instance_ids: list[str], transform: list[float]) -> None:
    if not instance_ids:
        return
    client.request(
        "POST",
        f"/api/v6/assemblies/d/{DID}/w/{WID}/e/{ASM}/occurrencetransforms",
        {
            "isRelative": False,
            "occurrences": [{"path": [iid]} for iid in instance_ids],
            "transform": transform,
        },
    )


def _insert(client, body: dict) -> str:
    before = {i["id"] for i in _instances(client)}
    client.request("POST", f"/api/v6/assemblies/d/{DID}/w/{WID}/e/{ASM}/instances", body)
    created = [i for i in _instances(client) if i["id"] not in before]
    if len(created) != 1:
        raise SystemExit(f"expected one new instance, got {[c.get('name') for c in created]}")
    print("  inserted", created[0].get("name"))
    return created[0]["id"]


def _try_retire(client, ids: list[str]) -> None:
    if not ids:
        return
    client.request(
        "POST",
        f"/api/v9/assemblies/d/{DID}/w/{WID}/e/{ASM}/modify",
        {"deleteInstances": ids},
    )
    print("retired", len(ids), "instances")


def _from_document(client, did: str) -> list[str]:
    return [i["id"] for i in _instances(client) if i.get("documentId") == did]


def _ensure_assembly(client, src: dict, transform: list[float]) -> None:
    if not _from_document(client, src["did"]):
        _insert(
            client,
            {
                "documentId": src["did"],
                "versionId": src["vid"],
                "elementId": src["eid"],
                "isAssembly": True,
            },
        )
    _move(client, _from_document(client, src["did"]), transform)


def _ensure_parts(client, src: dict, transform: list[float]) -> None:
    have = {i.get("partId") for i in _instances(client) if i.get("documentId") == src["did"]}
    parts = client.request("GET", f"/api/v6/parts/d/{src['did']}/v/{src['vid']}/e/{src['eid']}")
    for part in parts:
        if part["partId"] in have:
            continue
        _insert(
            client,
            {
                "documentId": src["did"],
                "versionId": src["vid"],
                "elementId": src["eid"],
                "partId": part["partId"],
                "isAssembly": False,
            },
        )
    _move(client, _from_document(client, src["did"]), transform)


def _place_assembly(client) -> None:
    rows = _instances(client)
    retire = [
        i["id"]
        for i in rows
        if any((i.get("name") or "").startswith(prefix) for prefix in RETIRE_PREFIXES)
    ]
    _try_retire(client, retire)

    _ensure_assembly(client, PI, _board_transform(PI_BOTTOM))
    # The UPS PCB origin is a corner. +15 mm in Z puts that board on the Pi center.
    _ensure_assembly(client, UPS, _board_transform(UPS_TOP - 1.6, tx_mm=-32.5, tz_mm=15.0))
    _ensure_parts(client, CAM, _camera_transform())
    _ensure_parts(client, LCD, _board_transform(LCD_BOTTOM))

    present = {i.get("partId") for i in _instances(client)}
    parts = client.request("GET", f"/api/v6/parts/d/{DID}/w/{WID}/e/{EID}")
    for part in parts:
        if part["partId"] in present:
            continue
        box = client.request(
            "GET",
            f"/api/v6/parts/d/{DID}/w/{WID}/e/{EID}/partid/{part['partId']}/boundingboxes",
        )
        span_x = box["highX"] - box["lowX"]
        # Standoffs are the short pillars between the boards. The lid is the
        # plate at the back; the button-hole chips are much smaller.
        is_standoff = 0.040 < box["lowY"] < 0.060 and span_x < 0.010
        is_lid = box["lowY"] > 0.060 and span_x > 0.050
        if not is_standoff and not is_lid:
            continue
        _insert(
            client,
            {
                "documentId": DID,
                "workspaceId": WID,
                "elementId": EID,
                "partId": part["partId"],
                "isAssembly": False,
            },
        )

    for inst in _instances(client):
        name = inst.get("name") or ""
        if name.startswith("BNO055"):
            _move(client, [inst["id"]], [1, 0, 0, _m(-13), 0, 1, 0, _m(18), 0, 0, 1, _m(-2), 0, 0, 0, 1])
            print("moved BNO055 into the gap ahead of the UPS")


def _preflight(client) -> None:
    for label, src in (("pi", PI), ("lcd", LCD), ("camera", CAM), ("ups", UPS)):
        elements = client.request("GET", f"/api/v6/documents/d/{src['did']}/v/{src['vid']}/elements")
        ids = {e.get("id") for e in elements}
        if src["eid"] not in ids:
            raise SystemExit(f"{label} version {src['vid']} has no element {src['eid']}")
        print("preflight", label, "ok")


def _report(client) -> None:
    box = client.request("GET", f"/api/v6/assemblies/d/{DID}/w/{WID}/e/{ASM}/boundingboxes")
    print(
        "assembly mm",
        {k: round(box[k] * 1000, 1) for k in ("lowX", "lowY", "lowZ", "highX", "highY", "highZ")},
    )
    print("instances:")
    for inst in _instances(client):
        print(" ", inst.get("name"))


def main() -> None:
    mod = _load()
    client = mod.Client()
    _preflight(client)
    _update_bore(mod, client)
    _delete_placeholders(client)
    _add_hardware(mod, client)
    _place_assembly(client)
    _report(client)
    print(f"https://cad.onshape.com/documents/{DID}/w/{WID}/e/{ASM}")


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        sys.exit(0)
