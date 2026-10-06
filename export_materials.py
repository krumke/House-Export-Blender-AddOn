"""Material export: render-pipeline-neutral description of every material used by
exported modules, plus a copy of the original texture files.

Output (shared by all kit and house files):
    <root>/MaterialSource/Material_Manifest.json
    <root>/MaterialSource/Textures/<file>

Supported shading: one Principled BSDF on the active material output.
Inputs may be constant values or image textures, optionally through Reroute,
Separate Color (channel selection) and, for normals, a Normal Map node.
UV transform comes from a Mapping node in front of the image textures.
Unconnected image textures whose name marks them as ambient occlusion are
exported as occlusion map. Anything else is reported and replaced by the
socket's constant value.

By default only local materials are exported; materials linked from another
.blend belong to that file. Kit files can opt in (``include_linked``) to also
export materials linked from a shared material library; their ``sourceFile``
is then the library file, so every kit writes identical entries for them.

Double-sided like the glTF exporter: backface culling off in Blender
(Material > Settings) means double-sided, on means single-sided.

Colors: ``baseColor`` is sRGB-encoded (what Unity's material color expects),
``emissionColor`` is linear (HDR). Texture colour space reflects the role:
base color / emission sRGB, all other maps linear.
"""

import hashlib
import json
import os
import re

import bpy

from . import manifest

AO_RE = re.compile(r"(^|[^a-z])(ao|ambientocclusion|ambient_occlusion|occlusion)([^a-z]|$)", re.IGNORECASE)
GENERIC_NAME_RE = re.compile(r"^(Material|Mat)(\.\d+)?$")
DATA_SOCKETS = {"Metallic": "metallicMap", "Roughness": "roughnessMap"}
EPS = 1e-6


def _linear_to_srgb(c):
    c = max(0.0, min(1.0, c))
    return c * 12.92 if c <= 0.0031308 else 1.055 * (c ** (1.0 / 2.4)) - 0.055


def _r(v, d=5):
    return round(float(v), d) + 0.0


def _upstream(socket):
    """Follow a link through reroutes. Returns (node, from_socket) or (None, None)."""
    while socket.is_linked:
        link = socket.links[0]
        node = link.from_node
        if node.type == "REROUTE":
            socket = node.inputs[0]
            continue
        return node, link.from_socket
    return None, None


def _file_hash(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class _TextureRef:
    def __init__(self, image, role):
        self.image = image
        self.role = role


def _texture_source(image):
    """(bytes-or-None, src_path-or-None, file name). Packed images are written from memory."""
    if image.source not in {"FILE"}:
        return None, None, None
    if image.packed_file is not None:
        name = os.path.basename(bpy.path.abspath(image.filepath)) or f"{image.name}.png"
        return bytes(image.packed_file.data), None, name
    path = bpy.path.abspath(image.filepath, library=image.library)
    return None, os.path.normpath(path), os.path.basename(path)


def _mapping_of(tex_node, mat_name, report):
    node, _ = _upstream(tex_node.inputs["Vector"]) if tex_node.inputs["Vector"].is_linked else (None, None)
    if node is None or node.type == "TEX_COORD":
        return (1.0, 1.0), (0.0, 0.0)
    if node.type == "UVMAP":
        return (1.0, 1.0), (0.0, 0.0)
    if node.type == "MAPPING":
        if node.vector_type not in {"POINT", "TEXTURE"}:
            report.warn("W_MAT_UNSUPPORTED", f"Material '{mat_name}': Mapping node type {node.vector_type} not supported, using default UVs")
            return (1.0, 1.0), (0.0, 0.0)
        rot = node.inputs["Rotation"].default_value
        if any(abs(a) > EPS for a in rot):
            report.warn("W_MAT_UNSUPPORTED", f"Material '{mat_name}': UV rotation in Mapping node is ignored")
        sc = node.inputs["Scale"].default_value
        loc = node.inputs["Location"].default_value
        if node.vector_type == "TEXTURE":   # inverse transform
            sx, sy = (1.0 / sc[0] if sc[0] else 1.0), (1.0 / sc[1] if sc[1] else 1.0)
            return (sx, sy), (-loc[0] * sx, -loc[1] * sy)
        return (sc[0], sc[1]), (loc[0], loc[1])
    report.warn("W_MAT_UNSUPPORTED", f"Material '{mat_name}': texture coordinates from {node.type} not supported, using default UVs")
    return (1.0, 1.0), (0.0, 0.0)


def describe(mat, report):
    """Return (entry dict without textures resolved, list of (key, TextureRef, channel, tex_node))."""
    name = mat.name
    # Same rule as the glTF exporter: no backface culling = double-sided.
    entry = dict(name=name, surface="opaque", doubleSided=not mat.use_backface_culling)
    maps = []

    if GENERIC_NAME_RE.match(name) or not re.match(r"^[A-Za-z0-9_\-]+$", name):
        report.warn("W_MAT_NAME", f"Material '{name}': rename it (generic name or '.', spaces) - the name is its identity in Unity")

    nt = mat.node_tree if mat.use_nodes else None
    out = None
    if nt:
        out = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output), None) \
            or next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None)
    bsdf = None
    if out and out.inputs["Surface"].is_linked:
        node, _ = _upstream(out.inputs["Surface"])
        if node is not None and node.type == "BSDF_PRINCIPLED":
            bsdf = node
    if bsdf is None:
        col = mat.diffuse_color
        report.warn("W_MAT_UNSUPPORTED", f"Material '{name}': surface is not a single Principled BSDF, exporting viewport color only")
        entry.update(baseColor=[_r(_linear_to_srgb(col[0])), _r(_linear_to_srgb(col[1])), _r(_linear_to_srgb(col[2])), _r(col[3])],
                     metallic=_r(mat.metallic), roughness=_r(mat.roughness), alpha=_r(col[3]))
        return entry, maps

    def tex_from(socket, key, allow_channel=True):
        node, from_sock = _upstream(socket)
        if node is None:
            return False
        channel = None
        if allow_channel and node.type in {"SEPARATE_COLOR", "SEPRGB", "SEPARATE_RGB"}:
            channel = {"Red": "R", "Green": "G", "Blue": "B", "R": "R", "G": "G", "B": "B"}.get(from_sock.name)
            node, from_sock = _upstream(node.inputs[0])
            if node is None:
                return False
        if node.type == "TEX_IMAGE" and node.image is not None:
            if from_sock.name == "Alpha":
                channel = "A"
            maps.append((key, _TextureRef(node.image, key), channel, node))
            return True
        report.warn("W_MAT_UNSUPPORTED", f"Material '{name}': input '{socket.name}' is driven by {node.type}, using its constant value")
        return False

    inp = bsdf.inputs
    bc = inp["Base Color"].default_value
    alpha = inp["Alpha"].default_value
    entry["baseColor"] = [_r(_linear_to_srgb(bc[0])), _r(_linear_to_srgb(bc[1])), _r(_linear_to_srgb(bc[2])), _r(alpha)]
    if inp["Base Color"].is_linked:
        if tex_from(inp["Base Color"], "baseColorMap", allow_channel=False):
            entry["baseColor"][:3] = [1.0, 1.0, 1.0]
    entry["metallic"] = _r(inp["Metallic"].default_value)
    entry["roughness"] = _r(inp["Roughness"].default_value)
    for sock, key in DATA_SOCKETS.items():
        if inp[sock].is_linked:
            tex_from(inp[sock], key)
    entry["alpha"] = _r(alpha)
    alpha_linked = inp["Alpha"].is_linked and tex_from(inp["Alpha"], "alphaMap")

    if inp["Normal"].is_linked:
        node, _ = _upstream(inp["Normal"])
        if node is not None and node.type == "NORMAL_MAP":
            if node.space != "TANGENT":
                report.warn("W_MAT_UNSUPPORTED", f"Material '{name}': normal map space {node.space} not supported")
            entry["normalStrength"] = _r(node.inputs["Strength"].default_value)
            tex_from(node.inputs["Color"], "normalMap", allow_channel=False)
        elif node is not None:
            report.warn("W_MAT_UNSUPPORTED", f"Material '{name}': normal input driven by {node.type} (only Normal Map node is supported)")

    em_sock = inp.get("Emission Color") or inp.get("Emission")
    em_strength = inp["Emission Strength"].default_value if "Emission Strength" in inp else 1.0
    if em_sock is not None:
        ec = em_sock.default_value
        has_em_tex = em_sock.is_linked and tex_from(em_sock, "emissionMap", allow_channel=False)
        if has_em_tex or (em_strength > EPS and max(ec[0], ec[1], ec[2]) > EPS):
            entry["emissionColor"] = [1.0, 1.0, 1.0] if has_em_tex else [_r(ec[0]), _r(ec[1]), _r(ec[2])]
            entry["emissionStrength"] = _r(em_strength)

    # unconnected AO textures
    used_nodes = {m[3] for m in maps}
    ao = [n for n in nt.nodes if n.type == "TEX_IMAGE" and n.image is not None and n not in used_nodes
          and not any(o.is_linked for o in n.outputs)
          and (AO_RE.search(n.image.name) or AO_RE.search(os.path.basename(n.image.filepath)) or AO_RE.search(n.label or ""))]
    if len(ao) == 1:
        maps.append(("occlusionMap", _TextureRef(ao[0].image, "occlusionMap"), None, ao[0]))
        report.info(f"Material '{name}': unconnected '{ao[0].image.name}' used as occlusion map")
    elif len(ao) > 1:
        report.warn("W_MAT_UNSUPPORTED", f"Material '{name}': several unconnected AO textures, none used")

    # surface type
    method = getattr(mat, "surface_render_method", None)
    blended = method == "BLENDED" if method else getattr(mat, "blend_method", "OPAQUE") in {"BLEND"}
    if blended or alpha < 1.0 - EPS:
        entry["surface"] = "transparent"
    elif alpha_linked:
        entry["surface"] = "cutout"
        entry["alphaCutoff"] = 0.5
    if inp.get("Transmission Weight") is not None and inp["Transmission Weight"].default_value > EPS:
        report.warn("W_MAT_UNSUPPORTED", f"Material '{name}': transmission is not transferred, use Alpha for glass")

    # UV transform: from the base color texture, else the first texture
    tiling, offset = (1.0, 1.0), (0.0, 0.0)
    transforms = {m[0]: _mapping_of(m[3], name, report) for m in maps}
    if transforms:
        ref = transforms.get("baseColorMap") or next(iter(transforms.values()))
        tiling, offset = ref
        if any(abs(t[0][i] - tiling[i]) > EPS or abs(t[1][i] - offset[i]) > EPS for t in transforms.values() for i in range(2)):
            report.warn("W_MAT_UNSUPPORTED", f"Material '{name}': textures use different UV transforms, using the one of the base color map")
    entry["tiling"] = [_r(tiling[0]), _r(tiling[1])]
    entry["offset"] = [_r(offset[0]), _r(offset[1])]
    return entry, maps


def material_source(mat, current_file):
    """File that owns a material: the library it is linked from, else the current file."""
    return bpy.path.basename(mat.library.filepath) if mat.library is not None else current_file


def collect_materials(modules, report, include_linked=False):
    mats, skipped = {}, set()
    for m in modules:
        for o in m.objects:
            for i, slot in enumerate(o.material_slots):
                mat = slot.material
                if mat is None:
                    report.warn("W_MAT_EMPTY_SLOT", f"Object '{o.name}' has an empty material slot {i}")
                    continue
                if mat.library is not None and not include_linked:
                    skipped.add(mat.name)
                    continue
                other = mats.get(mat.name)
                if other is not None and other != mat:
                    report.warn("W_MAT_CONFLICT", f"Two different materials are named '{mat.name}' "
                                f"({material_source(other, 'this file')} / {material_source(mat, 'this file')}); only the first is exported")
                    continue
                mats[mat.name] = mat
    if skipped:
        report.info(f"{len(skipped)} linked material(s) skipped (exported by the kit that includes linked materials): {', '.join(sorted(skipped))}")
    return [mats[k] for k in sorted(mats)]


def validate_materials(modules, report, include_linked=False):
    for mat in collect_materials(modules, report, include_linked):
        entry, maps = describe(mat, report)
        for key, ref, channel, node in maps:
            data, src, fname = _texture_source(ref.image)
            if data is None and (src is None or not os.path.isfile(src)):
                report.warn("W_MAT_TEXTURE_MISSING", f"Material '{mat.name}': texture '{ref.image.name}' not found ({src or ref.image.source})")
                continue
            cs = ref.image.colorspace_settings.name
            wants_srgb = key in ("baseColorMap", "emissionMap")
            if wants_srgb and cs != "sRGB":
                report.warn("W_MAT_COLORSPACE", f"Material '{mat.name}': color texture '{ref.image.name}' is '{cs}' in Blender, expected sRGB")
            if not wants_srgb and cs == "sRGB":
                report.warn("W_MAT_COLORSPACE", f"Material '{mat.name}': data texture '{ref.image.name}' ({key}) is sRGB in Blender, set it to Non-Color (exported as linear)")


def export_materials(modules, root, source_file, force, report, include_linked=False):
    """Write textures and merge this file's materials into Material_Manifest.json."""
    base = os.path.join(root, "MaterialSource")
    tex_dir = os.path.join(base, "Textures")
    man_path = os.path.join(base, "Material_Manifest.json")
    os.makedirs(tex_dir, exist_ok=True)
    old = manifest.read_json(man_path) or {}
    old_entries = {e["name"]: e for e in old.get("materials", [])}
    old_textures = {t["file"]: t for t in old.get("textures", [])}
    tex_by_image, textures, written = {}, dict(old_textures), 0

    def place_texture(ref, owner):
        nonlocal written
        if ref.image in tex_by_image:
            return tex_by_image[ref.image]
        data, src, fname = _texture_source(ref.image)
        if data is None and (src is None or not os.path.isfile(src)):
            report.warn("W_MAT_TEXTURE_MISSING", f"Texture '{ref.image.name}' not found ({src or ref.image.source}), map skipped")
            tex_by_image[ref.image] = None
            return None
        digest = hashlib.sha1(data).hexdigest() if data is not None else _file_hash(src)
        stem, ext = os.path.splitext(fname)
        rel = f"Textures/{fname}"
        prev = textures.get(rel)
        if prev and prev["hash"] != digest and prev.get("sourceFile") not in (None, owner):
            rel = f"Textures/{stem}_{digest[:8]}{ext}"      # same file name, other content, other source
        dest = os.path.join(base, rel.replace("/", os.sep))
        if force or not os.path.isfile(dest) or _file_hash(dest) != digest:
            if data is not None:
                with open(dest, "wb") as f:
                    f.write(data)
            else:
                with open(src, "rb") as fi, open(dest, "wb") as fo:
                    fo.write(fi.read())
            written += 1
        textures[rel] = dict(file=rel, hash=digest, sourceFile=owner)
        tex_by_image[ref.image] = (rel, digest)
        return tex_by_image[ref.image]

    entries, changed = [], []
    for mat in collect_materials(modules, report, include_linked):
        owner = material_source(mat, source_file)
        entry, maps = describe(mat, report)
        for key, ref, channel, node in maps:
            placed = place_texture(ref, owner)
            if placed is None:
                continue
            rel, digest = placed
            tex = dict(file=rel, hash=digest, colorSpace="sRGB" if key in ("baseColorMap", "emissionMap") else "Linear")
            if channel:
                tex["channel"] = channel
            if key == "normalMap":
                tex["convention"] = "DirectX" if re.search(r"(^|[^a-z])dx([^a-z]|$)", ref.image.name, re.IGNORECASE) else "OpenGL"
            entry[key] = tex
        entry["sourceFile"] = owner
        h = hashlib.sha1(json.dumps(entry, sort_keys=True).encode()).hexdigest()
        prev = old_entries.get(mat.name)
        if prev and prev.get("sourceFile") not in (None, owner) and prev.get("hash") != h:
            report.warn("W_MAT_CONFLICT", f"Material '{mat.name}' is also exported by '{prev.get('sourceFile')}' with different settings; this export overwrites it")
        entry["hash"] = h
        entry["exported"] = prev["exported"] if prev and prev.get("hash") == h else manifest.now_iso()
        if not prev or prev.get("hash") != h:
            changed.append(mat.name)
        entries.append(entry)

    names = {e["name"] for e in entries}
    for n, e in old_entries.items():
        if n in names:
            continue
        if e.get("sourceFile") == source_file:
            report.info(f"Material '{n}' is no longer used by '{source_file}' and was removed from the manifest")
            changed.append(n)
        else:
            entries.append(e)    # belongs to another file (or a library this kit does not use completely)
    used_tex = {e[k]["file"] for e in entries for k in e if isinstance(e.get(k), dict) and "file" in e[k]}
    tex_list = sorted((t for f, t in textures.items() if f in used_tex), key=lambda t: t["file"])

    data = dict(version=manifest.MANIFEST_VERSION, materials=sorted(entries, key=lambda e: e["name"]), textures=tex_list)
    same = old and {k: old.get(k) for k in data} == data
    if not same:
        data["exported"] = manifest.now_iso()
        manifest.write_json(man_path, data)
    return len(entries), changed, written
