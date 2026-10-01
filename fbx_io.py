"""FBX settings (identical for modules and layouts) and the temporary export scene.

Exports run in a temporary scene that is removed afterwards. Objects of the
user's file are only linked into it for the duration of one export; every
temporary change (location, viewport visibility, names) is restored in
``finally`` blocks by the callers.
"""

from contextlib import contextmanager

import bpy
from bpy_extras.io_utils import axis_conversion

TEMP_SCENE_NAME = "__HousePipeline_Export__"
AXIS_FORWARD = "-Z"
AXIS_UP = "Y"


def axis_matrix():
    """Blender space -> FBX file space for the pipeline axes (4x4)."""
    return axis_conversion(to_forward=AXIS_FORWARD, to_up=AXIS_UP).to_4x4()


def fbx_settings(object_types, bake_space_transform=True):
    """Shared settings. ``bake_space_transform`` is only switched off for the layout,
    which converts its empties itself (see export_layout.py)."""
    return dict(
        use_selection=False,   # the temporary scene contains exactly the objects to export
        use_visible=False,
        use_active_collection=False,
        object_types=set(object_types),
        global_scale=1.0,
        apply_unit_scale=True,
        apply_scale_options="FBX_SCALE_ALL",
        axis_forward=AXIS_FORWARD,
        axis_up=AXIS_UP,
        bake_space_transform=bake_space_transform,
        use_mesh_modifiers=True,
        use_mesh_modifiers_render=True,
        mesh_smooth_type="FACE",
        use_subsurf=False,
        use_mesh_edges=False,
        use_tspace=False,
        use_triangles=False,
        use_custom_props=True,
        add_leaf_bones=False,
        bake_anim=False,
        path_mode="STRIP",
        embed_textures=False,
        batch_mode="OFF",
        use_metadata=True,
    )


def export_scene_contents(filepath, object_types, bake_space_transform=True):
    """Export every object of the current (overridden) temporary view layer."""
    kwargs = fbx_settings(object_types, bake_space_transform)
    available = bpy.ops.export_scene.fbx.get_rna_type().properties.keys()
    missing = [k for k in kwargs if k not in available]
    if missing:
        raise RuntimeError(f"FBX exporter does not support required options: {missing}")
    result = bpy.ops.export_scene.fbx(filepath=filepath, check_existing=False, **kwargs)
    if "FINISHED" not in result:
        raise RuntimeError(f"FBX export failed for {filepath}: {result}")


@contextmanager
def temp_scene(src_scene):
    """Create an empty scene, make it the context scene, remove it afterwards."""
    old = bpy.data.scenes.get(TEMP_SCENE_NAME)
    if old is not None:
        bpy.data.scenes.remove(old)
    tmp = bpy.data.scenes.new(TEMP_SCENE_NAME)
    tmp.unit_settings.system = src_scene.unit_settings.system
    tmp.unit_settings.scale_length = src_scene.unit_settings.scale_length
    try:
        view_layer = tmp.view_layers[0]
        with bpy.context.temp_override(scene=tmp, view_layer=view_layer):
            yield tmp, view_layer
    finally:
        bpy.data.scenes.remove(tmp)
