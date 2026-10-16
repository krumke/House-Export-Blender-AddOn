"""Acceptance tests for the House Pipeline Export add-on.

Run (no GUI needed):
    blender -b --factory-startup --python tests/run_tests.py -- <empty output folder>

Builds synthetic kit and house scenes, exports them and checks the FBX files
directly in FBX file space (tests/fbx_reader.py), i.e. what Unity reads.
"""

import os
import sys
import shutil
import traceback

import bpy
from mathutils import Matrix, Vector, Euler
from math import radians

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))   # folder that contains the add-on package

import fbx_reader  # noqa: E402
import house_pipeline_export as hp  # noqa: E402
from house_pipeline_export import common as C, validate, fbx_io  # noqa: E402

OUT = sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else os.path.join(HERE, "_out")
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  ({detail})" if detail else ""))


# --------------------------------------------------------------------------- scene builders
def new_file():
    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.context.scene.unit_settings.scale_length = 1.0


def arrow_mesh(name, scale=1.0):
    """Asymmetric L-shape with an arrow tip and a step in Z (chiral, no symmetry plane)."""
    s = scale
    pts2d = [(0, 0), (2, 0), (2, 0.4), (2.6, 0.2), (2, 1.0), (2, 0.6), (0.5, 0.6), (0.5, 3), (0, 3)]
    verts = [(x * s, y * s, 0.0) for x, y in pts2d] + [(x * s, y * s, (0.3 if x < 1 else 0.6) * s) for x, y in pts2d]
    n = len(pts2d)
    faces = [list(range(n))[::-1], list(range(n, 2 * n))]
    faces += [[i, (i + 1) % n, n + (i + 1) % n, n + i] for i in range(n)]
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.uv_layers.new(name="UVMap")
    me.update()
    return me


def make_module(name, parent_coll, offset=(0, 0, 0), lods=(0, 1), category=None, detail=None, unique=True):
    coll = bpy.data.collections.new(name)
    parent_coll.children.link(coll)
    coll.instance_offset = offset
    for i in lods:
        o = bpy.data.objects.new(f"{name}_LOD{i}", arrow_mesh(f"{name}_LOD{i}", 1.0))
        o.location = offset
        coll.objects.link(o)
        if i > 0:
            o.hide_viewport = True
            o.hide_render = True
    if category:
        coll["category"] = category
    if detail:
        coll["detail"] = detail
    if unique:
        coll["unique"] = True
    return coll


def child(parent, name, group=None):
    c = bpy.data.collections.new(name)
    parent.children.link(c)
    if group:
        c["group"] = group
    return c


def instance(coll_target, parent, name, loc=(0, 0, 0), rot=(0, 0, 0), scale=(1, 1, 1)):
    e = bpy.data.objects.new(name, None)
    e.instance_type = "COLLECTION"
    e.instance_collection = coll_target
    e.location = loc
    e.rotation_euler = [radians(a) for a in rot]
    e.scale = scale
    parent.objects.link(e)
    return e


def build_house(house="TH"):
    """Valid house: modules in _Modules_TH, Exterior with nested folders, two rooms."""
    new_file()
    sc = bpy.context.scene.collection
    mods = child(sc, f"_Modules_{house}")
    arrow = make_module(f"{house}_Arrow", mods, offset=(40, 10, 2), lods=(0, 1, 2), category="exterior")
    shelf = make_module(f"{house}_Shelf", mods, offset=(50, 0, 0), lods=(0,), category="interior", detail="small")
    wall = make_module(f"{house}_InnerWall", mods, offset=(60, 0, 0), lods=(0,), category="interior", detail="structure")
    # modifier must be applied in the export, never in the file
    mod = shelf.objects[f"{house}_Shelf_LOD0"].modifiers.new("Array", "ARRAY")
    mod.count = 2
    root = child(sc, house)
    ext = child(root, "Exterior", "exterior")
    f1 = child(ext, "Facade", "folder")
    f2 = child(f1, "Facade_Upper", "folder")
    inte = child(root, "Interior", "interior")
    r1 = child(inte, "Room_EG_Kueche", "room")
    s1, p1 = child(r1, "Structure_EG_Kueche", "structure"), child(r1, "Props_EG_Kueche", "props")
    r2 = child(inte, "Room_OG1_Buero", "room")
    s2, p2 = child(r2, "Structure_OG1_Buero", "structure"), child(r2, "Props_OG1_Buero", "props")
    instance(arrow, ext, "A_plain", (1, 2, 3))
    instance(arrow, f1, "A_rotZ90", (-5, 7, 0), (0, 0, 90))
    instance(arrow, f2, "A_rot3axis", (12.5, -3.25, 4), (17, -33, 128))
    instance(arrow, f2, "A_scaled", (3, 3, 3), (0, 90, 0), (1.5, 1.5, 1.5))
    instance(wall, s1, "W_1", (0, 0, 0), (0, 0, 180))
    instance(shelf, p1, "S_1", (2, 1, 0), (0, 0, 45))
    instance(shelf, p2, "S_2", (-2, -1, 3.5), (0, 0, -30))
    instance(wall, s2, "W_2", (5, 5, 3.5), (90, 0, 0))
    return dict(arrow=arrow, shelf=shelf, wall=wall, root=root, ext=ext, inte=inte, r1=r1, s1=s1, p1=p1, mods=mods, f2=f2)


def snapshot():
    s = {}
    for o in bpy.data.objects:
        s["O:" + o.name] = (tuple(tuple(round(v, 7) for v in r) for r in o.matrix_basis), o.hide_viewport, o.hide_render,
                            tuple(sorted(c.name for c in o.users_collection)), o.data.name if o.data else None,
                            len(o.modifiers), o.select_get() if o.name in bpy.context.view_layer.objects else None)
    for c in bpy.data.collections:
        s["C:" + c.name] = (tuple(c.instance_offset), tuple(sorted(o.name for o in c.objects)), tuple(sorted(ch.name for ch in c.children)))
    s["scenes"] = tuple(sc.name for sc in bpy.data.scenes)
    s["counts"] = (len(bpy.data.objects), len(bpy.data.meshes), len(bpy.data.collections))
    return s


def world_points(inst):
    """World positions of all LOD0 vertices (evaluated, so modifiers count) of a collection instance."""
    coll = inst.instance_collection
    l0 = coll.objects[coll.name + "_LOD0"]
    dg = bpy.context.evaluated_depsgraph_get()
    ev = l0.evaluated_get(dg)
    me = ev.to_mesh()
    M = inst.matrix_world @ Matrix.Translation(-coll.instance_offset) @ l0.matrix_world
    pts = [M @ v.co for v in me.vertices]
    ev.to_mesh_clear()
    return pts


def file_space_check(house, layout_dir, unique_dir, instances):
    G = fbx_io.axis_matrix()
    _, lay, _ = fbx_reader.model_data(os.path.join(layout_dir, f"{house}_Layout.fbx"))
    by_name = {m["name"]: m for m in lay.values()}
    worst = 0.0
    for inst, pts in instances.items():
        mod = inst.instance_collection.name
        _, models, geoms = fbx_reader.model_data(os.path.join(unique_dir, f"{mod}.fbx"))
        l0 = next(m for m in models.values() if m["name"] == mod + "_LOD0")
        verts = geoms[l0["geom"]]
        W = by_name[inst.name]["world"] @ l0["world"]
        if len(verts) != len(pts):
            return float("inf"), f"{inst.name}: {len(verts)} verts in FBX, {len(pts)} expected"
        for v, p in zip(verts, pts):
            worst = max(worst, ((W @ v) - G @ p).length)
    return worst, by_name


# --------------------------------------------------------------------------- tests
def test_house_export():
    h = build_house()
    s = bpy.context.scene.house_pipeline
    s.export_root_override = OUT
    insts = {o: world_points(o) for o in h["root"].all_objects}
    before = snapshot()
    r = bpy.ops.house_pipeline.export_house()
    check("house export finishes", r == {"FINISHED"}, str(hp.operators.LAST_RESULT["lines"][:5]))
    s.export_root_override = ""
    after = snapshot()
    s.export_root_override = OUT
    diff = [k for k in set(before) | set(after) if before.get(k) != after.get(k)]
    check("house file unchanged after export (objects, transforms, visibility, collections, scenes)", not diff, str(diff[:5]))

    lay_dir = os.path.join(OUT, "Houses", "TH", "Layout")
    uni_dir = os.path.join(OUT, "Houses", "TH", "Unique")
    worst, by_name = file_space_check("TH", lay_dir, uni_dir, insts)
    check("asymmetric module: every instance matches Blender exactly in FBX space (rotations, 3-axis, scale, nested folders, rooms)",
          worst < 1e-4, f"max deviation {worst:.2e} m")
    if isinstance(by_name, dict):
        e = by_name["S_1"]
        pr = {k: v[0].decode() if isinstance(v[0], bytes) else v[0] for k, v in e["props"].items() if k in ("module", "category", "room", "slot")}
        check("instance custom properties", pr == dict(module="TH_Shelf", category="interior", room="Room_EG_Kueche", slot="props"), str(pr))
        e = by_name["A_rot3axis"]
        pr = {k: v[0].decode() for k, v in e["props"].items() if k in ("module", "category", "room", "slot")}
        check("exterior instance has no room/slot", pr == dict(module="TH_Arrow", category="exterior"), str(pr))
        groups = {n: m["props"].get("group", [b""])[0].decode() for n, m in by_name.items() if "group" in m["props"]}
        check("grouping empties carry 'group'", groups == {"Exterior": "exterior", "Facade": "folder", "Facade_Upper": "folder",
              "Interior": "interior", "Room_EG_Kueche": "room", "Structure_EG_Kueche": "structure", "Props_EG_Kueche": "props",
              "Room_OG1_Buero": "room", "Structure_OG1_Buero": "structure", "Props_OG1_Buero": "props"}, str(groups))
        parent = by_name["A_rot3axis"]["parent"]
        names = {uid: m["name"] for uid, m in fbx_reader.model_data(os.path.join(lay_dir, "TH_Layout.fbx"))[1].items()}
        check("hierarchy preserved (A_rot3axis under Facade_Upper)", names.get(parent) == "Facade_Upper", names.get(parent))
        _, lay_models, lay_geoms = fbx_reader.model_data(os.path.join(lay_dir, "TH_Layout.fbx"))
        check("layout FBX contains no geometry", not lay_geoms, f"{len(lay_geoms)} geometries")

    # module FBX: LODs, hidden LODs exported, modifier applied, origin at instance_offset
    _, models, geoms = fbx_reader.model_data(os.path.join(uni_dir, "TH_Arrow.fbx"))
    names = sorted(m["name"] for m in models.values())
    check("module FBX contains all LODs incl. hidden LOD1/LOD2", names == ["TH_Arrow_LOD0", "TH_Arrow_LOD1", "TH_Arrow_LOD2"], str(names))
    l0 = next(m for m in models.values() if m["name"] == "TH_Arrow_LOD0")
    G = fbx_io.axis_matrix()
    src = h["arrow"].objects["TH_Arrow_LOD0"]
    exp = [G @ (v.co + src.location - h["arrow"].instance_offset) for v in src.data.vertices]
    got = [l0["world"] @ v for v in geoms[l0["geom"]]]
    d = max((a - b).length for a, b in zip(exp, got))
    check("shifted instance_offset: module exported around its origin", d < 1e-5, f"max {d:.2e} m, offset {tuple(h['arrow'].instance_offset)}")
    _, models, geoms = fbx_reader.model_data(os.path.join(uni_dir, "TH_Shelf.fbx"))
    l0 = next(m for m in models.values() if m["name"] == "TH_Shelf_LOD0")
    n_src = len(h["shelf"].objects["TH_Shelf_LOD0"].data.vertices)
    check("modifiers applied in export only", len(geoms[l0["geom"]]) == 2 * n_src and len(h["shelf"].objects["TH_Shelf_LOD0"].modifiers) == 1,
          f"{len(geoms[l0['geom']])} verts exported, {n_src} in file")

    man = hp.manifest.read_json(os.path.join(uni_dir, "TH_Unique_Manifest.json"))
    e = {m["name"]: m for m in man["modules"]}
    check("unique manifest fields", e["TH_Arrow"]["lods"] == [0, 1, 2] and e["TH_Shelf"]["detail"] == "small"
          and e["TH_InnerWall"]["category"] == "interior" and not e["TH_Arrow"]["hasCollider"] and len(e["TH_Arrow"]["hash"]) == 40, str(e["TH_Arrow"]))
    lm = hp.manifest.read_json(os.path.join(lay_dir, "TH_Layout_Manifest.json"))
    check("layout manifest", lm["house"] == "TH" and lm["instanceCount"] == 8 and lm["coordinateSpace"] == "world"
          and {m["name"] for m in lm["modules"]} == {"TH_Arrow", "TH_Shelf", "TH_InnerWall"}
          and {r["name"]: r["storey"] for r in lm["rooms"]} == {"Room_EG_Kueche": "EG", "Room_OG1_Buero": "OG1"}, str(lm)[:300])

    # second export: no FBX rewritten
    mtimes = {f: os.path.getmtime(os.path.join(uni_dir, f)) for f in os.listdir(uni_dir) if f.endswith(".fbx")}
    bpy.ops.house_pipeline.export_house()
    same = all(os.path.getmtime(os.path.join(uni_dir, f)) == t for f, t in mtimes.items())
    check("second export without changes rewrites no module FBX", same and "skipped (unchanged): 3" in " ".join(hp.operators.LAST_RESULT["lines"]))

    # geometry change -> only that module is rewritten
    lod = h["wall"].objects["TH_InnerWall_LOD0"]
    lod.data.vertices[0].co.x += 0.1
    bpy.ops.house_pipeline.export_house()
    changed = [f for f, t in mtimes.items() if os.path.getmtime(os.path.join(uni_dir, f)) != t]
    check("changed module is re-exported, others skipped", changed == ["TH_InnerWall.fbx"], str(changed))
    lod.data.vertices[0].co.x -= 0.1

    # force export
    s.force_export = True
    bpy.ops.house_pipeline.export_house()
    s.force_export = False
    check("force export rewrites all", "exported: 3" in " ".join(hp.operators.LAST_RESULT["lines"]))

    # removed module disappears from manifest
    bpy.data.collections.remove(h["shelf"])
    for o in [o for o in bpy.data.objects if o.instance_type == "COLLECTION" and o.instance_collection is None]:
        bpy.data.objects.remove(o)
    bpy.ops.house_pipeline.export_house()
    man = hp.manifest.read_json(os.path.join(uni_dir, "TH_Unique_Manifest.json"))
    check("removed module dropped from manifest and reported", "TH_Shelf" not in {m["name"] for m in man["modules"]}
          and any("TH_Shelf" in l for l in hp.operators.LAST_RESULT["lines"]))


def test_door_export():
    """behavior=door in the manifest; a handle parented to a door instance is nested in the layout."""
    h = build_house("TD")
    door = h["arrow"]
    door["behavior"] = "door"
    h["shelf"]["behavior"] = "window"   # unknown -> warning, stays static
    d = bpy.data.objects["A_rot3axis"]
    handle = bpy.data.objects["A_scaled"]
    bpy.context.view_layer.update()
    mw = handle.matrix_world.copy()
    handle.parent = d
    handle.matrix_parent_inverse = d.matrix_world.inverted()
    bpy.context.view_layer.update()
    check("door test: parenting keeps the handle in place", all(abs(a - b) < 1e-6 for ra, rb in zip(mw, handle.matrix_world) for a, b in zip(ra, rb)))
    s = bpy.context.scene.house_pipeline
    s.export_root_override = OUT
    insts = {o: world_points(o) for o in h["root"].all_objects}
    r = bpy.ops.house_pipeline.export_house()
    lines = hp.operators.LAST_RESULT["lines"]
    check("door export finishes", r == {"FINISHED"}, str(lines[:5]))
    check("unknown behavior warns", any("W_BEHAVIOR" in l and "TD_Shelf" in l for l in lines), str(lines[:8]))
    check("door without _COL warns", any("W_DOOR_NO_COL" in l and "TD_Arrow" in l for l in lines), str(lines[:8]))

    lay_dir = os.path.join(OUT, "Houses", "TD", "Layout")
    uni_dir = os.path.join(OUT, "Houses", "TD", "Unique")
    worst, by_name = file_space_check("TD", lay_dir, uni_dir, insts)
    check("nested handle matches Blender in FBX space", worst < 1e-4, f"max deviation {worst:.2e} m")
    if isinstance(by_name, dict):
        names = {uid: m["name"] for uid, m in fbx_reader.model_data(os.path.join(lay_dir, "TD_Layout.fbx"))[1].items()}
        check("handle is a child of the door empty", names.get(by_name["A_scaled"]["parent"]) == "A_rot3axis",
              names.get(by_name["A_scaled"]["parent"]))
    man = hp.manifest.read_json(os.path.join(uni_dir, "TD_Unique_Manifest.json"))
    e = {m["name"]: m for m in man["modules"]}
    check("manifest behavior", e["TD_Arrow"]["behavior"] == "door" and e["TD_Shelf"]["behavior"] == "", str(e["TD_Arrow"]))
    lm = hp.manifest.read_json(os.path.join(lay_dir, "TD_Layout_Manifest.json"))
    check("nested instance counted once", lm["instanceCount"] == 8, str(lm["instanceCount"]))
    s.export_root_override = ""


def make_material_library():
    """Shared material library .blend with one textured material."""
    new_file()
    tex_dir = os.path.join(OUT, "_library_textures")
    os.makedirs(tex_dir, exist_ok=True)
    mat = bpy.data.materials.new("Lib_Metal")
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    t = nt.nodes.new("ShaderNodeTexImage")
    t.image = make_image(os.path.join(tex_dir, "Lib_Metal_color.png"), (0.2, 0.3, 0.4, 1), "sRGB")
    nt.links.new(t.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Metallic"].default_value = 1.0
    mat.use_fake_user = True
    path = os.path.join(OUT, "Material_Library.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    return path


def test_kit_export():
    import json
    lib_path = make_material_library()
    new_file()
    sc = bpy.context.scene.collection
    walls = child(sc, "Walls")   # folder collection without objects
    brick = make_module("Wall_Brick_240", walls, offset=(10, 0, 0), lods=(0, 1, 2), unique=False)
    make_module("Wall_Brick_120", walls, offset=(20, 0, 0), lods=(0,), unique=False)
    with bpy.data.libraries.load(lib_path, link=True) as (src, dst):
        dst.materials = ["Lib_Metal"]
    brick.objects["Wall_Brick_240_LOD0"].data.materials.append(dst.materials[0])
    local = bpy.data.materials.new("Kit_Local_Paint")
    brick.objects["Wall_Brick_240_LOD1"].data.materials.append(local)
    s = bpy.context.scene.house_pipeline
    s.export_root_override = OUT
    s.mode = C.MODE_KIT_EXTERIOR
    man_path = os.path.join(OUT, "MaterialSource", "Material_Manifest.json")

    s.export_linked_materials = False
    bpy.ops.house_pipeline.export_kit()
    names = {m["name"] for m in json.load(open(man_path))["materials"]}
    check("kit: linked material skipped when flag is off", "Lib_Metal" not in names and "Kit_Local_Paint" in names
          and any("linked material(s) skipped" in l for l in hp.operators.LAST_RESULT["lines"]), str(sorted(names)))
    s.export_linked_materials = True
    check("Export House disabled in kit mode", not bpy.ops.house_pipeline.export_house.poll())
    r = bpy.ops.house_pipeline.export_kit()
    man = hp.manifest.read_json(os.path.join(OUT, "Kit", "Exterior", "Kit_Manifest.json"))
    names = [m["name"] for m in man["modules"]] if man else []
    check("kit export: one FBX per module + Kit_Manifest.json", r == {"FINISHED"} and names == ["Wall_Brick_120", "Wall_Brick_240"]
          and all(os.path.isfile(os.path.join(OUT, "Kit", "Exterior", n + ".fbx")) for n in names)
          and man["source"] == "kit_exterior" and man["modules"][0]["category"] == "exterior", str(names))
    mats = {m["name"]: m for m in json.load(open(man_path))["materials"]}
    lib = mats.get("Lib_Metal", {})
    check("kit with flag on: linked library material exported, sourceFile = library, texture copied",
          lib.get("sourceFile") == "Material_Library.blend" and lib.get("metallic") == 1.0
          and lib.get("baseColorMap", {}).get("file") == "Textures/Lib_Metal_color.png"
          and os.path.isfile(os.path.join(OUT, "MaterialSource", "Textures", "Lib_Metal_color.png")), str(lib)[:200])
    kit_path = os.path.join(OUT, "Kit_Exterior.blend")
    bpy.ops.wm.save_as_mainfile(filepath=kit_path)
    check("mode auto-detected from file name", hp.common.detect_mode_from_filename() == C.MODE_KIT_EXTERIOR)
    return kit_path


def test_linked_kit(kit_path):
    h = build_house("TL")
    with bpy.data.libraries.load(kit_path, link=True) as (src, dst):
        dst.collections = ["Wall_Brick_240"]
    kit_coll = dst.collections[0]
    inst = instance(kit_coll, h["ext"], "K_1", (1, 1, 0), (0, 0, 30))
    report, modules, layout = validate.validate_house(bpy.context.scene, "TL")
    check("linked kit instance accepted", report.ok, str(report.lines()[:3]))
    s = bpy.context.scene.house_pipeline
    s.export_root_override = OUT
    bpy.ops.house_pipeline.export_house()
    lm = hp.manifest.read_json(os.path.join(OUT, "Houses", "TL", "Layout", "TL_Layout_Manifest.json"))
    src = {m["name"]: m["source"] for m in lm["modules"]}
    check("layout manifest marks kit vs unique", src.get("Wall_Brick_240") == "kit" and src.get("TL_Arrow") == "unique", str(src))
    check("kit modules are not exported with the house", not os.path.exists(os.path.join(OUT, "Houses", "TL", "Unique", "Wall_Brick_240.fbx")))
    import json
    lib = {m["name"]: m for m in json.load(open(os.path.join(OUT, "MaterialSource", "Material_Manifest.json")))["materials"]}.get("Lib_Metal", {})
    check("house export leaves library material entry untouched", lib.get("sourceFile") == "Material_Library.blend", str(lib)[:120])

    # a house that uses a linked material directly on a unique module: skipped, never exported by houses
    with bpy.data.libraries.load(os.path.join(OUT, "Material_Library.blend"), link=True) as (src, dst):
        dst.materials = ["Lib_Metal"]
    h_lod = bpy.data.collections["TL_Arrow"].objects["TL_Arrow_LOD0"]
    h_lod.data.materials.append(dst.materials[0])
    s.export_linked_materials = True     # flag must be ignored in house mode
    bpy.ops.house_pipeline.export_house()
    check("house export skips linked materials even with the flag on",
          any("linked material(s) skipped" in l and "Lib_Metal" in l for l in hp.operators.LAST_RESULT["lines"]))


def expect(code, mutate, house="TH", warning=False):
    h = build_house(house)
    try:
        mutate(h)
        report, _, _ = validate.validate_house(bpy.context.scene, house)
        codes = {c for c, _ in (report.warnings if warning else report.errors)}
        check(f"validation {'warning' if warning else 'error'} {code}", code in codes, str(report.lines()[:3]))
    except Exception:
        check(f"validation {code}", False, traceback.format_exc(limit=2))


def test_validation():
    build_house()
    report, _, _ = validate.validate_house(bpy.context.scene, "TH")
    check("valid test house has no errors and no warnings", report.ok and not report.warnings, str(report.lines()[:5]))

    def rename(h):
        h["arrow"].name = "TH_Bad Name"
    expect("E_NAME", rename)
    expect("E_NO_LOD0", lambda h: bpy.data.objects.remove(h["arrow"].objects["TH_Arrow_LOD0"]))
    expect("E_OBJECT_NAME", lambda h: h["arrow"].objects.link(bpy.data.objects.new("Foo", arrow_mesh("Foo"))))
    expect("E_FOREIGN_OBJECT", lambda h: h["arrow"].objects.link(bpy.data.objects.new("TH_Arrow_LOD5", None)))

    def unapplied(h):
        h["arrow"].objects["TH_Arrow_LOD1"].rotation_euler.z = 0.3
    expect("E_UNAPPLIED", unapplied)

    def neg_mesh(h):
        h["arrow"].objects["TH_Arrow_LOD0"].scale.x = -1
    expect("E_NEGATIVE_SCALE", neg_mesh)

    def neg_inst(h):
        bpy.data.objects["A_plain"].scale.y = -1
    expect("E_NEGATIVE_SCALE", neg_inst)

    def parented(h):
        h["arrow"].objects["TH_Arrow_LOD1"].parent = h["arrow"].objects["TH_Arrow_LOD0"]
    expect("E_PARENT", parented)
    expect("E_NESTED", lambda h: instance(h["shelf"], h["arrow"], "nested"))
    expect("E_NESTED", lambda h: child(h["arrow"], "Sub"))

    def no_prefix(h):
        c = make_module("Lamp_A", h["mods"], lods=(0,), category="exterior")
        instance(c, h["ext"], "L_1")
    expect("E_UNIQUE_PREFIX", no_prefix)

    def unknown(h):
        c = make_module("TH_Loose", bpy.context.scene.collection, lods=(0,), category="exterior", unique=False)
        instance(c, h["ext"], "U_1")
    expect("E_UNKNOWN_MODULE", unknown)
    expect("E_NO_ROOT", lambda h: setattr(h["root"], "name", "Other"))
    expect("E_LAYOUT", lambda h: h["root"].children.unlink(h["inte"]))
    expect("E_LAYOUT", lambda h: h["r1"].children.unlink(h["p1"]))
    expect("E_LAYOUT", lambda h: instance(h["arrow"], h["inte"], "in_interior"))
    expect("E_LAYOUT", lambda h: setattr(h["r1"], "name", "Kitchen"))
    expect("E_LAYOUT", lambda h: child(h["root"], "Garden", "folder"))
    expect("E_LOOSE_OBJECT", lambda h: h["ext"].objects.link(bpy.data.objects.new("Loose", arrow_mesh("Loose"))))
    expect("E_MULTI_LINK", lambda h: h["f2"].objects.link(bpy.data.objects["A_plain"]))

    def lod_shift(h):
        o = h["arrow"].objects["TH_Arrow_LOD1"]
        o.location.x += 0.5
    expect("W_LOD_BOUNDS", lod_shift, warning=True)

    def add_lod(idx):
        def mutate(h):
            name = f"TH_Arrow_LOD{idx}"
            o = bpy.data.objects.new(name, arrow_mesh(name, 1.0))
            o.location = h["arrow"].instance_offset
            h["arrow"].objects.link(o)
        return mutate
    expect("W_LOD_GAP", add_lod(4), warning=True)
    expect("W_LOD_COUNT", add_lod(8), warning=True)
    expect("W_NONUNIFORM", lambda h: setattr(bpy.data.objects["A_plain"], "scale", (1, 2, 1)), warning=True)

    def no_detail(h):
        del h["shelf"]["detail"]
    expect("W_NO_DETAIL", no_detail, warning=True)

    def no_category(h):
        del h["arrow"]["category"]
    expect("W_NO_CATEGORY", no_category, warning=True)

    def appended(h):
        make_module("Wall_Brick_240", bpy.context.scene.collection, lods=(0,), unique=False)
    expect("W_APPENDED", appended, warning=True)
    expect("W_GROUP_MISSING", lambda h: child(h["ext"], "NoGroup"), warning=True)

    def light(h):
        h["ext"].objects.link(bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN")))
    expect("W_IGNORED", light, warning=True)
    expect("W_UNITS", lambda h: setattr(bpy.context.scene.unit_settings, "scale_length", 0.01), warning=True)

    # export is blocked by errors and writes nothing
    h = build_house("TB")
    neg_mesh_b = h["arrow"]
    neg_mesh_b.objects["TB_Arrow_LOD0"].scale.x = -1
    bpy.context.scene.house_pipeline.export_root_override = OUT
    try:
        r = bpy.ops.house_pipeline.export_house()
    except RuntimeError:   # operators that report an ERROR raise when called from Python
        r = {"CANCELLED"}
    check("export aborted on validation error, nothing written", r == {"CANCELLED"} and not os.path.exists(os.path.join(OUT, "Houses", "TB")))


def make_image(path, color, colorspace="sRGB"):
    img = bpy.data.images.new(os.path.basename(path), 8, 8)
    img.pixels = list(color) * 64
    img.filepath_raw = path
    img.file_format = "PNG"
    img.save()
    img.colorspace_settings.name = colorspace
    return img


def make_material(name, tex_dir):
    """Principled with base color map, roughness via Separate Color (G), normal map, mapping, unlinked AO, emission."""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    coord = nt.nodes.new("ShaderNodeTexCoord")
    mapping = nt.nodes.new("ShaderNodeMapping")
    mapping.inputs["Scale"].default_value = (2.0, 3.0, 1.0)
    mapping.inputs["Location"].default_value = (0.25, 0.5, 0.0)
    nt.links.new(coord.outputs["UV"], mapping.inputs["Vector"])
    reroute = nt.nodes.new("NodeReroute")
    nt.links.new(mapping.outputs["Vector"], reroute.inputs[0])

    def tex(fname, color, cs):
        n = nt.nodes.new("ShaderNodeTexImage")
        n.image = make_image(os.path.join(tex_dir, fname), color, cs)
        nt.links.new(reroute.outputs[0], n.inputs["Vector"])
        return n
    col = tex(f"{name}_color.png", (1, 0, 0, 1), "sRGB")
    nt.links.new(col.outputs["Color"], bsdf.inputs["Base Color"])
    orm = tex(f"{name}_orm.png", (1, 0.5, 0, 1), "Non-Color")
    sep = nt.nodes.new("ShaderNodeSeparateColor")
    nt.links.new(orm.outputs["Color"], sep.inputs[0])
    nt.links.new(sep.outputs["Green"], bsdf.inputs["Roughness"])
    nrm = tex(f"{name}_normal.png", (0.5, 0.5, 1, 1), "Non-Color")
    nm = nt.nodes.new("ShaderNodeNormalMap")
    nm.inputs["Strength"].default_value = 0.7
    nt.links.new(nrm.outputs["Color"], nm.inputs["Color"])
    nt.links.new(nm.outputs["Normal"], bsdf.inputs["Normal"])
    tex(f"{name}_AO.png", (0.8, 0.8, 0.8, 1), "Non-Color")      # deliberately not connected
    bsdf.inputs["Emission Color"].default_value = (0.0, 0.5, 1.0, 1.0)
    bsdf.inputs["Emission Strength"].default_value = 2.0
    bsdf.inputs["Metallic"].default_value = 0.25
    return mat


def test_materials():
    import json
    h = build_house("TM")
    tex_dir = os.path.join(OUT, "_blend_textures")
    os.makedirs(tex_dir, exist_ok=True)
    mat = make_material("TM_Brick", tex_dir)
    mat.use_backface_culling = True
    glass = bpy.data.materials.new("TM_Glass")
    glass.use_nodes = True
    gb = next(n for n in glass.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    gb.inputs["Alpha"].default_value = 0.4
    gb.inputs["Base Color"].default_value = (0.5, 0.5, 0.5, 1)
    glass.surface_render_method = "BLENDED"
    glass.use_backface_culling = False
    lod0 = h["arrow"].objects["TM_Arrow_LOD0"]
    lod0.data.materials.append(mat)
    lod0.data.materials.append(glass)
    h["shelf"].objects["TM_Shelf_LOD0"].data.materials.append(mat)   # shared material, exported once
    s = bpy.context.scene.house_pipeline
    s.export_root_override = OUT
    r = bpy.ops.house_pipeline.export_house()
    lines = " ".join(hp.operators.LAST_RESULT["lines"])
    man_path = os.path.join(OUT, "MaterialSource", "Material_Manifest.json")
    man = json.load(open(man_path))
    e = {m["name"]: m for m in man["materials"]}
    check("material export runs with house export", r == {"FINISHED"} and set(e) >= {"TM_Brick", "TM_Glass"}, lines[:200])
    b = e.get("TM_Brick", {})
    check("base color map sRGB, constant color neutral", b.get("baseColorMap", {}).get("colorSpace") == "sRGB" and b.get("baseColor") == [1.0, 1.0, 1.0, 1.0], str(b.get("baseColorMap")))
    check("roughness from Separate Color channel G, linear", b.get("roughnessMap", {}).get("channel") == "G" and b["roughnessMap"]["colorSpace"] == "Linear", str(b.get("roughnessMap")))
    check("normal map with strength and OpenGL convention", b.get("normalStrength") == 0.7 and b.get("normalMap", {}).get("convention") == "OpenGL", str(b.get("normalMap")))
    check("unconnected AO texture detected as occlusion map", b.get("occlusionMap", {}).get("file", "").endswith("TM_Brick_AO.png"))
    check("UV tiling/offset from Mapping node", b.get("tiling") == [2.0, 3.0] and b.get("offset") == [0.25, 0.5], f"{b.get('tiling')} {b.get('offset')}")
    check("emission linear color and strength", b.get("emissionColor") == [0.0, 0.5, 1.0] and b.get("emissionStrength") == 2.0)
    check("constant metallic kept, single-sided with backface culling", b.get("metallic") == 0.25 and b.get("doubleSided") is False)
    g = e.get("TM_Glass", {})
    srgb_half = round(1.055 * 0.5 ** (1 / 2.4) - 0.055, 5)
    check("transparent glass: surface, alpha, sRGB-encoded color, double-sided without culling",
          g.get("surface") == "transparent" and g.get("alpha") == 0.4 and abs(g["baseColor"][0] - srgb_half) < 1e-4 and g.get("doubleSided") is True, str(g))
    tex_files = sorted(os.listdir(os.path.join(OUT, "MaterialSource", "Textures")))
    check("textures copied, AO included", "TM_Brick_color.png" in tex_files and "TM_Brick_AO.png" in tex_files and len([t for t in man["textures"] if t["file"].startswith("Textures/TM_")]) == 4, str(tex_files))

    mt = os.path.getmtime(man_path)
    tex_m = {f: os.path.getmtime(os.path.join(OUT, "MaterialSource", "Textures", f)) for f in tex_files}
    bpy.ops.house_pipeline.export_house()
    lines = " ".join(hp.operators.LAST_RESULT["lines"])
    check("second export: manifest and textures untouched", os.path.getmtime(man_path) == mt and "0 changed, 0 texture file(s) written" in lines
          and all(os.path.getmtime(os.path.join(OUT, "MaterialSource", "Textures", f)) == t for f, t in tex_m.items()), lines[-300:])
    gb.inputs["Alpha"].default_value = 0.3
    bpy.ops.house_pipeline.export_house()
    lines = " ".join(hp.operators.LAST_RESULT["lines"])
    check("changed material is reported as changed", "1 changed" in lines, lines[-200:])

    # material warnings
    report = hp.common.Report()
    bad = bpy.data.materials.new("Material.001")
    bad.use_nodes = True
    bb = next(n for n in bad.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    ramp = bad.node_tree.nodes.new("ShaderNodeValToRGB")
    bad.node_tree.links.new(ramp.outputs["Color"], bb.inputs["Base Color"])
    t = bad.node_tree.nodes.new("ShaderNodeTexImage")
    t.image = bpy.data.images.load(os.path.join(tex_dir, "TM_Brick_color.png"), check_existing=False)
    t.image.filepath = os.path.join(tex_dir, "missing.png")
    bad.node_tree.links.new(t.outputs["Color"], bb.inputs["Roughness"])
    t2 = bad.node_tree.nodes.new("ShaderNodeTexImage")    # existing data texture flagged sRGB
    t2.image = make_image(os.path.join(tex_dir, "TM_metal_srgb.png"), (1, 1, 1, 1), "sRGB")
    bad.node_tree.links.new(t2.outputs["Color"], bb.inputs["Metallic"])
    lod0.data.materials.append(bad)
    lod0.data.materials.append(None)
    hp.export_materials.validate_materials(hp.validate.validate_house(bpy.context.scene, "TM")[1], report)
    codes = report.codes()
    for code in ("W_MAT_NAME", "W_MAT_UNSUPPORTED", "W_MAT_TEXTURE_MISSING", "W_MAT_COLORSPACE", "W_MAT_EMPTY_SLOT"):
        check(f"material warning {code}", code in codes, str([l for l in report.lines() if "MAT" in l][:4]))


def main():
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT)
    hp.register()
    try:
        for t in (test_house_export, test_door_export, test_validation, test_materials):
            try:
                t()
            except Exception:
                check(t.__name__, False, traceback.format_exc())
        try:
            test_linked_kit(test_kit_export())
        except Exception:
            check("kit tests", False, traceback.format_exc())
    finally:
        hp.unregister()
    failed = [r for r in RESULTS if not r[1]]
    print(f"\nRESULT: {len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    for n, _, d in failed:
        print(f"  FAILED: {n}: {d}")


main()
