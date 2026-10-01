"""Geometry hash of a module.

The hash covers, for every LOD and the collider (in name order): object name,
evaluated vertex positions in module space, corner normals, all UV layers,
face topology and the material slot names per face. Modifiers are evaluated
(same result as the FBX export with use_mesh_modifiers=True). Values are
rounded so that float noise does not change the hash.
"""

import hashlib


def _fmt(values, digits):
    return ",".join(f"{(round(v, digits) + 0.0):.{digits}f}" for v in values)


def module_hash(objects, depsgraph):
    """``objects`` must already be placed so that ``obj.location`` is relative to the module origin."""
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
    return h.hexdigest()
