"""Operators: Validate, Export Kit, Export House."""

import os
import time

import bpy

from . import common as C
from . import export_layout, export_materials, export_modules, validate

# Last result, shown in the panel. Kept in memory only so the .blend is not modified.
LAST_RESULT = {"title": "", "lines": [], "errors": 0, "warnings": 0}


def addon_prefs(context):
    pkg = __package__
    addon = context.preferences.addons.get(pkg)
    return addon.preferences if addon else None


def export_root(context):
    settings = context.scene.house_pipeline
    if settings.export_root_override:
        return bpy.path.abspath(settings.export_root_override)
    prefs = addon_prefs(context)
    if prefs and prefs.export_root:
        return bpy.path.abspath(prefs.export_root)
    return ""


def effective_mode(context):
    m = context.scene.house_pipeline.mode
    return C.detect_mode_from_filename() if m == "AUTO" else m


def effective_house(context):
    name = context.scene.house_pipeline.house_name.strip()
    if name:
        return name
    root = C.detect_house_root(context.scene)
    return root.name if root else ""


def _publish(title, report, extra=()):
    lines = report.lines() + list(extra)
    LAST_RESULT.update(title=title, lines=lines, errors=len(report.errors), warnings=len(report.warnings))
    print(f"[HousePipeline] {title}")
    for line in lines:
        print(f"[HousePipeline]   {line}")


def _finish(op, title, report, extra=()):
    _publish(title, report, extra)
    if report.errors:
        op.report({"ERROR"}, f"{title}: {len(report.errors)} error(s), see House Export panel / console")
        return {"CANCELLED"}
    level = {"WARNING"} if report.warnings else {"INFO"}
    op.report(level, f"{title}: OK, {len(report.warnings)} warning(s)")
    return {"FINISHED"}


def run_validation(context):
    mode = effective_mode(context)
    if mode == C.MODE_HOUSE:
        house = effective_house(context)
        report, modules, layout = validate.validate_house(context.scene, house)
    else:
        house, layout = "", None
        report, modules = validate.validate_kit(context.scene, mode)
    if context.scene.house_pipeline.export_materials:
        export_materials.validate_materials(modules, report, include_linked_materials(context, mode))
    return mode, house, report, modules, layout


def include_linked_materials(context, mode):
    """Linked materials (e.g. from a shared material library) are only exported by kit files, and only if enabled."""
    return mode != C.MODE_HOUSE and context.scene.house_pipeline.export_linked_materials


def _export_materials(context, modules, root, report):
    if not context.scene.house_pipeline.export_materials:
        return
    sub = C.Report()
    n, changed, written = export_materials.export_materials(
        modules, root, bpy.path.basename(bpy.data.filepath), context.scene.house_pipeline.force_export, sub,
        include_linked=include_linked_materials(context, effective_mode(context)))
    # validation already reported the per-material findings; only add what is new
    for lst, src in ((report.warnings, sub.warnings), (report.infos, sub.infos)):
        for item in src:
            if item not in lst:
                lst.append(item)
    report.info(f"Materials: {n} in manifest, {len(changed)} changed, {written} texture file(s) written")


class HOUSEPIPE_OT_validate(bpy.types.Operator):
    bl_idname = "house_pipeline.validate"
    bl_label = "Validate"
    bl_description = "Check the file against the pipeline contract without exporting"

    def execute(self, context):
        mode, house, report, modules, layout = run_validation(context)
        extra = [f"INFO    Mode {mode}" + (f", house '{house}'" if house else "") + f", {len(modules)} module(s)"]
        return _finish(self, "Validate", report, extra)


class HOUSEPIPE_OT_export_kit(bpy.types.Operator):
    bl_idname = "house_pipeline.export_kit"
    bl_label = "Export Kit"
    bl_description = "Validate and export every kit module as its own FBX plus Kit_Manifest.json"

    @classmethod
    def poll(cls, context):
        return effective_mode(context) in (C.MODE_KIT_EXTERIOR, C.MODE_KIT_INTERIOR)

    def execute(self, context):
        root = export_root(context)
        mode, _, report, modules, _ = run_validation(context)
        if not root:
            report.error("E_PATH", "Export folder (Assets/HousePipeline) is not set")
        if report.errors:
            return _finish(self, "Export Kit", report)
        t = time.time()
        out_dir, man = export_modules.kit_paths(root, mode)
        meta = dict(source="kit_exterior" if mode == C.MODE_KIT_EXTERIOR else "kit_interior",
                    blendFile=bpy.path.basename(bpy.data.filepath))
        exported, skipped = export_modules.export_modules(
            context.scene, modules, out_dir, man, meta, context.scene.house_pipeline.force_export, report)
        report.info(f"Modules exported: {len(exported)}, skipped (unchanged): {len(skipped)}  -> {out_dir}  ({time.time() - t:.1f}s)")
        _export_materials(context, modules, root, report)
        return _finish(self, "Export Kit", report)


class HOUSEPIPE_OT_export_house(bpy.types.Operator):
    bl_idname = "house_pipeline.export_house"
    bl_label = "Export House"
    bl_description = "Validate, export the house's unique modules and its layout FBX + manifests"

    @classmethod
    def poll(cls, context):
        return effective_mode(context) == C.MODE_HOUSE

    def execute(self, context):
        root = export_root(context)
        mode, house, report, modules, layout = run_validation(context)
        if not root:
            report.error("E_PATH", "Export folder (Assets/HousePipeline) is not set")
        if not house:
            report.error("E_NO_ROOT", "No house name set and no house root collection found")
        if report.errors:
            return _finish(self, "Export House", report)
        t = time.time()
        out_dir, man = export_modules.unique_paths(root, house)
        meta = dict(source="unique", house=house, blendFile=bpy.path.basename(bpy.data.filepath))
        exported, skipped = export_modules.export_modules(
            context.scene, modules, out_dir, man, meta, context.scene.house_pipeline.force_export, report)
        report.info(f"Unique modules exported: {len(exported)}, skipped (unchanged): {len(skipped)}  -> {out_dir}")
        _export_materials(context, modules, root, report)
        fbx_path, count = export_layout.export_layout(context.scene, house, layout, modules, root, report)
        report.info(f"Layout: {count} instance(s)  -> {fbx_path}  ({time.time() - t:.1f}s)")
        return _finish(self, "Export House", report)


classes = (HOUSEPIPE_OT_validate, HOUSEPIPE_OT_export_kit, HOUSEPIPE_OT_export_house)
