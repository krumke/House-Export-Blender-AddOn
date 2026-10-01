"""Settings, add-on preferences and the sidebar panel (N-panel, tab 'House Export')."""

import bpy

from . import common as C
from . import operators as ops


class HousePipelinePreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    export_root: bpy.props.StringProperty(
        name="Export folder",
        description="Path to the Unity project folder Assets/HousePipeline/",
        subtype="DIR_PATH",
    )

    def draw(self, context):
        self.layout.prop(self, "export_root")


class HousePipelineSettings(bpy.types.PropertyGroup):
    mode: bpy.props.EnumProperty(
        name="Mode",
        items=[
            ("AUTO", "Auto (from file name)", "Kit_Exterior / Kit_Interior in the file name, otherwise House"),
            (C.MODE_KIT_EXTERIOR, "Kit Exterior", ""),
            (C.MODE_KIT_INTERIOR, "Kit Interior", ""),
            (C.MODE_HOUSE, "House", ""),
        ],
        default="AUTO",
    )
    house_name: bpy.props.StringProperty(
        name="House",
        description="House name = root collection. Empty: detected automatically",
    )
    export_root_override: bpy.props.StringProperty(
        name="Folder override",
        description="Optional per-file export folder; empty uses the add-on preferences",
        subtype="DIR_PATH",
    )
    export_materials: bpy.props.BoolProperty(
        name="Export Materials",
        description="Write Material_Manifest.json and copy textures to MaterialSource/ (Unity builds the materials)",
        default=True,
    )
    export_linked_materials: bpy.props.BoolProperty(
        name="Include Linked Materials",
        description="Kit files only: also export materials linked from another .blend (e.g. a shared material "
                    "library). House exports never export linked materials",
        default=True,
    )
    force_export: bpy.props.BoolProperty(
        name="Force Export",
        description="Rewrite module FBX files even if their geometry hash is unchanged",
        default=False,
    )


class HOUSEPIPE_PT_panel(bpy.types.Panel):
    bl_label = "House Export"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "House Export"

    def draw(self, context):
        layout = self.layout
        s = context.scene.house_pipeline
        prefs = ops.addon_prefs(context)

        col = layout.column(align=True)
        if prefs:
            col.prop(prefs, "export_root", text="Folder")
        col.prop(s, "export_root_override", text="Override")
        root = ops.export_root(context)
        col.label(text=root or "No export folder set", icon="FILE_FOLDER" if root else "ERROR")

        layout.prop(s, "mode")
        mode = ops.effective_mode(context)
        if s.mode == "AUTO":
            layout.label(text=f"Detected: {mode}", icon="INFO")
        if mode == C.MODE_HOUSE:
            layout.prop(s, "house_name", placeholder=ops.effective_house(context) or "House")
        row = layout.row(align=True)
        row.prop(s, "export_materials")
        row.prop(s, "force_export")
        if mode != C.MODE_HOUSE:
            sub = layout.row()
            sub.enabled = s.export_materials
            sub.prop(s, "export_linked_materials")

        row = layout.row(align=True)
        row.scale_y = 1.3
        row.operator("house_pipeline.validate", icon="CHECKMARK")
        if mode == C.MODE_HOUSE:
            row.operator("house_pipeline.export_house", icon="EXPORT")
        else:
            row.operator("house_pipeline.export_kit", icon="EXPORT")

        res = ops.LAST_RESULT
        if res["title"]:
            box = layout.box()
            box.label(text=f"{res['title']}: {res['errors']} error(s), {res['warnings']} warning(s)",
                      icon="ERROR" if res["errors"] else ("INFO" if not res["warnings"] else "QUESTION"))
            for line in res["lines"][:40]:
                icon = "CANCEL" if line.startswith("ERROR") else "ERROR" if line.startswith("WARNING") else "DOT"
                box.label(text=line.split(" ", 1)[1].strip() if " " in line else line, icon=icon)
            if len(res["lines"]) > 40:
                box.label(text=f"... {len(res['lines']) - 40} more lines in the system console")


classes = (HousePipelinePreferences, HousePipelineSettings, HOUSEPIPE_PT_panel)
