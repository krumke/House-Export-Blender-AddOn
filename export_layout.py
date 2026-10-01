"""Layout export: one FBX per house that contains only empties.

The layout hierarchy (house root, Exterior, folders, Interior, rooms,
Structure, Props) is rebuilt with plain empties in a temporary scene. Every
collection instance becomes a plain empty with the instance's world matrix
and the custom properties module / category / room / slot. Houses are
exported in world coordinates: all grouping empties sit at the origin.

Axis conversion: Blender's FBX exporter converts nested empties wrongly when
``bake_space_transform`` is on (only direct children of the root are correct).
The layout is therefore exported without it, and the empties are converted
here instead: the root gets G^-1 (written as identity by the exporter, which
pre-multiplies G for root objects) and every instance gets G @ M @ G^-1. In the
file this is exactly what the baked module FBX expect (vertices G @ v, LOD
transforms identity), so layout and modules line up in FBX/Unity space.

Temporary empties get the exact names of their source collections/instances.
Objects of the user's file that already carry such a name are renamed for the
duration of the export and get their name back in ``finally``.
"""

import os

import bpy
from mathutils import Matrix

from . import fbx_io, manifest


def export_layout(src_scene, house, layout, unique_modules, root_dir, report):
    out_dir = os.path.join(root_dir, "Houses", house, "Layout")
    os.makedirs(out_dir, exist_ok=True)
    fbx_path = os.path.join(out_dir, f"{house}_Layout.fbx")
    manifest_path = os.path.join(out_dir, f"{house}_Layout_Manifest.json")

    created, held = [], []   # held: (object, original name)

    def claim(name):
        existing = bpy.data.objects.get(name)
        if existing is not None and existing not in created and existing.library is None:
            held.append((existing, existing.name))
            existing.name = f"__hp_hold_{len(held)}"

    def make_empty(name, parent, tmp):
        claim(name)
        e = bpy.data.objects.new(name, None)
        created.append(e)
        if e.name != name:
            report.warn("W_NAME", f"Layout node '{name}' was exported as '{e.name}' (name taken by linked data)")
        e.empty_display_size = 0.25
        tmp.collection.objects.link(e)
        if parent is not None:
            e.parent = parent
            e.matrix_parent_inverse = Matrix.Identity(4)
        return e

    G = fbx_io.axis_matrix()
    G_inv = G.inverted()
    counts, rooms = {}, []
    try:
        with fbx_io.temp_scene(src_scene) as (tmp, view_layer):
            def build(node, parent):
                e = make_empty(node.name, parent, tmp)
                if parent is None:
                    e.matrix_basis = G_inv.copy()
                    e.matrix_world = G_inv.copy()
                else:
                    e.matrix_world = parent.matrix_world.copy()   # local identity
                if node.group != "root":
                    e["group"] = node.group
                if node.group == "room":
                    storey = node.name.split("_")[1] if node.name.count("_") >= 2 else ""
                    rooms.append(dict(name=node.name, storey=storey))
                for inst in node.instances:
                    ie = make_empty(inst.obj.name, e, tmp)
                    # Local matrix in FBX space (see module docstring). matrix_world is set as
                    # well because the temporary scene is never evaluated into these objects.
                    local = G @ inst.obj.matrix_world @ G_inv
                    ie.matrix_basis = local
                    ie.matrix_world = e.matrix_world @ local
                    ie["module"] = inst.module
                    ie["category"] = inst.category
                    if inst.category == "interior":
                        ie["room"] = inst.room
                        ie["slot"] = inst.slot
                    key = (inst.module, inst.source)
                    counts[key] = counts.get(key, 0) + 1
                for ch in node.children:
                    build(ch, e)

            build(layout, None)
            fbx_io.export_scene_contents(fbx_path, {"EMPTY"}, bake_space_transform=False)
            for e in reversed(created):
                bpy.data.objects.remove(e, do_unlink=True)
            created.clear()
    finally:
        for e in created:
            try:
                bpy.data.objects.remove(e, do_unlink=True)
            except ReferenceError:
                pass
        for obj, name in reversed(held):
            obj.name = name

    modules = [dict(name=n, source=s, count=c) for (n, s), c in sorted(counts.items())]
    data = dict(
        version=manifest.MANIFEST_VERSION,
        house=house,
        exported=manifest.now_iso(),
        coordinateSpace="world",
        layoutFile=os.path.basename(fbx_path),
        instanceCount=sum(counts.values()),
        modules=modules,
        uniques=sorted(m.name for m in unique_modules),
        rooms=rooms,
    )
    manifest.write_json(manifest_path, data)
    return fbx_path, data["instanceCount"]
