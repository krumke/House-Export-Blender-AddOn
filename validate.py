"""Validation of kit files and house files.

Every rule has a short code so tests and users can refer to it:

Errors (export is aborted)
  E_NAME            module name contains characters other than A-Z a-z 0-9 _
  E_NO_LOD0         module has no <Module>_LOD0 mesh
  E_OBJECT_NAME     object in a module collection does not follow <Module>_LOD<n> / <Module>_COL
  E_FOREIGN_OBJECT  non-mesh object in a module collection
  E_UNAPPLIED       rotation/scale of a module mesh is not applied
  E_NEGATIVE_SCALE  negative scale on a module mesh or on an instance
  E_PARENT          module mesh is parented
  E_NESTED          module contains collection instances or child collections
  E_UNIQUE_PREFIX   unique module without <House>_ prefix
  E_UNKNOWN_MODULE  instance points to a collection that is neither linked from a kit nor a unique module
  E_NO_ROOT         house root collection missing
  E_LAYOUT          layout does not follow the Exterior / Interior / Room / Structure / Props structure
  E_LOOSE_OBJECT    geometry in the layout that is not a collection instance
  E_MULTI_LINK      instance linked into more than one layout collection

Warnings (export continues)
  W_LOD_BOUNDS      LOD bounding boxes differ noticeably (origin shifted?)
  W_NONUNIFORM      instances with non-uniform scale
  W_NO_DETAIL       interior module without 'detail' property (default 'large' is used)
  W_NO_CATEGORY     unique module without 'category' property (derived from usage)
  W_APPENDED        local copy of what looks like a kit module (append instead of link)
  W_GROUP_MISSING   layout sub-collection without 'group' property, treated as folder
  W_IGNORED         non-geometry object inside the layout is ignored
  W_UNITS           scene unit scale is not 1.0
"""

from dataclasses import dataclass, field

import bpy
from mathutils import Vector

from . import common as C


# --------------------------------------------------------------------------- layout model
@dataclass
class LayoutNode:
    name: str
    group: str                       # root / exterior / interior / room / structure / props / folder
    children: list = field(default_factory=list)
    instances: list = field(default_factory=list)   # list[LayoutInstance]


@dataclass
class LayoutInstance:
    obj: object
    module: str
    source: str
    category: str
    room: str = ""
    slot: str = ""


# --------------------------------------------------------------------------- modules
def _bounds(obj):
    pts = [Vector(c) + obj.location for c in obj.bound_box]
    return (Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts))),
            Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts))))


def validate_module(m, report, house=None):
    n = m.name
    coll = m.collection
    if not C.MODULE_NAME_RE.match(n):
        report.error("E_NAME", f"Module '{n}': name may only contain A-Z, a-z, 0-9 and '_' (no dots, spaces or umlauts)")
    if house and m.source == C.SOURCE_UNIQUE and not n.startswith(house + "_"):
        report.error("E_UNIQUE_PREFIX", f"Unique module '{n}' must start with '{house}_'")
    if coll.children:
        report.error("E_NESTED", f"Module '{n}' contains child collections: {[c.name for c in coll.children]}")

    known = set(m.lods.values()) | ({m.collider} if m.collider else set())
    for o in coll.objects:
        if o.instance_type == "COLLECTION" and o.instance_collection:
            report.error("E_NESTED", f"Module '{n}' contains collection instance '{o.name}'")
            continue
        if o.type != "MESH":
            report.error("E_FOREIGN_OBJECT", f"Module '{n}': object '{o.name}' is a {o.type}, only meshes are allowed")
            continue
        if o not in known:
            report.error("E_OBJECT_NAME", f"Module '{n}': object '{o.name}' must be named '{n}_LOD<n>' or '{n}_COL'")
        if o.parent is not None:
            report.error("E_PARENT", f"Module '{n}': object '{o.name}' is parented to '{o.parent.name}'")
        if o.matrix_basis.determinant() < 0 or any(s < 0 for s in o.scale):
            report.error("E_NEGATIVE_SCALE", f"Module '{n}': object '{o.name}' has negative scale; model mirrored parts as their own module")
        elif not C.is_identity_3x3(o.matrix_basis):
            report.error("E_UNAPPLIED", f"Module '{n}': object '{o.name}' has unapplied rotation/scale (Ctrl+A)")

    if 0 not in m.lods:
        report.error("E_NO_LOD0", f"Module '{n}' has no '{n}_LOD0'")
    else:
        mn0, mx0 = _bounds(m.lods[0])
        size = max((mx0 - mn0).length, 1e-6)
        for idx, o in sorted(m.lods.items()):
            if idx == 0:
                continue
            mn, mx = _bounds(o)
            d = max((mn - mn0).length, (mx - mx0).length)
            if d > max(0.05, 0.05 * size):
                report.warn("W_LOD_BOUNDS", f"Module '{n}': bounds of LOD{idx} differ from LOD0 by {d:.3f} m (origin shifted?)")

    if m.category == "interior" and not getattr(m, "_detail_explicit", True):
        report.warn("W_NO_DETAIL", f"Interior module '{n}' has no 'detail' property, using '{m.detail}'")


# --------------------------------------------------------------------------- kit
def validate_kit(scene, mode):
    report = C.Report()
    _check_units(scene, report)
    category = "exterior" if mode == C.MODE_KIT_EXTERIOR else "interior"
    modules = []
    for coll in C.kit_module_collections(scene):
        m = C.gather_module(coll, C.SOURCE_KIT, category=category)
        validate_module(m, report)
        modules.append(m)
    if not modules:
        report.error("E_NO_LOD0", "No module collections found in this kit file")
    return report, modules


# --------------------------------------------------------------------------- house
def validate_house(scene, house):
    report = C.Report()
    # make sure matrix_world of every instance reflects the latest edits
    for vl in scene.view_layers:
        vl.update()
    _check_units(scene, report)
    root = bpy.data.collections.get(house)
    if root is None or root not in C.scene_collections(scene):
        report.error("E_NO_ROOT", f"House root collection '{house}' not found in scene '{scene.name}'")
        return report, [], None

    uniques = {c: C.gather_module(c, C.SOURCE_UNIQUE) for c in C.house_unique_collections(scene, house)}
    layout = _parse_layout(root, uniques, report)

    # Category of unique modules without explicit property: derived from usage
    usage = {}
    for inst in _iter_instances(layout):
        usage.setdefault(inst.module, set()).add(inst.category)
    for m in uniques.values():
        if not m._category_explicit:
            cats = usage.get(m.name, set())
            m.category = "interior" if cats == {"interior"} else "exterior"
            if not m._detail_explicit:
                m.detail = C.DEFAULT_DETAIL[m.category]
            report.warn("W_NO_CATEGORY", f"Unique module '{m.name}' has no 'category' property, using '{m.category}'")
        validate_module(m, report, house=house)

    # Local collections that look like kit modules but are neither unique nor linked
    for c in bpy.data.collections:
        if c.library is None and c not in uniques and any(C.LOD_RE.match(o.name) for o in c.objects):
            report.warn("W_APPENDED", f"Collection '{c.name}' looks like a kit module but is a local copy; link it from the kit instead of appending")

    # Linked kit modules: minimal check
    seen = set()
    for inst in _iter_instances(layout):
        coll = inst.obj.instance_collection
        if inst.source == C.SOURCE_KIT and coll not in seen:
            seen.add(coll)
            m = C.gather_module(coll, C.SOURCE_KIT)
            if 0 not in m.lods:
                report.error("E_NO_LOD0", f"Linked kit module '{coll.name}' has no '{coll.name}_LOD0'")

    # Non-uniform instance scale, aggregated per module
    nonuni = {}
    for inst in _iter_instances(layout):
        if not C.is_uniform_scale(inst.obj.matrix_world):
            nonuni[inst.module] = nonuni.get(inst.module, 0) + 1
    for mod, cnt in sorted(nonuni.items()):
        report.warn("W_NONUNIFORM", f"{cnt} instance(s) of '{mod}' have non-uniform scale")

    return report, sorted(uniques.values(), key=lambda m: m.name), layout


def _check_units(scene, report):
    if abs(scene.unit_settings.scale_length - 1.0) > 1e-6:
        report.warn("W_UNITS", f"Scene unit scale is {scene.unit_settings.scale_length}, expected 1.0 (1 BU = 1 m)")


def _iter_instances(node):
    if node is None:
        return
    yield from node.instances
    for ch in node.children:
        yield from _iter_instances(ch)


def _parse_layout(root, uniques, report):
    node = LayoutNode(root.name, "root")
    seen_objs = {}
    groups = {}
    for ch in root.children:
        g = C.collection_group(ch)
        if g in (C.GROUP_EXTERIOR, C.GROUP_INTERIOR):
            if g in groups:
                report.error("E_LAYOUT", f"House '{root.name}' has more than one '{g}' collection")
            groups[g] = ch
            node.children.append(_walk(ch, g, {"category": g}, uniques, report, seen_objs))
        else:
            report.error("E_LAYOUT", f"Collection '{ch.name}' under house root must be 'Exterior' or 'Interior' (group property)")
    for g in (C.GROUP_EXTERIOR, C.GROUP_INTERIOR):
        if g not in groups:
            report.error("E_LAYOUT", f"House '{root.name}' has no '{g.capitalize()}' collection")
    _check_objects(root, None, "house root", uniques, report, seen_objs)
    return node


def _walk(coll, group, ctx, uniques, report, seen_objs):
    node = LayoutNode(coll.name, group)
    if group == C.GROUP_INTERIOR:
        _check_objects(coll, None, "Interior (place instances in a room's Structure/Props)", uniques, report, seen_objs)
        for ch in coll.children:
            g = C.collection_group(ch)
            if g != C.GROUP_ROOM:
                report.error("E_LAYOUT", f"'{ch.name}' under Interior must be a room named Room_<Storey>_<Name>")
                continue
            node.children.append(_walk(ch, C.GROUP_ROOM, dict(ctx), uniques, report, seen_objs))
        return node

    if group == C.GROUP_ROOM:
        if not C.ROOM_RE.match(coll.name):
            report.error("E_LAYOUT", f"Room '{coll.name}' must be named Room_<Storey>_<Name> (e.g. Room_EG_Kueche)")
        _check_objects(coll, None, f"room '{coll.name}' (use its Structure/Props)", uniques, report, seen_objs)
        slots = set()
        for ch in coll.children:
            g = C.collection_group(ch)
            if g in (C.GROUP_STRUCTURE, C.GROUP_PROPS):
                slots.add(g)
                sub = dict(ctx, room=coll.name, slot=g)
                node.children.append(_walk(ch, g, sub, uniques, report, seen_objs))
            else:
                report.error("E_LAYOUT", f"'{ch.name}' in room '{coll.name}' must be Structure or Props (group property)")
        for g in (C.GROUP_STRUCTURE, C.GROUP_PROPS):
            if g not in slots:
                report.error("E_LAYOUT", f"Room '{coll.name}' has no {g.capitalize()} collection")
        return node

    # exterior, structure, props, folder: instances allowed, sub-collections are folders
    _check_objects(coll, node, None, uniques, report, seen_objs, ctx)
    for ch in coll.children:
        g = C.collection_group(ch)
        if g != C.GROUP_FOLDER:
            if g is None:
                report.warn("W_GROUP_MISSING", f"Collection '{ch.name}' has no 'group' property, treated as folder")
            else:
                report.error("E_LAYOUT", f"Collection '{ch.name}' (group '{g}') is not allowed inside '{coll.name}'")
                continue
        node.children.append(_walk(ch, C.GROUP_FOLDER, dict(ctx), uniques, report, seen_objs))
    return node


def _check_objects(coll, node, forbidden_where, uniques, report, seen_objs, ctx=None):
    for o in coll.objects:
        is_inst = o.instance_type == "COLLECTION" and o.instance_collection is not None
        if not is_inst:
            if o.type in {"MESH", "CURVE", "SURFACE", "FONT", "META", "CURVES", "POINTCLOUD", "VOLUME", "GPENCIL", "GREASEPENCIL"}:
                report.error("E_LOOSE_OBJECT", f"'{o.name}' in '{coll.name}' is geometry, not a collection instance of a module")
            else:
                report.warn("W_IGNORED", f"'{o.name}' ({o.type}) in '{coll.name}' is ignored by the layout export")
            continue
        if forbidden_where:
            report.error("E_LAYOUT", f"Instance '{o.name}' lies directly in {forbidden_where}")
            continue
        if o in seen_objs:
            report.error("E_MULTI_LINK", f"Instance '{o.name}' is linked into '{seen_objs[o]}' and '{coll.name}'")
            continue
        seen_objs[o] = coll.name
        target = o.instance_collection
        if target.library is not None:
            source = C.SOURCE_KIT
        elif target in uniques:
            source = C.SOURCE_UNIQUE
        else:
            report.error("E_UNKNOWN_MODULE", f"Instance '{o.name}' points to '{target.name}', which is neither linked from a kit nor a unique module (unique=True / _Modules_<House>)")
            continue
        if o.matrix_world.determinant() < 0:
            report.error("E_NEGATIVE_SCALE", f"Instance '{o.name}' has negative scale; use a mirrored module instead")
        node.instances.append(_make_instance(o, target.name, source, ctx or {}))


def _make_instance(o, module, source, ctx):
    category = ctx.get("category", "exterior")
    return LayoutInstance(obj=o, module=module, source=source, category=category,
                          room=ctx.get("room", "") if category == "interior" else "",
                          slot=ctx.get("slot", "") if category == "interior" else "")
