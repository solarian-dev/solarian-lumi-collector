"""Create the LUMI collector enclosure as native Onshape sketches.

The Part Studio is sketches + extrudes, not an imported solid. Open any sketch
in the feature tree and edit it. Key sizes are Onshape variables; the sketch
dimensions reference them, so changing a variable rebuilds the part.

The front profile is the GoPro Hero 10 Black body (71.8 x 50.8 mm, 33.6 mm
deep). That is the section the existing UMI holder clamps. A pod continues
behind that section for the Pi Zero 2 W stack.

Credentials: ONSHAPE_ACCESS_WRITE_KEY / ONSHAPE_SECRET_WRITE_KEY, or
onshape_keys.txt walked up from this file. The read-only pair is not used.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = "https://cad.onshape.com"

# Hero 10 Black body, the section the UMI holder was built to clamp.
# https://gopro.com — 71.8 x 50.8 x 33.6 mm without the mounting fingers.
VARIABLES = {
    "body_w": "71.8 mm",
    "body_h": "50.8 mm",
    "grip_d": "33.6 mm",
    "pod_d": "28 mm",
    "wall": "2.4 mm",
    "front_wall": "2.4 mm",
    "lid_t": "2.4 mm",
    "lens_bore": "26 mm",
    "pi_hole_dx": "58 mm",
    "pi_hole_dy": "23 mm",
    "pi_hole_d": "2.75 mm",
    "display_window": "24 mm",
    "cam_board": "38 mm",
}


def _load_keys() -> tuple[str, str]:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "onshape_keys.txt"
        if not candidate.is_file():
            continue
        for raw in candidate.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
        break
    access = os.environ.get("ONSHAPE_ACCESS_WRITE_KEY")
    secret = os.environ.get("ONSHAPE_SECRET_WRITE_KEY")
    if not access or not secret:
        raise SystemExit(
            "Set ONSHAPE_ACCESS_WRITE_KEY and ONSHAPE_SECRET_WRITE_KEY, "
            "or put them in onshape_keys.txt above this file."
        )
    return access, secret


class Client:
    def __init__(self) -> None:
        access, secret = _load_keys()
        token = base64.b64encode(f"{access}:{secret}".encode()).decode()
        self._auth = f"Basic {token}"

    def request(self, method: str, path: str, body: dict | None = None) -> dict:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(
            API + path,
            data=data,
            method=method,
            headers={
                "Authorization": self._auth,
                "Accept": "application/json;charset=UTF-8; qs=0.09",
                "Content-Type": "application/json;charset=UTF-8; qs=0.09",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = resp.read().decode()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:2000]
            raise SystemExit(f"{method} {path} -> {exc.code}\n{detail}") from exc


def _mm(value: str) -> float:
    number, unit = value.split()
    scale = {"mm": 0.001, "m": 1.0}[unit]
    return float(number) * scale


def variable(name: str, expression: str) -> dict:
    return {
        "feature": {
            "btType": "BTMFeature-134",
            "featureType": "assignVariable",
            "name": f"# {name}",
            "parameters": [
                {
                    "btType": "BTMParameterEnum-145",
                    "parameterId": "mode",
                    "enumName": "VariableMode",
                    "value": "ASSIGNED",
                },
                {
                    "btType": "BTMParameterEnum-145",
                    "parameterId": "variableType",
                    "enumName": "VariableType",
                    "value": "LENGTH",
                },
                {
                    "btType": "BTMParameterString-149",
                    "parameterId": "name",
                    "value": name,
                },
                {
                    "btType": "BTMParameterQuantity-147",
                    "parameterId": "lengthValue",
                    "expression": expression,
                    "isInteger": False,
                },
            ],
        }
    }


def _line(entity_id: str, x0: float, y0: float, x1: float, y1: float, construction: bool = False) -> dict:
    dx, dy = x1 - x0, y1 - y0
    length = (dx * dx + dy * dy) ** 0.5
    if length == 0:
        raise ValueError(entity_id)
    return {
        "btType": "BTMSketchCurveSegment-155",
        "entityId": entity_id,
        "startPointId": f"{entity_id}.start",
        "endPointId": f"{entity_id}.end",
        "startParam": 0.0,
        "endParam": length,
        "isConstruction": construction,
        "geometry": {
            "btType": "BTCurveGeometryLine-117",
            "pntX": x0,
            "pntY": y0,
            "dirX": dx / length,
            "dirY": dy / length,
        },
    }


def _ref(parameter_id: str, entity_id: str) -> dict:
    return {
        "btType": "BTMParameterString-149",
        "parameterId": parameter_id,
        "value": entity_id,
    }


def _constraint(entity_id: str, kind: str, params: list[dict]) -> dict:
    return {
        "btType": "BTMSketchConstraint-2",
        "constraintType": kind,
        "entityId": entity_id,
        "parameters": params,
    }


def _length(entity_id: str, line_id: str, expression: str) -> dict:
    return _constraint(
        entity_id,
        "LENGTH",
        [
            _ref("localFirst", line_id),
            {
                "btType": "BTMParameterEnum-145",
                "parameterId": "direction",
                "enumName": "DimensionDirection",
                "value": "MINIMUM",
            },
            {
                "btType": "BTMParameterQuantity-147",
                "parameterId": "length",
                "expression": expression,
                "isInteger": False,
            },
            {
                "btType": "BTMParameterEnum-145",
                "parameterId": "alignment",
                "enumName": "DimensionAlignment",
                "value": "ALIGNED",
            },
        ],
    )


def centered_rect(prefix: str, width_m: float, height_m: float, width_expr: str, height_expr: str, construction: bool = False) -> tuple[list[dict], list[dict]]:
    """Rectangle centered on the sketch origin. +x right, +y up."""
    x0, x1 = -width_m / 2, width_m / 2
    y0, y1 = -height_m / 2, height_m / 2
    bottom, right, top, left = (f"{prefix}.{side}" for side in ("bottom", "right", "top", "left"))
    entities = [
        _line(bottom, x0, y0, x1, y0, construction),
        _line(right, x1, y0, x1, y1, construction),
        _line(top, x1, y1, x0, y1, construction),
        _line(left, x0, y1, x0, y0, construction),
    ]
    constraints = [
        _constraint(f"{prefix}.horizontal", "HORIZONTAL", [_ref("localFirst", bottom)]),
        _constraint(f"{prefix}.perp", "PERPENDICULAR", [_ref("localFirst", bottom), _ref("localSecond", left)]),
        _constraint(f"{prefix}.par1", "PARALLEL", [_ref("localFirst", bottom), _ref("localSecond", top)]),
        _constraint(f"{prefix}.par2", "PARALLEL", [_ref("localFirst", left), _ref("localSecond", right)]),
        _constraint(f"{prefix}.c0", "COINCIDENT", [_ref("localFirst", f"{bottom}.start"), _ref("localSecond", f"{left}.end")]),
        _constraint(f"{prefix}.c1", "COINCIDENT", [_ref("localFirst", f"{bottom}.end"), _ref("localSecond", f"{right}.start")]),
        _constraint(f"{prefix}.c2", "COINCIDENT", [_ref("localFirst", f"{top}.start"), _ref("localSecond", f"{right}.end")]),
        _constraint(f"{prefix}.c3", "COINCIDENT", [_ref("localFirst", f"{top}.end"), _ref("localSecond", f"{left}.start")]),
        _length(f"{prefix}.width", bottom, width_expr),
        _length(f"{prefix}.height", right, height_expr),
    ]
    return entities, constraints


def circle(entity_id: str, x: float, y: float, radius: float, construction: bool = False) -> dict:
    return {
        "btType": "BTMSketchCurve-4",
        "entityId": entity_id,
        "centerId": f"{entity_id}.center",
        "isConstruction": construction,
        "geometry": {
            "btType": "BTCurveGeometryCircle-115",
            "radius": radius,
            "xCenter": x,
            "yCenter": y,
            "xDir": 1.0,
            "yDir": 0.0,
            "clockwise": False,
        },
    }


def _diameter(entity_id: str, circle_id: str, expression: str) -> dict:
    return _constraint(
        entity_id,
        "DIAMETER",
        [
            _ref("localFirst", circle_id),
            {
                "btType": "BTMParameterQuantity-147",
                "parameterId": "length",
                "expression": expression,
                "isInteger": False,
            },
        ],
    )


def sketch(name: str, entities: list[dict], constraints: list[dict], plane_query: str | None = None) -> dict:
    query = plane_query or 'query=qCreatedBy(makeId("Front"), EntityType.FACE);'
    return {
        "feature": {
            "btType": "BTMSketch-151",
            "featureType": "newSketch",
            "name": name,
            "suppressed": False,
            "parameters": [
                {
                    "btType": "BTMParameterQueryList-148",
                    "parameterId": "sketchPlane",
                    "queries": [
                        {
                            "btType": "BTMIndividualQuery-138",
                            "queryString": query,
                        }
                    ],
                }
            ],
            "entities": entities,
            "constraints": constraints,
        }
    }


def offset_plane(name: str, distance_expr: str) -> dict:
    """Plane offset from Front, in the same direction as the shell extrude."""
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
                            "queryString": 'query=qCreatedBy(makeId("Front"), EntityType.FACE);',
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
                    "value": True,
                },
            ],
        }
    }


def _region_query(sketch_id: str, x_m: float, z_m: float, y_m: float = 0.0) -> dict:
    # Front plane and planes offset from it: sketch +x is world +x, sketch +y is world +z.
    return {
        "btType": "BTMIndividualQuery-138",
        "queryString": (
            "query=qContainsPoint(qSketchRegion(makeId(%r), false), vector(%r, %r, %r)*meter);"
            % (sketch_id, x_m, y_m, z_m)
        ),
    }


def extrude(
    name: str,
    sketch_id: str,
    depth_expr: str,
    *,
    operation: str,
    points: list[tuple[float, float]],
    opposite: bool = True,
    plane_y_m: float = 0.0,
) -> dict:
    params: list[dict] = [
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
            "queries": [_region_query(sketch_id, x, z, plane_y_m) for x, z in points],
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
    ]
    return {
        "feature": {
            "btType": "BTMFeature-134",
            "featureType": "extrude",
            "name": name,
            "parameters": params,
        }
    }


def add(client: Client, did: str, wid: str, eid: str, feature: dict, microversion: str | None) -> tuple[str, str | None]:
    body = dict(feature)
    if microversion:
        body["sourceMicroversion"] = microversion
    result = client.request(
        "POST",
        f"/api/v9/partstudios/d/{did}/w/{wid}/e/{eid}/features",
        body,
    )
    feature_state = result.get("featureState") or {}
    status = feature_state.get("featureStatus")
    feat = result.get("feature") or {}
    fid = feat.get("featureId") or result.get("featureId")
    name = feat.get("name") or feature["feature"].get("name")
    if status not in (None, "OK", "WARNING"):
        raise SystemExit(f"Feature {name} status {status}: {json.dumps(feature_state)[:1500]}")
    print(f"  {name}  {fid}  {status or 'ok'}")
    return fid, result.get("sourceMicroversion") or microversion


def main() -> None:
    client = Client()
    created = client.request(
        "POST",
        "/api/v6/documents",
        {
            "name": "LUMI collector enclosure",
            "description": (
                "Drop-in replacement for the GoPro Hero 10 Black in the UMI holder. "
                "Front profile is the Hero 10 body. Edit the sketches, or the variables "
                "they are dimensioned to."
            ),
            "isPublic": False,
        },
    )
    did = created["id"]
    wid = created["defaultWorkspace"]["id"]
    elements = client.request("GET", f"/api/v6/documents/d/{did}/w/{wid}/elements")
    part = next(e for e in elements if e.get("elementType") == "PARTSTUDIO")
    eid = part["id"]
    print(f"document {did}")
    print(f"part studio {part.get('name')} {eid}")

    micro = None
    for name, expr in VARIABLES.items():
        _, micro = add(client, did, wid, eid, variable(name, expr), micro)

    w = _mm(VARIABLES["body_w"])
    h = _mm(VARIABLES["body_h"])
    wall = _mm(VARIABLES["wall"])
    inner_w, inner_h = w - 2 * wall, h - 2 * wall
    lens_r = _mm(VARIABLES["lens_bore"]) / 2
    hole_r = _mm(VARIABLES["pi_hole_d"]) / 2
    hole_dx = _mm(VARIABLES["pi_hole_dx"]) / 2
    hole_dy = _mm(VARIABLES["pi_hole_dy"]) / 2
    window = _mm(VARIABLES["display_window"])
    cam = _mm(VARIABLES["cam_board"])

    shell_ents, shell_cons = centered_rect("shell", w, h, "#body_w", "#body_h")
    opening_ents, opening_cons = centered_rect(
        "opening", inner_w, inner_h, "#body_w - 2 * #wall", "#body_h - 2 * #wall"
    )
    shell_id, micro = add(
        client,
        did,
        wid,
        eid,
        sketch("GoPro profile", shell_ents + opening_ents, shell_cons + opening_cons),
        micro,
    )
    # Point sits in the wall, midway between the inner and outer left edges.
    wall_point = (-(w / 2 - wall / 2), 0.0)
    _, micro = add(
        client,
        did,
        wid,
        eid,
        extrude(
            "Shell",
            shell_id,
            "#grip_d + #pod_d",
            operation="NEW",
            points=[wall_point],
        ),
        micro,
    )

    cap_ents, cap_cons = centered_rect(
        "cap", inner_w, inner_h, "#body_w - 2 * #wall", "#body_h - 2 * #wall"
    )
    cap_id, micro = add(client, did, wid, eid, sketch("Front wall", cap_ents, cap_cons), micro)
    _, micro = add(
        client,
        did,
        wid,
        eid,
        extrude("Front wall", cap_id, "#front_wall", operation="ADD", points=[(0.0, 0.0)]),
        micro,
    )

    cam_ents, cam_cons = centered_rect("cam", cam, cam, "#cam_board", "#cam_board", construction=True)
    lens_ent = circle("lens", 0.0, 0.0, lens_r)
    lens_id, micro = add(
        client,
        did,
        wid,
        eid,
        sketch(
            "Lens bore",
            cam_ents + [lens_ent],
            cam_cons + [_diameter("lens.dia", "lens", "#lens_bore")],
        ),
        micro,
    )
    _, micro = add(
        client,
        did,
        wid,
        eid,
        extrude("Lens bore", lens_id, "#front_wall", operation="REMOVE", points=[(0.0, 0.0)]),
        micro,
    )

    holes = []
    hole_cons = []
    hole_pts = []
    for i, (sx, sy) in enumerate(((1, 1), (1, -1), (-1, 1), (-1, -1))):
        cx, cy = sx * hole_dx, sy * hole_dy
        hid = f"pihole{i}"
        holes.append(circle(hid, cx, cy, hole_r))
        hole_cons.append(_diameter(f"{hid}.dia", hid, "#pi_hole_d"))
        hole_pts.append((cx, cy))
    holes_id, micro = add(
        client, did, wid, eid, sketch("Pi Zero mounting holes", holes, hole_cons), micro
    )
    _, micro = add(
        client,
        did,
        wid,
        eid,
        extrude("Pi mounting holes", holes_id, "#front_wall", operation="REMOVE", points=hole_pts),
        micro,
    )

    plane_id, micro = add(
        client, did, wid, eid, offset_plane("Lid plane", "#grip_d + #pod_d"), micro
    )
    lid_ents, lid_cons = centered_rect("lid", w, h, "#body_w", "#body_h")
    win_ents, win_cons = centered_rect("window", window, window, "#display_window", "#display_window")
    lid_id, micro = add(
        client,
        did,
        wid,
        eid,
        sketch(
            "Lid",
            lid_ents + win_ents,
            lid_cons + win_cons,
            plane_query=f'query=qCreatedBy(makeId("{plane_id}"), EntityType.FACE);',
        ),
        micro,
    )
    # The offset plane sits at the back of the shell. Extrude away from the body.
    _, micro = add(
        client,
        did,
        wid,
        eid,
        extrude(
            "Lid",
            lid_id,
            "#lid_t",
            operation="NEW",
            points=[wall_point],
            opposite=True,
            plane_y_m=_mm(VARIABLES["grip_d"]) + _mm(VARIABLES["pod_d"]),
        ),
        micro,
    )

    print(f"\nhttps://cad.onshape.com/documents/{did}")


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        sys.exit(0)
