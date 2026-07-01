from __future__ import annotations

import json
import os

import bpy
from mathutils import Matrix

from .core import build_export_output_path, build_metadata, find_addon_preferences, write_metadata_sidecar


def _find_bound_armature(obj: bpy.types.Object) -> bpy.types.Object | None:
    if obj.parent is not None and obj.parent.type == "ARMATURE":
        return obj.parent

    for modifier in obj.modifiers:
        if modifier.type == "ARMATURE" and modifier.object is not None:
            return modifier.object

    return None


def _prepare_export_duplicates(
    context: bpy.types.Context,
    mesh_obj: bpy.types.Object,
    armature_obj: bpy.types.Object | None,
) -> tuple[bpy.types.Object, bpy.types.Object | None, list[bpy.types.Object]]:
    temp_objects: list[bpy.types.Object] = []

    mesh_copy = mesh_obj.copy()
    mesh_copy.data = mesh_obj.data.copy()
    context.scene.collection.objects.link(mesh_copy)
    temp_objects.append(mesh_copy)

    armature_copy: bpy.types.Object | None = None
    if armature_obj is not None:
        armature_copy = armature_obj.copy()
        armature_copy.data = armature_obj.data.copy()
        context.scene.collection.objects.link(armature_copy)
        temp_objects.append(armature_copy)

        if mesh_obj.parent == armature_obj:
            matrix_world = mesh_copy.matrix_world.copy()
            mesh_copy.parent = armature_copy
            mesh_copy.parent_type = "OBJECT"
            mesh_copy.parent_bone = ""
            mesh_copy.matrix_world = matrix_world

        for modifier in mesh_copy.modifiers:
            if modifier.type == "ARMATURE":
                modifier.object = armature_copy

    bpy.ops.object.select_all(action="DESELECT")
    mesh_copy.select_set(True)
    if armature_copy is not None:
        armature_copy.select_set(True)

    context.view_layer.objects.active = mesh_copy
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)

    return mesh_copy, armature_copy, temp_objects


class CROWD_OT_ExportAssets(bpy.types.Operator):
    bl_idname = "crowd_diversity.export_assets"
    bl_label = "Export Selected Assets"
    bl_description = "Export the selected rigged garment meshes as USD files and write metadata sidecars"

    def execute(self, context: bpy.types.Context) -> set[str]:
        prefs = find_addon_preferences(context, __package__ or __name__)
        if prefs is None:
            self.report({"ERROR"}, "Add-on preferences are unavailable. Re-enable the extension.")
            return {"CANCELLED"}

        library_root = prefs.library_root
        if not library_root:
            self.report({"ERROR"}, "Choose a library root before exporting.")
            return {"CANCELLED"}

        os.makedirs(library_root, exist_ok=True)

        objects = [obj for obj in context.selected_objects if obj.type == "MESH"]
        if not objects:
            self.report({"ERROR"}, "Select one or more mesh objects to export.")
            return {"CANCELLED"}

        for obj in objects:
            category = obj.crowd_diversity_category
            export_path = build_export_output_path(library_root, category, obj.name)
            export_dir = os.path.dirname(export_path)
            os.makedirs(export_dir, exist_ok=True)

            armature = _find_bound_armature(obj)
            mesh_export_obj = None
            armature_export_obj = None
            temp_objects: list[bpy.types.Object] = []

            try:
                mesh_export_obj, armature_export_obj, temp_objects = _prepare_export_duplicates(context, obj, armature)

                bpy.ops.object.select_all(action="DESELECT")
                mesh_export_obj.select_set(True)
                if armature_export_obj is not None:
                    armature_export_obj.select_set(True)
                context.view_layer.objects.active = mesh_export_obj
                bpy.ops.wm.usd_export(
                    filepath=export_path,
                    check_existing=False,
                    selected_objects_only=True,
                )
            finally:
                for temp_obj in temp_objects:
                    if temp_obj.name in bpy.data.objects:
                        bpy.data.objects.remove(temp_obj, do_unlink=True)

            metadata = build_metadata(
                category=category,
                object_name=obj.name,
                source_file=bpy.data.filepath,
            )
            write_metadata_sidecar(export_path, metadata)
            self.report({"INFO"}, f"Exported {obj.name} to {export_path}")

        # Clear all selections in the viewport after batch export completes.
        bpy.ops.object.select_all(action="DESELECT")
        context.view_layer.objects.active = None

        # Open the output folder in the host OS file explorer.
        try:
            bpy.ops.wm.path_open(filepath=library_root)
        except Exception:
            self.report({"WARNING"}, "Export finished, but opening Library Root failed.")

        return {"FINISHED"}


class CROWD_OT_RunFitCheck(bpy.types.Operator):
    bl_idname = "crowd_diversity.run_fit_check"
    bl_label = "Run Fit Check"
    bl_description = "Pose the original rig(s) for quick clipping review and optionally restore the original pose"

    _ORIGINAL_POSE_KEY = "crowd_diversity_original_pose"

    def execute(self, context: bpy.types.Context) -> set[str]:
        objects = [obj for obj in context.selected_objects if obj.type == "MESH"]
        if not objects:
            self.report({"ERROR"}, "Select one or more mesh objects to fit-check.")
            return {"CANCELLED"}

        armatures: list[bpy.types.Object] = []
        seen_armatures: set[str] = set()
        for obj in objects:
            armature = self._find_armature_for_object(context, obj)
            if armature is None:
                self.report({"WARNING"}, f"No armature found for {obj.name}; skipping.")
                continue

            if armature.name in seen_armatures:
                continue

            seen_armatures.add(armature.name)
            armatures.append(armature)

        if not armatures:
            self.report({"ERROR"}, "No armatures were found from selected meshes.")
            return {"CANCELLED"}

        pose_name = context.scene.crowd_diversity_fit_check_pose
        for armature in armatures:
            if pose_name != "original":
                self._capture_original_pose(armature)
            self._apply_pose(context, armature, pose_name)

        self.report({"INFO"}, f"Applied {pose_name} to {len(armatures)} armature(s).")

        return {"FINISHED"}

    def _find_armature_for_object(self, context: bpy.types.Context, obj: bpy.types.Object) -> bpy.types.Object | None:
        if obj.parent is not None and obj.parent.type == "ARMATURE":
            return obj.parent

        for modifier in obj.modifiers:
            if modifier.type == "ARMATURE" and modifier.object is not None:
                return modifier.object

        return None

    def _capture_original_pose(self, armature: bpy.types.Object) -> None:
        if self._ORIGINAL_POSE_KEY in armature:
            return

        pose_data: dict[str, list[float]] = {}
        for bone in armature.pose.bones:
            pose_data[bone.name] = [value for row in bone.matrix_basis for value in row]

        armature[self._ORIGINAL_POSE_KEY] = json.dumps(pose_data)

    def _restore_original_pose(self, armature: bpy.types.Object) -> bool:
        if self._ORIGINAL_POSE_KEY not in armature:
            return False

        raw_data = armature.get(self._ORIGINAL_POSE_KEY)
        if not isinstance(raw_data, str):
            return False

        try:
            pose_data = json.loads(raw_data)
        except json.JSONDecodeError:
            return False

        for bone in armature.pose.bones:
            values = pose_data.get(bone.name)
            if not values or len(values) != 16:
                continue

            rows = [values[0:4], values[4:8], values[8:12], values[12:16]]
            bone.matrix_basis = Matrix(rows)

        return True

    def _apply_pose(self, context: bpy.types.Context, armature: bpy.types.Object, pose_name: str) -> None:
        bpy.ops.object.select_all(action="DESELECT")
        armature.select_set(True)
        context.view_layer.objects.active = armature
        bpy.ops.object.mode_set(mode="POSE")

        if pose_name == "original":
            restored = self._restore_original_pose(armature)
            if not restored:
                for bone in armature.pose.bones:
                    bone.rotation_euler.zero()
            bpy.ops.object.mode_set(mode="OBJECT")
            return

        for bone in armature.pose.bones:
            bone.rotation_euler.zero()

        if pose_name == "a_pose":
            for bone in armature.pose.bones:
                name = bone.name.lower()
                if "upper_arm" in name or "shoulder" in name:
                    if "left" in name or ".l" in name or name.endswith("l"):
                        bone.rotation_euler = (0.0, 0.0, 0.45)
                    elif "right" in name or ".r" in name or name.endswith("r"):
                        bone.rotation_euler = (0.0, 0.0, -0.45)
                elif "spine" in name:
                    bone.rotation_euler = (0.15, 0.0, 0.0)
        elif pose_name == "t_pose":
            for bone in armature.pose.bones:
                name = bone.name.lower()
                if "upper_arm" in name or "shoulder" in name:
                    if "left" in name or ".l" in name or name.endswith("l"):
                        bone.rotation_euler = (0.0, 0.0, 0.9)
                    elif "right" in name or ".r" in name or name.endswith("r"):
                        bone.rotation_euler = (0.0, 0.0, -0.9)

        bpy.ops.object.mode_set(mode="OBJECT")
