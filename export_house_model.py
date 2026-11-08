"""House model export for the iPhone app (house-model/1, see house-model-format.md).

Writes a package folder per house:

    <House>/model-house.json    house, storeys and rooms
    <House>/storeys/<id>.usdz   one model per storey
    <House>/props/<room>.usdz   optional: the Props of one room

Storeys are the storeys of all rooms (``Room_<Storey>_<Name>``) plus the
``Exterior_<Storey>`` children of the exterior collection. A storey model holds
the Structure instances of its rooms and the instances of its exterior; Props
and colliders are left out. Props go into a separate model per room, so the app
can show them on demand. Every module is written as one LOD (see ``_lod_object``).

Coordinates: a Blender point maps to ``model = (x, z, -y)`` (meters, Y up). The
USD exporter does this conversion itself (forward -Z, up Y).
"""

import os
import re
import tempfile
import zipfile

import bpy

from . import common as C, fbx_io, manifest
from .export_layout import _plain_prop
from .house_model_polygons import floor_polygons, model_xz

FORMAT = "house-model/1"
MODEL_FILE = "model-house.json"
STOREY_DIR = "storeys"
PROPS_DIR = "props"
EXTERIOR_STOREY_RE = re.compile(r"^Exterior_(?P<storey>[A-Za-z0-9]+)$")

COARSEST_LOD = -1
TEXTURE_SIZE = "512"     # usdz_downscale_size: KEEP, 256, 512, 1024, 2048, 4096


def package_dir(root, house):
    return os.path.join(root, house)


# --------------------------------------------------------------------------- gathering
def _instances(colls):
    for coll in colls:
        for obj in coll.all_objects:
            if obj.instance_type == "COLLECTION" and obj.instance_collection is not None:
                yield obj


def _nodes(node, group):
    if node.group == group:
        yield node
    for ch in node.children:
        yield from _nodes(ch, group)


def _value(coll, key):
    """Room property as plain data, None if unset or empty."""
    v = coll.get(key)
    if v is None or v == "":
        return None
    return _plain_prop(v)


def _text(coll, key):
    v = _value(coll, key)
    return None if v is None else str(v)


def _number(coll, key, report):
    v = _value(coll, key)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        report.warn("W_HM_PROPERTY", f"Room '{coll.name}': '{key}' = {v!r} is not a number, treated as unset")
        return None


def _gather(layout, report):
    """Rooms and storeys of the layout, before polygons and floor heights."""
    storeys = {}    # id -> dict(structure=[colls], exterior=[colls])

    def storey(sid):
        return storeys.setdefault(sid, dict(structure=[], exterior=[]))

    rooms = []
    for node in _nodes(layout, C.GROUP_ROOM):
        m = C.ROOM_RE.match(node.name)
        if not m:
            continue
        sid = m.group("storey")
        coll = node.collection
        structure = [ch.collection for ch in node.children if ch.group == C.GROUP_STRUCTURE]
        props = [ch.collection for ch in node.children if ch.group == C.GROUP_PROPS]
        storey(sid)["structure"].extend(structure)
        rooms.append(dict(
            collection=coll,
            props=props,
            seeds=[s.matrix_world.translation.copy() for s in node.seeds],
            entry=dict(
                id=_text(coll, "room_id") or _text(coll, "room_nr") or coll.name,
                collection=coll.name,
                storey=sid,
                nr=_text(coll, "room_nr"),
                name=_text(coll, "room_name"),
                type=_text(coll, "room_type"),
                area=_number(coll, "room_area", report),
                propCount=sum(1 for _ in _instances(props)),
                props=None,
            ),
        ))

    left_out = 0
    for ext in _nodes(layout, C.GROUP_EXTERIOR):
        for ch in ext.collection.children:
            m = EXTERIOR_STOREY_RE.match(ch.name)
            if m:
                storey(m.group("storey"))["exterior"].append(ch)
        stored = {o for ch in ext.collection.children if EXTERIOR_STOREY_RE.match(ch.name) for o in _instances([ch])}
        left_out += sum(1 for o in _instances([ext.collection]) if o not in stored)
    if left_out:
        report.warn("W_HM_EXTERIOR", f"{left_out} exterior instance(s) are not in an Exterior_<Storey> collection "
                                     "and are left out of the house model")
    return storeys, rooms


def _floor_z(sid, s, report):
    for key in ("structure", "exterior"):
        zs = [o.matrix_world.translation.z for o in _instances(s[key])]
        if zs:
            return min(zs)
    report.warn("W_HM_FLOOR", f"Storey '{sid}' has no instances, floorY = 0")
    return 0.0


# --------------------------------------------------------------------------- models
def _lod_object(coll, lod):
    """The module's mesh for LOD index ``lod``: COARSEST_LOD = highest index the module has, otherwise
    that index or, if missing, the module's coarsest LOD below it."""
    lods = {}
    for o in coll.objects:
        lm = C.LOD_RE.match(o.name)
        if lm and lm.group("module") == coll.name and o.type == "MESH":
            lods[int(lm.group("idx"))] = o
    if not lods:
        return None
    if lod < 0:
        return lods[max(lods)]
    return lods[max((i for i in lods if i <= lod), default=min(lods))]


def _usd_export(path):
    required = dict(
        selected_objects_only=False, export_animation=False,
        export_materials=True, generate_preview_surface=True,
        use_instancing=True, evaluation_mode="RENDER",
        convert_orientation=True, export_global_forward_selection="NEGATIVE_Z", export_global_up_selection="Y",
    )
    # options that older Blender versions (4.2+) may not have; left out there
    optional = dict(
        export_textures_mode="NEW", overwrite_textures=True, usdz_downscale_size=TEXTURE_SIZE,
        export_custom_properties=False, export_lights=False, export_cameras=False, export_armatures=False,
        export_shapekeys=False, export_hair=False, export_volumes=False, export_points=False,
    )
    available = bpy.ops.wm.usd_export.get_rna_type().properties.keys()
    missing = [k for k in required if k not in available]
    if missing:
        raise RuntimeError(f"USD exporter does not support required options: {missing}")
    kwargs = dict(required, **{k: v for k, v in optional.items() if k in available})
    result = bpy.ops.wm.usd_export(filepath=path, check_existing=False, **kwargs)
    if "FINISHED" not in result:
        raise RuntimeError(f"USD export failed for {path}: {result}")


def fix_normal_maps(path):
    """Set the UsdPreviewSurface normal map encoding on every texture that feeds a ``normal`` input.

    Blender writes 8-bit normal maps without scale / bias, or with a wrong alpha component
    (2,2,2,2 / -1,-1,-1,-1), and sometimes without ``sourceColorSpace = raw``. The USDZ is unpacked,
    its root layer edited and packed again. Returns the number of textures changed.
    """
    from pxr import Gf, Sdf, UsdUtils

    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(path) as z:
            root_name = z.namelist()[0]   # a USDZ starts with its root layer
            z.extractall(tmp)
        layer = Sdf.Layer.FindOrOpen(os.path.join(tmp, root_name))
        targets = set()

        def visit(p):
            if p.IsPropertyPath() and p.name == "inputs:normal":
                spec = layer.GetAttributeAtPath(p)
                if spec is not None:
                    targets.update(c.GetPrimPath() for c in spec.connectionPathList.GetAddedOrExplicitItems())
        layer.Traverse(Sdf.Path.absoluteRootPath, visit)

        wanted = (("inputs:scale", Sdf.ValueTypeNames.Float4, Gf.Vec4f(2, 2, 2, 1)),
                  ("inputs:bias", Sdf.ValueTypeNames.Float4, Gf.Vec4f(-1, -1, -1, 0)),
                  ("inputs:sourceColorSpace", Sdf.ValueTypeNames.Token, "raw"))
        changed = 0
        for prim_path in sorted(targets):
            prim = layer.GetPrimAtPath(prim_path)
            ident = prim.attributes.get("info:id") if prim is not None else None
            if ident is None or ident.default != "UsdUVTexture":
                continue
            dirty = False
            for name, type_name, value in wanted:
                attr = prim.attributes.get(name) or Sdf.AttributeSpec(prim, name, type_name)
                if attr.default != value:
                    attr.default = value
                    dirty = True
            changed += dirty
        if not changed:
            return 0
        layer.Save()
        packed = os.path.join(tmp, "__packed.usdz")
        if not UsdUtils.CreateNewUsdzPackage(Sdf.AssetPath(layer.realPath), packed):
            raise RuntimeError(f"Repacking {path} failed")
        os.replace(packed, path)
        return changed


def export_model(src_scene, path, colls, lod, report):
    """Write the instances of ``colls`` as one USDZ. Every module becomes one prototype that is instanced."""
    created_objs, created_colls = [], []
    try:
        with fbx_io.temp_scene(src_scene) as (tmp, view_layer):
            protos = {}
            count = 0
            for inst in list(_instances(colls)):   # list first: creating objects below invalidates the iterator
                module = inst.instance_collection
                if module not in protos:
                    src = _lod_object(module, lod)
                    proto = None
                    if src is not None:
                        proto = bpy.data.collections.new(f"__hm_{module.name}")
                        created_colls.append(proto)
                        proto.instance_offset = module.instance_offset
                        copy = src.copy()
                        created_objs.append(copy)
                        copy.parent = None
                        copy.matrix_world = src.matrix_world.copy()
                        copy.hide_viewport = copy.hide_render = False
                        proto.objects.link(copy)
                    protos[module] = proto
                proto = protos[module]
                if proto is None:
                    continue
                e = bpy.data.objects.new(inst.name, None)
                created_objs.append(e)
                e.instance_type = "COLLECTION"
                e.instance_collection = proto
                e.matrix_world = inst.matrix_world.copy()
                tmp.collection.objects.link(e)
                count += 1
            _usd_export(path)
            fixed = fix_normal_maps(path)
            if fixed:
                report.info(f"{os.path.basename(path)}: normal map encoding set on {fixed} texture(s)")
            return count
    finally:
        for o in created_objs:
            try:
                bpy.data.objects.remove(o, do_unlink=True)
            except ReferenceError:
                pass
        for c in created_colls:
            bpy.data.collections.remove(c)


# --------------------------------------------------------------------------- export
def _props_stem(rid):
    """File name (without extension) of a room's props model."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", rid).lstrip(".") or "_"


def _remove_stale(folder, keep, what, report):
    if not os.path.isdir(folder):
        return
    for f in sorted(os.listdir(folder)):
        if f.endswith(".usdz") and f not in keep:
            os.remove(os.path.join(folder, f))
            report.info(f"Removed {what}: {os.path.basename(folder)}/{f}")


def export_house_model(src_scene, house, layout, root_dir, report,
                       storey_lod=COARSEST_LOD, export_props=True, props_lod=COARSEST_LOD):
    """Write the house-model/1 package. Returns (package folder, number of storeys) or (None, 0) on errors.

    ``storey_lod`` / ``props_lod``: LOD index of the modules in the storey / props models, COARSEST_LOD for
    the coarsest LOD of each module. Without ``export_props`` no props models are written and old ones removed.
    """
    storeys, rooms = _gather(layout, report)

    ids = {}
    for r in rooms:
        ids.setdefault(r["entry"]["id"], []).append(r["entry"]["collection"])
    for rid, colls in sorted(ids.items()):
        if len(colls) > 1:
            report.error("E_HM_ROOM_ID", f"Room id '{rid}' is used by {sorted(colls)}; ids must be unique (set room_id)")
    if export_props:
        stems = {}
        for r in rooms:
            if r["entry"]["propCount"]:
                stems.setdefault(_props_stem(r["entry"]["id"]).lower(), []).append(r["entry"]["id"])
        for same in stems.values():
            if len(same) > 1:
                report.error("E_HM_PROPS_FILE", f"Room ids {sorted(same)} map to the same props file name; "
                                                "make them differ in more than case or special characters")
    if not storeys:
        report.error("E_HM_EMPTY", "No rooms and no Exterior_<Storey> collections, nothing to export")
    if report.errors:
        return None, 0

    floor = {sid: _floor_z(sid, s, report) for sid, s in storeys.items()}
    order = sorted(storeys, key=lambda sid: (floor[sid], sid))

    for sid in order:
        s = storeys[sid]
        members = [r for r in rooms if r["entry"]["storey"] == sid]
        polys = floor_polygons(s["structure"] + s["exterior"], floor[sid],
                               {i: (r["seeds"], r["entry"]["area"]) for i, r in enumerate(members)})
        for i, r in enumerate(members):
            r["entry"]["seeds"] = [model_xz(p) for p in r["seeds"]]
            r["entry"]["polygons"] = polys[i]

    pkg = package_dir(root_dir, house)
    storey_dir = os.path.join(pkg, STOREY_DIR)
    os.makedirs(storey_dir, exist_ok=True)
    entries = []
    for sid in order:
        rel = f"{STOREY_DIR}/{sid}.usdz"
        s = storeys[sid]
        n = export_model(src_scene, os.path.join(pkg, rel), s["structure"] + s["exterior"], storey_lod, report)
        report.info(f"Storey {sid}: floorY {floor[sid]:.3f}, {n} instance(s) -> {rel}")
        entries.append(dict(id=sid, floorY=round(floor[sid], 3) + 0.0, file=rel))
    _remove_stale(storey_dir, {f"{sid}.usdz" for sid in order}, "storey model of a storey that no longer exists", report)

    props_dir = os.path.join(pkg, PROPS_DIR)
    keep = set()
    if export_props:
        for r in rooms:
            e = r["entry"]
            if not e["propCount"]:
                continue
            os.makedirs(props_dir, exist_ok=True)
            rel = f"{PROPS_DIR}/{_props_stem(e['id'])}.usdz"
            n = export_model(src_scene, os.path.join(pkg, rel), r["props"], props_lod, report)
            if n:
                e["props"] = rel
                keep.add(os.path.basename(rel))
                report.info(f"Room {e['id']}: {n} prop(s) -> {rel}")
    _remove_stale(props_dir, keep, "props model", report)
    if os.path.isdir(props_dir) and not os.listdir(props_dir):
        os.rmdir(props_dir)

    rank = {sid: i for i, sid in enumerate(order)}
    room_entries = [r["entry"] for r in sorted(rooms, key=lambda r: (rank[r["entry"]["storey"]], r["entry"]["id"]))]
    data = dict(format=FORMAT, house=house, exported=manifest.now_iso(), storeys=entries, rooms=room_entries)
    manifest.write_json(os.path.join(pkg, MODEL_FILE), data)

    with_poly = [e for e in room_entries if e["polygons"]]
    pins = [e for e in room_entries if not e["polygons"] and e["seeds"]]
    unmarked = [e["collection"] for e in room_entries if not e["polygons"] and not e["seeds"]]
    report.info(f"Rooms: {len(room_entries)}, {len(with_poly)} with polygons, {len(pins)} with a pin only")
    if unmarked:
        report.warn("W_HM_NO_MARKER", f"{len(unmarked)} room(s) have neither polygons nor seeds and only appear in the "
                                      f"room list: {unmarked}")
    return pkg, len(entries)
