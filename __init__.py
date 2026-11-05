"""House Pipeline Export: modular house assets from Blender to Unity.

Exports kit / unique modules (one FBX per module with LODs) and per-house
layouts (FBX with empties only) plus JSON manifests, following the
pipeline contract, and house models for the iPhone app (house-model/1).
See README.md.
"""

bl_info = {
    "name": "House Pipeline Export",
    "author": "Campus Westend",
    "version": (2, 0, 0),   # 2.0.0-alpha, see blender_manifest.toml
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > House Export",
    "description": "Export modular houses (modules + layout) for the Unity house pipeline",
    "category": "Import-Export",
}

if "bpy" in locals():
    import importlib
    for _m in (common, validate, hashing, manifest, fbx_io, export_modules, export_layout, export_house_model, export_materials, operators, ui):  # noqa: F821
        importlib.reload(_m)

import bpy

from . import common, validate, hashing, manifest, fbx_io, export_modules, export_layout, export_house_model, export_materials, operators, ui

_classes = ui.classes + operators.classes


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.house_pipeline = bpy.props.PointerProperty(type=ui.HousePipelineSettings)


def unregister():
    del bpy.types.Scene.house_pipeline
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
