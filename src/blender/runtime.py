from __future__ import annotations

import bpy
from bpy.app.handlers import persistent

from .core import CATEGORY_LABELS


class CrowdRigIDItem(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(name="Rig ID", default="mixamo_v1")


_RIG_ENUM_ITEMS_CACHE: list[tuple[str, str, str]] = []


def _rig_enum_items(_self, context):
    # Keep a module-level cache because Blender can retain raw pointers to enum strings.
    # This avoids the known dynamic EnumProperty item lifetime/GC instability.
    global _RIG_ENUM_ITEMS_CACHE

    items: list[tuple[str, str, str]] = []
    if context is not None and getattr(context, "scene", None) is not None:
        for rig in context.scene.crowd_diversity_rigs:
            rig_name = rig.name.strip()
            if rig_name:
                items.append((rig_name, rig_name, ""))

    if not items:
        items.append(("", "(No rigs defined)", ""))

    _RIG_ENUM_ITEMS_CACHE = items
    return _RIG_ENUM_ITEMS_CACHE


def _ensure_default_scene_rig(scene: bpy.types.Scene) -> None:
    if len(scene.crowd_diversity_rigs) == 0:
        item = scene.crowd_diversity_rigs.add()
        item.name = "mixamo_v1"
        scene.crowd_diversity_rigs_index = 0


def _initialize_default_scene_rigs() -> None:
    scenes = getattr(getattr(bpy, "data", None), "scenes", None)
    if scenes is None:
        return

    for scene in scenes:
        _ensure_default_scene_rig(scene)


@persistent
def _on_load_post(_dummy) -> None:
    _initialize_default_scene_rigs()


def _schedule_default_scene_rig_init() -> None:
    def _timer_callback():
        _initialize_default_scene_rigs()
        return None

    try:
        bpy.app.timers.register(_timer_callback, first_interval=0.0)
    except Exception:
        pass


def register() -> None:
    bpy.types.Object.crowd_diversity_category = bpy.props.EnumProperty(
        name="Category",
        description="Type for this selected garment, hairstyle, accessory, or character body",
        items=[(key, label, "") for key, label in CATEGORY_LABELS.items()],
        default="top",
    )
    bpy.types.Scene.crowd_diversity_fit_check_pose = bpy.props.EnumProperty(
        name="Fit Check Pose",
        description="Pose to apply for the temporary fit check",
        items=[
            ("original", "Original", "Restore the saved pre-fit-check pose"),
            ("neutral", "Neutral", ""),
            ("a_pose", "A-Pose", ""),
            ("t_pose", "T-Pose", ""),
        ],
        default="neutral",
    )
    bpy.types.Scene.crowd_diversity_rigs = bpy.props.CollectionProperty(type=CrowdRigIDItem)
    bpy.types.Scene.crowd_diversity_rigs_index = bpy.props.IntProperty(default=0, min=0)
    bpy.types.Object.crowd_diversity_compatible_rig = bpy.props.EnumProperty(
        name="Rig ID",
        description="Rig compatibility identifier for this object's metadata sidecar",
        # Keep rig compatibility per object to match per-object category assignment.
        items=_rig_enum_items,
    )

    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)

    _schedule_default_scene_rig_init()


def unregister() -> None:
    del bpy.types.Object.crowd_diversity_category
    del bpy.types.Object.crowd_diversity_compatible_rig
    del bpy.types.Scene.crowd_diversity_fit_check_pose
    del bpy.types.Scene.crowd_diversity_rigs_index
    del bpy.types.Scene.crowd_diversity_rigs

    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
