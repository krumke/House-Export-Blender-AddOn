"""Shared constants, naming rules, report container and scene discovery helpers.

The rules implemented here follow the pipeline contract (section 0) with the
project decisions made for HoF:
- Layout groups are identified by the collection property ``group``
  (exterior / interior / room / structure / props / folder), not by name,
  because Blender collection names must be unique (e.g. ``Structure_EG``).
- Unique modules live in ``_Modules_<House>`` and/or carry ``unique = True``.
- Houses are exported in world coordinates.
"""

import re
from dataclasses import dataclass, field

import bpy
from mathutils import Matrix

MODULE_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")
LOD_RE = re.compile(r"^(?P<module>.+)_LOD(?P<idx>\d+)$")
ROOM_RE = re.compile(r"^Room_(?P<storey>[A-Za-z0-9]+)_(?P<name>[A-Za-z0-9_]+)$")
COL_SUFFIX = "_COL"

GROUP_EXTERIOR = "exterior"
GROUP_INTERIOR = "interior"
GROUP_ROOM = "room"
GROUP_STRUCTURE = "structure"
GROUP_PROPS = "props"
GROUP_FOLDER = "folder"
LAYOUT_GROUPS = {GROUP_EXTERIOR, GROUP_INTERIOR, GROUP_ROOM, GROUP_STRUCTURE, GROUP_PROPS, GROUP_FOLDER}

CATEGORIES = {"exterior", "interior"}
DETAILS = {"structure", "large", "small"}
DEFAULT_DETAIL = {"exterior": "structure", "interior": "large"}
BEHAVIORS = {"door"}

MODE_KIT_EXTERIOR = "KIT_EXTERIOR"
MODE_KIT_INTERIOR = "KIT_INTERIOR"
MODE_HOUSE = "HOUSE"

SOURCE_UNIQUE = "unique"
SOURCE_KIT = "kit"

TOL = 1e-5


# --------------------------------------------------------------------------- report
@dataclass
class Report:
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    infos: list = field(default_factory=list)

    def error(self, code, msg):
        self.errors.append((code, msg))

    def warn(self, code, msg):
        self.warnings.append((code, msg))

    def info(self, msg):
        self.infos.append(("", msg))

    @property
    def ok(self):
        return not self.errors

    def codes(self):
        return {c for c, _ in self.errors} | {c for c, _ in self.warnings}

    def lines(self):
        out = [f"ERROR   [{c}] {m}" for c, m in self.errors]
        out += [f"WARNING [{c}] {m}" for c, m in self.warnings]
        out += [f"INFO    {m}" for _, m in self.infos]
        return out


# --------------------------------------------------------------------------- data
@dataclass
class ModuleInfo:
    name: str
    collection: object
    source: str                      # SOURCE_UNIQUE / SOURCE_KIT
    category: str = "exterior"
    detail: str = "structure"
    behavior: str = ""               # "" (static) or one of BEHAVIORS
    lods: dict = field(default_factory=dict)   # idx -> object
    collider: object = None

    @property
    def offset(self):
        return self.collection.instance_offset

    @property
    def objects(self):
        objs = [self.lods[i] for i in sorted(self.lods)]
        if self.collider:
            objs.append(self.collider)
        return objs


# --------------------------------------------------------------------------- helpers
def strip_dup_suffix(name):
    return re.sub(r"\.\d{3,}$", "", name)


def is_identity_3x3(m, tol=TOL):
    m3 = m.to_3x3()
    ident = Matrix.Identity(3)
    return all(abs(m3[i][j] - ident[i][j]) <= tol for i in range(3) for j in range(3))


def is_uniform_scale(m, tol=1e-4):
    s = m.to_scale()
    return abs(s.x - s.y) <= tol * max(1.0, abs(s.x)) and abs(s.y - s.z) <= tol * max(1.0, abs(s.y))


def collection_group(coll):
    """Layout group of a collection: explicit ``group`` property, else inferred from the name."""
    g = coll.get("group")
    if isinstance(g, str) and g in LAYOUT_GROUPS:
        return g
    n = coll.name
    if n == "Exterior":
        return GROUP_EXTERIOR
    if n == "Interior":
        return GROUP_INTERIOR
    if n.startswith("Room_"):
        return GROUP_ROOM
    if n.startswith("Structure"):
        return GROUP_STRUCTURE
    if n.startswith("Props"):
        return GROUP_PROPS
    return None


def scene_collections(scene):
    """All collections reachable from the scene's master collection."""
    out, stack = [], list(scene.collection.children)
    while stack:
        c = stack.pop()
        if c not in out:
            out.append(c)
            stack.extend(c.children)
    return out


def detect_mode_from_filename():
    fn = bpy.path.basename(bpy.data.filepath).lower()
    if "kit_exterior" in fn:
        return MODE_KIT_EXTERIOR
    if "kit_interior" in fn:
        return MODE_KIT_INTERIOR
    return MODE_HOUSE


def detect_house_root(scene):
    """First top-level collection that has Exterior/Interior group children."""
    for c in scene.collection.children:
        groups = {collection_group(ch) for ch in c.children}
        if GROUP_EXTERIOR in groups or GROUP_INTERIOR in groups:
            return c
    return None


def modules_root_name(house):
    return f"_Modules_{house}"


def gather_module(coll, source, category=None, report=None):
    """Build ModuleInfo for a module collection (no validation of naming here)."""
    cat = category or coll.get("category")
    if cat not in CATEGORIES:
        cat = None
    det = coll.get("detail")
    m = ModuleInfo(name=coll.name, collection=coll, source=source, category=cat or "exterior")
    m.detail = det if det in DETAILS else DEFAULT_DETAIL[m.category]
    beh = coll.get("behavior")
    m.behavior = beh if beh in BEHAVIORS else ""
    for o in coll.objects:
        lm = LOD_RE.match(o.name)
        if lm and lm.group("module") == coll.name and o.type == "MESH":
            m.lods[int(lm.group("idx"))] = o
        elif o.name == coll.name + COL_SUFFIX and o.type == "MESH":
            m.collider = o
    m._category_explicit = cat is not None
    m._detail_explicit = det in DETAILS
    m._behavior_raw = beh
    return m


def kit_module_collections(scene):
    """Kit file: every local collection that directly contains objects is a module."""
    return [c for c in scene_collections(scene) if c.library is None and len(c.objects) > 0]


def house_unique_collections(scene, house):
    """House file: collections below ``_Modules_<house>`` with objects, plus any collection flagged unique."""
    found = []
    root = bpy.data.collections.get(modules_root_name(house))
    if root is not None:
        stack = list(root.children)
        while stack:
            c = stack.pop()
            if len(c.objects) > 0:
                found.append(c)
            stack.extend(c.children)
    for c in bpy.data.collections:
        if c.library is None and c.get("unique") and c not in found:
            found.append(c)
    return sorted(found, key=lambda c: c.name)
