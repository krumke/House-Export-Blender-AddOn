# House Pipeline Export (Blender → Unity)

Blender add-on (Blender 4.2+, tested with 5.1.2) that exports modular houses for the Unity house pipeline:

- **Modules**: one FBX per module (all LODs + optional collider), origin = the collection's `instance_offset`.
- **Layout**: one FBX per house with **empties only** (hierarchy + module reference + transform).
- **Manifests**: JSON files with hashes, LODs and categories (lists, readable with Unity's `JsonUtility`).
- **Materials**: render-pipeline-neutral description of all materials + copies of the original textures.
  Unity builds the actual materials (URP/HDRP/Built-in) from it.

## Installation

1. *Edit → Preferences → Get Extensions → ⌄ (top right) → Install from Disk…* and pick
   `house_pipeline_export-1.2.1.zip`.
2. In the add-on preferences set **Export folder** to the Unity folder `…/Assets/HousePipeline/`.
3. Sidebar of the 3D viewport (N) → tab **House Export**.

## File structure

### Module (kit or unique)

```
<Module>                  collection, instance_offset = module origin
 ├─ <Module>_LOD0         mesh, required
 ├─ <Module>_LOD1 / _LOD2 optional, may be hidden (hide_viewport / hide_render); exported anyway
 └─ <Module>_COL          optional collider mesh
```

- Mesh objects: rotation and scale applied, no parent, no negative scale.
- Modifiers stay live in the file and are applied **during export** only.
- Collection properties: `category` (`exterior` / `interior`), `detail` (`structure` / `large` / `small`),
  `unique = True` for modules that only exist in this house.
- Names: `A-Z a-z 0-9 _` only. Unique modules start with `<House>_`.

### Kit files

`Kit_Exterior.blend` / `Kit_Interior.blend`: every collection that directly contains objects is a module;
collections that only contain collections are folders. **Export Kit** writes
`Kit/<Exterior|Interior>/<Module>.fbx` and `Kit_Manifest.json`.

### House files

```
_Reference            (excluded; ignored by the export)
_Modules_<House>      (excluded; unique modules, one collection each)
<House>
 ├─ Exterior          group = exterior    instances, sub-folders (group = folder)
 └─ Interior          group = interior
     └─ Room_<Storey>_<Name>      group = room
         ├─ Structure_…           group = structure
         └─ Props_…               group = props
```

- Layout collections are identified by the **`group` property**, not by their name
  (Blender needs unique names, hence `Structure_EG`, `Props_EG`, …). Without the property the
  names `Exterior`, `Interior`, `Room_*`, `Structure*`, `Props*` are recognised.
- Only collection instances may live in the layout. Instances point either to a collection
  **linked** from a kit file or to a unique module.
- The house is exported in **world coordinates** (house root at 0,0,0).

**Export House** writes:

```
Houses/<House>/Unique/<Module>.fbx + <House>_Unique_Manifest.json
Houses/<House>/Layout/<House>_Layout.fbx + <House>_Layout_Manifest.json
```

## Materials

With **Export Materials** (on by default) every export also writes

```
MaterialSource/Material_Manifest.json
MaterialSource/Textures/<original texture files>
```

for the materials used by the exported modules. The manifest is shared by all kit and house files; each
export updates its own materials (`sourceFile`) and keeps the others.

- **House files** export only **local** materials. Linked materials are skipped (listed in the report).
- **Kit files** additionally export materials **linked from another .blend** (e.g. a shared material library)
  when **Include Linked Materials** is on (default on, only shown in kit mode). Their `sourceFile` is the
  library file, so every kit writes identical entries; entries from other files are never removed by an export. Textures and the manifest are only rewritten when their content changes.

Supported shading per material: one Principled BSDF on the active output. Inputs may be constants or image
textures, optionally through Reroute, Separate Color (channel) and, for normals, a Normal Map node. A Mapping
node in front of the textures becomes `tiling` / `offset`. An **unconnected** image texture whose name contains
`ao` / `AmbientOcclusion` / `occlusion` is exported as occlusion map.

| Manifest field | Source in Blender |
|---|---|
| `baseColor` (sRGB, alpha = Alpha) / `baseColorMap` | Base Color (white when a texture is connected) |
| `metallic` / `metallicMap`, `roughness` / `roughnessMap` | Metallic, Roughness (`channel` R/G/B/A if via Separate Color or Alpha) |
| `normalMap`, `normalStrength`, `convention` | Normal Map node (`DirectX` if the image name contains `DX`, else `OpenGL`) |
| `occlusionMap` | unconnected AO texture |
| `emissionColor` (linear), `emissionStrength`, `emissionMap` | Emission Color / Strength |
| `surface` | `transparent` (render method Blended or Alpha < 1), `cutout` (alpha texture), else `opaque` |
| `alpha`, `alphaMap`, `alphaCutoff` | Alpha |
| `doubleSided` | *Material → Settings → Backface Culling* off = double-sided, on = single-sided (same as glTF) |
| `tiling`, `offset` | Mapping node (scale, location) |

Texture entries: `file` (relative to `MaterialSource/`), `hash`, `colorSpace` (`sRGB` for base color / emission,
otherwise `Linear`), optional `channel`, `convention`.

### Double-sided materials and glass

`doubleSided` follows Blender's *Material → Settings → Backface Culling* (same rule as the glTF exporter):
culling **off** = double-sided (Unity: Render Face *Both*), culling **on** = single-sided (*Front*).
Turn culling on for everything that is a closed body; leave it off only where the back of a face must be
visible (single-plane glass, foliage cards, …). Blender's viewport (Material Preview / EEVEE) then shows
the same faces as Unity. In `HoF_Pipeline.blend` culling is on for every material except `glass` and
`Glas_LOD1_opak`.

**Known limitation:** URP Lit renders the back of a double-sided face **without flipping its normal**, so
the back is lit as if it faced away (darker, wrong highlights and reflections). A single-plane pane therefore
looks different from each side, and panes whose normals point in opposite directions look different from
the same view point (check with *Viewport Overlays → Face Orientation*: blue = front, red = back).
glTF importers like glTFast flip the normal on back faces, so a GLB import hides this. Options:

1. **Solidify modifier on the glass objects (recommended):** a thickness of about 0.5–1 cm, modifier stays
   live and is applied by the export. Turn backface culling **on** for the glass material. From each side
   exactly one front face with a correct normal is visible: same look inside and outside, no sorting issues
   within the pane. Raise `alpha` slightly if the glass gets too clear.
2. **Consistent normals only:** point all panes outwards (*Mesh → Normals → Recalculate Outside* / *Flip*).
   All panes then look the same and correct from outside, but uniformly wrong from inside. Smallest change.
3. **Custom Unity shader:** a Shader Graph that flips the normal on back faces (*Is Front Face*). Works with
   single planes, but is a special case outside the generated URP Lit materials (label the material
   `HousePipelineManual` so the pipeline does not overwrite it).

## Panel

| Element | Meaning |
|---|---|
| Folder / Override | export root (`Assets/HousePipeline/`); the override is stored per .blend file |
| Mode | Auto (from the file name: `Kit_Exterior`, `Kit_Interior`, otherwise House) or manual |
| House | house name = root collection, detected automatically if empty |
| Export Materials | write the material manifest and textures (default on) |
| Include Linked Materials | kit mode only: also export materials linked from a material library (default on) |
| Force Export | rewrite module FBX files even if their hash is unchanged |
| Validate / Export | results (errors, warnings, counts) appear in the panel and in the system console |

Module FBX files are only rewritten when the geometry hash changes (positions, normals, UVs, topology,
material slot names of all LODs after modifiers). This avoids unnecessary re-imports in Unity.
The layout FBX is always rewritten. The .blend file is not modified by an export: everything runs in a
temporary scene, and temporary changes are reverted in `finally`.

## Validation codes

Errors abort the export, warnings do not.

| Code | Meaning |
|---|---|
| E_NAME | invalid characters in a module name |
| E_NO_LOD0 | module without `<Module>_LOD0` |
| E_OBJECT_NAME | object in a module not named `<Module>_LOD<n>` / `<Module>_COL` |
| E_FOREIGN_OBJECT | non-mesh object in a module |
| E_UNAPPLIED | rotation/scale of a module mesh not applied |
| E_NEGATIVE_SCALE | negative scale on a module mesh or an instance |
| E_PARENT | module mesh is parented |
| E_NESTED | collection instance or child collection inside a module |
| E_UNIQUE_PREFIX | unique module without `<House>_` prefix |
| E_UNKNOWN_MODULE | instance of a collection that is neither linked from a kit nor unique |
| E_NO_ROOT | house root collection not found |
| E_LAYOUT | Exterior/Interior/Room/Structure/Props structure violated |
| E_LOOSE_OBJECT | geometry in the layout that is not an instance |
| E_MULTI_LINK | instance linked into several layout collections |
| E_PATH | no export folder set |
| W_LOD_BOUNDS | LOD bounds differ from LOD0 (> 5 cm / 5 %) |
| W_NONUNIFORM | instances with non-uniform scale |
| W_NO_DETAIL | interior module without `detail` (uses `large`) |
| W_NO_CATEGORY | unique module without `category` (derived from usage) |
| W_APPENDED | local copy of a kit-looking module (append instead of link) |
| W_GROUP_MISSING | layout sub-collection without `group`, treated as folder |
| W_IGNORED | light/camera/empty in the layout, ignored |
| W_UNITS | scene unit scale ≠ 1.0 |
| W_NAME | layout node exported under another name because the name is taken by linked data |
| W_MAT_NAME | generic material name or name with `.` / spaces (the name is the material's identity in Unity) |
| W_MAT_UNSUPPORTED | node setup not transferable (other shader, ColorRamp, Bump, UV rotation, …); constant value used |
| W_MAT_TEXTURE_MISSING | image file not found; map skipped |
| W_MAT_COLORSPACE | data texture (roughness, metallic, …) set to sRGB in Blender, or color texture not sRGB |
| W_MAT_EMPTY_SLOT | empty material slot on a module mesh |
| W_MAT_CONFLICT | another .blend file exports a material with the same name but different settings, or two different materials share a name |

## Notes for the Unity side (deviations from / additions to the contract)

- **Grouping**: read the `group` property of the layout empties (`exterior`, `interior`, `room`,
  `structure`, `props`, `folder`), not their names. `folder` empties are plain sub-folders of
  `Exterior` (or of `Structure`/`Props`).
- **Instance properties**: `module`, `category`, and for interior instances `room` and `slot`.
- **Unique manifest** is named `<House>_Unique_Manifest.json` (same schema as `Kit_Manifest.json`,
  header has `source: "unique"` and `house`).
- **Layout manifest** additionally contains `coordinateSpace: "world"`, `instanceCount`,
  `modules[]` (`name`, `source` = `kit`/`unique`, `count`) and `rooms[]` (`name`, `storey`).
- **Units**: FBX files declare `UnitScaleFactor = 100` (cm units) with values in metres
  (`FBX_SCALE_ALL`); Unity with *Convert Units* on gives scale 1.
- **Axes**: both module and layout FBX are written in Y-up / -Z forward space. Modules use
  `bake_space_transform`. The layout is written *without* it, because Blender's FBX exporter
  converts nested empties incorrectly with that option (only direct children of the root are
  right). The add-on converts the empties itself, so in the files layout and modules match exactly
  (verified in FBX file space, see tests). Blender's own FBX *importer* adds a root correction per
  file, so re-importing both files into Blender is not a valid check; use Unity or `tests/`.

## Tests

```
blender -b --factory-startup --python tests/run_tests.py -- <empty output folder>
```

74 checks: asymmetric module with 0°/90°/3-axis rotations, scale, nested folders and rooms matches
exactly in FBX space; shifted `instance_offset`; hidden LODs and modifiers; manifests; second export
writes nothing; only changed modules are re-exported; force export; removed modules; the .blend file
is unchanged after export; kit export, mode detection and linked kit modules; every validation rule
triggered by a deliberately broken scene; export aborts on errors without writing files; material export
(maps, channels, normal strength, AO detection, tiling, emission, transparency, sRGB conversion, unchanged
re-export, change detection, every material warning); linked library materials (kit flag on/off, house
exports skip them).

## Building the ZIP

```
blender --command extension build --source-dir . --output-dir ..
```

Uses `[build] paths_exclude_pattern` from `blender_manifest.toml` (tests, `.git`, caches are left out).
Versions are tagged in git (`v1.2.0`, `v1.2.1`, …); bump `version` in `blender_manifest.toml` and
`bl_info` in `__init__.py` together.
