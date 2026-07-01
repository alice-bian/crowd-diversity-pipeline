from __future__ import annotations

try:
    import bpy
except ImportError:  # pragma: no cover - exercised in plain Python test environments
    bpy = None

from .core import CATEGORY_LABELS, get_addon_id

if bpy is not None:
    from .operators import CROWD_OT_ExportAssets, CROWD_OT_RunFitCheck
    from .ui import CROWD_PT_Panel, CROWD_Preferences


ADDON_ID = get_addon_id(__package__ or __name__)


def _get_addon_name() -> str:
    return ADDON_ID


def _get_library_root(context) -> str:
    prefs = context.preferences.addons.get(ADDON_ID)
    if prefs is None:
        return ""
    return prefs.preferences.library_root


def register() -> None:
    if bpy is None:
        return

    for cls in (
        CROWD_Preferences,
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


def unregister() -> None:
    if bpy is None:
        return

    del bpy.types.Object.crowd_diversity_category
    del bpy.types.Scene.crowd_diversity_fit_check_pose

    for cls in reversed((
        CROWD_PT_Panel,
        CROWD_OT_RunFitCheck,
        CROWD_OT_ExportAssets,
        CROWD_Preferences,
    )):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
