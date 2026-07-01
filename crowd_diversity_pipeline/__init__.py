from __future__ import annotations

try:
    import bpy
except ImportError:  # pragma: no cover - exercised in plain Python test environments
    bpy = None

from .core import CATEGORY_LABELS, get_addon_id

if bpy is not None:
    from .operators import CROWD_OT_AddRig, CROWD_OT_ExportAssets, CROWD_OT_RemoveRig, CROWD_OT_RunFitCheck
    from .ui import CROWD_PT_Panel, CROWD_Preferences


ADDON_ID = get_addon_id(__package__ or __name__)


def _get_addon_name() -> str:
    return ADDON_ID


def _get_library_root(context) -> str:
    prefs = context.preferences.addons.get(ADDON_ID)
    if prefs is None:
        return ""
    return prefs.preferences.library_root


if bpy is not None:
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


def register() -> None:
    if bpy is None:
        return

    for cls in (
        CrowdRigIDItem,
        CROWD_Preferences,
        CROWD_OT_AddRig,
        CROWD_OT_RemoveRig,
        CROWD_OT_ExportAssets,
        CROWD_OT_RunFitCheck,
        CROWD_PT_Panel,
    ):
        bpy.utils.register_class(cls)

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

    for scene in bpy.data.scenes:
        _ensure_default_scene_rig(scene)


def unregister() -> None:
    if bpy is None:
        return

    del bpy.types.Object.crowd_diversity_category
    del bpy.types.Object.crowd_diversity_compatible_rig
    del bpy.types.Scene.crowd_diversity_fit_check_pose
    del bpy.types.Scene.crowd_diversity_rigs_index
    del bpy.types.Scene.crowd_diversity_rigs

    for cls in reversed((
        CROWD_PT_Panel,
        CROWD_OT_RunFitCheck,
        CROWD_OT_ExportAssets,
        CROWD_OT_RemoveRig,
        CROWD_OT_AddRig,
        CROWD_Preferences,
        CrowdRigIDItem,
    )):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
