"""Geometry hash of a module.

The hash covers, for every LOD and the collider (in name order): object name,
evaluated vertex positions in module space, corner normals, all UV layers,
face topology and the material slot names per face; plus, only if the module has
lights, every light's manifest fields and transform. Modifiers are evaluated
(same result as the FBX export with use_mesh_modifiers=True). Values are
rounded so that float noise does not change the hash.
"""

import hashlib


def _fmt(values, digits):
    return ",".join(f"{(round(v, digits) + 0.0):.{digits}f}" for v in values)


def lights_hash_part(lights, matrices):
    """Bytes for the module lights: name, every manifest field and the matrix relative to the
    module origin (``matrices``: name -> Matrix), sorted by name, rounded."""
    out = []
    for e in sorted(lights, key=lambda e: e["name"]):
        fields = []
        for k in sorted(e):
            v = e[k]
            if isinstance(v, (list, tuple)):
                fields.append(f"{k}=" + _fmt(v, 5))
            elif isinstance(v, float):
                fields.append(f"{k}=" + _fmt([v], 5))
            else:
                fields.append(f"{k}={v}")
        mat = matrices[e["name"]]
        fields.append("M=" + _fmt([v for row in mat for v in row], 5))
        out.append("|".join(fields))
    return b"LIGHTS" + "\n".join(out).encode()


def module_hash(objects, depsgraph, lights=(), light_matrices=None):
    """``objects`` must already be placed so that ``obj.location`` is relative to the module origin.

    ``lights`` are manifest entries (common.light_entry); they are only hashed when there are
    any, so modules without lights keep the hash they had before lights were supported."""
    h = hashlib.sha1()
    for obj in sorted(objects, key=lambda o: o.name):
        ev = obj.evaluated_get(depsgraph)
        me = ev.to_mesh()
        try:
            h.update(obj.name.encode())
            loc = obj.location
            co = [0.0] * (len(me.vertices) * 3)
            me.vertices.foreach_get("co", co)
            for i in range(0, len(co), 3):
                co[i] += loc.x
                co[i + 1] += loc.y
                co[i + 2] += loc.z
            h.update(b"V" + _fmt(co, 5).encode())

            nrm = [0.0] * (len(me.loops) * 3)
            if len(me.loops):
                me.corner_normals.foreach_get("vector", nrm)
            h.update(b"N" + _fmt(nrm, 4).encode())

            for layer in me.uv_layers:
                uv = [0.0] * (len(me.loops) * 2)
                if len(me.loops):
                    layer.uv.foreach_get("vector", uv)
                h.update(b"U" + layer.name.encode() + _fmt(uv, 5).encode())

            loops = [0] * len(me.loops)
            me.loops.foreach_get("vertex_index", loops)
            starts = [0] * len(me.polygons)
            me.polygons.foreach_get("loop_start", starts)
            mats = [0] * len(me.polygons)
            me.polygons.foreach_get("material_index", mats)
            h.update(b"L" + ",".join(map(str, loops)).encode())
            h.update(b"P" + ",".join(map(str, starts)).encode())

            slot_names = [s.material.name if s.material else "" for s in obj.material_slots]
            face_mats = [slot_names[i] if i < len(slot_names) else "" for i in mats]
            h.update(b"M" + "|".join(face_mats).encode())
        finally:
            ev.to_mesh_clear()
    if lights:
        h.update(lights_hash_part(lights, light_matrices or {}))
    return h.hexdigest()
