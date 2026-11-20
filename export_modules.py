"""Module export: one FBX per module with all LODs, the optional collider and one empty
per light (``<Module>_Light[_<nn>]``); the light parameters go into the manifest (``lights``).

The module origin (collection.instance_offset) is moved to the world origin
for the export by temporarily shifting the objects' locations. Locations and
viewport visibility are restored in ``finally``.
"""

import os

import bpy
from mathutils import Matrix

from . import common as C
from . import fbx_io, hashing, manifest


def export_modules(src_scene, modules, out_dir, manifest_path, meta, force, report):
    """Export ``modules`` (list[ModuleInfo]) to ``out_dir`` and update the manifest.

    ``meta`` is merged into the manifest header (e.g. source, house).
    Returns (exported_names, skipped_names).
    """
    old = manifest.read_json(manifest_path)
    old_entries = manifest.module_entries(old)
    entries, exported, skipped = [], [], []
    os.makedirs(out_dir, exist_ok=True)

    with fbx_io.temp_scene(src_scene) as (tmp, view_layer):
        for m in modules:
            objs = m.objects
            offset = m.offset.copy()
            saved = [(o, o.location.copy(), o.matrix_world.copy(), o.hide_viewport) for o in objs]
            linked, empties, held = [], [], []   # held: (light object, original name)
            try:
                for o in objs:
                    tmp.collection.objects.link(o)
                    linked.append(o)
                    o.hide_viewport = False
                    # matrix_world is set explicitly: the temporary scene's depsgraph does not
                    # write evaluated matrices back to the original objects the exporter reads.
                    o.matrix_world = Matrix.Translation(-offset) @ o.matrix_world
                # Lights: an empty with the light's name and transform (relative to the module
                # origin) goes into the FBX, so the axis conversion is the same as for the meshes.
                # The parameters go into the manifest. The light itself is not exported.
                lights = [C.light_entry(light) for light in m.lights]
                light_matrices = {}
                for light in m.lights:
                    name = light.name
                    light_matrices[name] = Matrix.Translation(-offset) @ light.matrix_world
                    held.append((light, name))
                    light.name = f"__hp_light_hold_{len(held)}"   # frees the name for the empty
                    e = bpy.data.objects.new(name, None)
                    empties.append(e)
                    e.empty_display_size = 0.1
                    tmp.collection.objects.link(e)
                    e.matrix_world = light_matrices[name]
                depsgraph = bpy.context.evaluated_depsgraph_get()
                depsgraph.update()
                digest = hashing.module_hash(objs, depsgraph, lights, light_matrices)

                filename = f"{m.name}.fbx"
                path = os.path.join(out_dir, filename)
                prev = old_entries.get(m.name)
                unchanged = prev is not None and prev.get("hash") == digest and os.path.isfile(path)
                if unchanged and not force:
                    skipped.append(m.name)
                    entries.append(dict(prev, category=m.category, detail=m.detail, behavior=m.behavior))
                    continue
                fbx_io.export_scene_contents(path, {"MESH", "EMPTY"} if empties else {"MESH"})
                exported.append(m.name)
                entry = dict(
                    name=m.name,
                    file=filename,
                    category=m.category,
                    detail=m.detail,
                    behavior=m.behavior,
                    lods=sorted(m.lods),
                    hasCollider=m.collider is not None,
                    hash=digest,
                    exported=manifest.now_iso(),
                )
                if lights:
                    entry["lights"] = lights
                entries.append(entry)
            finally:
                for e in empties:
                    bpy.data.objects.remove(e, do_unlink=True)
                for light, name in held:
                    light.name = name
                for o in linked:
                    tmp.collection.objects.unlink(o)
                for o, loc, mw, hide in saved:
                    o.matrix_world = mw
                    o.location = loc
                    o.hide_viewport = hide

    removed = sorted(set(old_entries) - {e["name"] for e in entries})
    for name in removed:
        report.info(f"Module '{name}' no longer exists and was removed from the manifest (FBX file left in place)")

    data = dict(meta)
    data.update(version=manifest.MANIFEST_VERSION, exported=manifest.now_iso(),
                modules=sorted(entries, key=lambda e: e["name"]))
    manifest.write_json(manifest_path, data)
    return exported, skipped


def kit_paths(root, mode):
    sub = "Exterior" if mode == C.MODE_KIT_EXTERIOR else "Interior"
    out_dir = os.path.join(root, "Kit", sub)
    return out_dir, os.path.join(out_dir, "Kit_Manifest.json")


def unique_paths(root, house):
    out_dir = os.path.join(root, "Houses", house, "Unique")
    return out_dir, os.path.join(out_dir, f"{house}_Unique_Manifest.json")
