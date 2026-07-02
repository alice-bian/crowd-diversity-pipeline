from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import unreal

# Path to the USD file to import. Update before running the script.
USD_FILE_PATH = "C:/Users/alice/crowd_diversity_library/tops/Jacket.usd"

# Maps Rig ID values (set per-asset in the Blender add-on's Rig IDs panel)
# to canonical UE5 Skeleton asset paths. Add one entry per master rig family.
# Extend this map to enable multi-rig support in a future pipeline iteration.
SKELETON_MAP = {
    "mixamo_v1": "/Game/Body/SkeletalMeshes/SKEL_BaseCharacter_Rig_004.SKEL_BaseCharacter_Rig_004",
}

# Destination root in the Content Browser.
CONTENT_ROOT = "/Game/CrowdDiversity"

# Keep reassignment disabled by default while stabilizing UE5.5 behavior.
# Set to True only after confirming your build does not crash during reassignment.
ENABLE_SKELETON_REASSIGN = False

# Consolidation can be unstable in some UE5.5 builds/projects when called from
# Python on recently imported assets. Keep it opt-in and prefer subsystem APIs.
ENABLE_CONSOLIDATE_FALLBACK = False

# Crash tracing: this file is written step-by-step so you can inspect progress
# even if the Unreal Editor process terminates unexpectedly.
TRACE_LOG_PATH = "C:/Users/Public/crowd_import_trace.log"

# Keep category-to-folder naming aligned with Blender exporter output.
CATEGORY_TO_FOLDER = {
    "character_body": "characters",
    "hair": "hair",
    "top": "tops",
    "bottom": "bottoms",
    "shoes": "shoes",
    "accessory": "accessories",
}


def _log_info(message: str) -> None:
    _append_trace("INFO", message)
    unreal.log(f"[CrowdImport] {message}")


def _log_warning(message: str) -> None:
    _append_trace("WARN", message)
    unreal.log_warning(f"[CrowdImport] {message}")


def _log_error(message: str) -> None:
    _append_trace("ERROR", message)
    unreal.log_error(f"[CrowdImport] {message}")


def _append_trace(level: str, message: str) -> None:
    timestamp = datetime.utcnow().isoformat() + "Z"
    line = f"{timestamp} [{level}] {message}\n"
    try:
        with Path(TRACE_LOG_PATH).open("a", encoding="utf-8") as handle:
            handle.write(line)
    except Exception:
        # Never fail the import because trace logging path is unavailable.
        pass


def _fail(message: str) -> bool:
    _log_error(message)
    return False


def _read_sidecar(usd_path: Path) -> Optional[dict[str, Any]]:
    sidecar_path = usd_path.with_suffix(".json")
    if not sidecar_path.exists():
        _fail(f"Missing sidecar JSON next to USD: {sidecar_path}")
        return None

    try:
        data = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except Exception as exc:
        _fail(f"Malformed sidecar JSON at {sidecar_path}: {exc}")
        return None

    if not isinstance(data, dict):
        _fail(f"Malformed sidecar JSON at {sidecar_path}: root must be an object.")
        return None

    return data


def _get_required_string(metadata: dict[str, Any], key: str) -> Optional[str]:
    value = metadata.get(key)
    if not isinstance(value, str) or not value.strip():
        _fail(f"Sidecar field '{key}' is missing or empty.")
        return None
    return value.strip()


def _category_folder(category: str) -> str:
    return CATEGORY_TO_FOLDER.get(category, category)


def _build_target_path(content_root: str, category: str) -> str:
    folder = _category_folder(category)
    return f"{content_root.rstrip('/')}/{folder}"


def _ensure_content_folder(content_path: str) -> None:
    if not unreal.EditorAssetLibrary.does_directory_exist(content_path):
        unreal.EditorAssetLibrary.make_directory(content_path)


def _is_redirector_asset(asset_path: str) -> bool:
    try:
        asset_data = unreal.EditorAssetLibrary.find_asset_data(asset_path)
    except Exception:
        return False

    if asset_data is None:
        return False

    class_tokens: list[str] = []
    try:
        class_path = asset_data.get_editor_property("asset_class_path")
    except Exception:
        class_path = None

    if class_path is not None:
        if hasattr(class_path, "asset_name"):
            class_tokens.append(str(class_path.asset_name))
        class_tokens.append(str(class_path))

    # Fallback for builds where AssetData class path metadata is incomplete.
    loaded_asset = unreal.EditorAssetLibrary.load_asset(asset_path)
    if loaded_asset is not None:
        try:
            class_tokens.append(str(loaded_asset.get_class().get_name()))
        except Exception:
            pass

    class_blob = " ".join(class_tokens).lower()
    return "redirector" in class_blob


def _cleanup_redirectors(content_path: str) -> int:
    cleaned = 0
    asset_paths = unreal.EditorAssetLibrary.list_assets(content_path, recursive=True, include_folder=False)
    for asset_path in asset_paths:
        if not _is_redirector_asset(asset_path):
            continue

        if unreal.EditorAssetLibrary.delete_asset(asset_path):
            cleaned += 1

    if cleaned > 0:
        _log_warning(f"Deleted {cleaned} stale redirector asset(s) under {content_path}.")
    return cleaned


def _has_redirectors(content_path: str) -> bool:
    asset_paths = unreal.EditorAssetLibrary.list_assets(content_path, recursive=True, include_folder=False)
    return any(_is_redirector_asset(asset_path) for asset_path in asset_paths)


def _find_first_skeletal_mesh(content_path: str):
    asset_paths = unreal.EditorAssetLibrary.list_assets(content_path, recursive=True, include_folder=False)
    for asset_path in asset_paths:
        asset = unreal.EditorAssetLibrary.load_asset(asset_path)
        if asset and isinstance(asset, unreal.SkeletalMesh):
            return asset
    return None


def _load_canonical_skeleton(compatible_rig: str):
    skeleton_path = SKELETON_MAP.get(compatible_rig)
    if not skeleton_path:
        _fail(
            "No skeleton mapping for rig ID '{}'. Add an entry to SKELETON_MAP at the top "
            "of this script, for example: SKELETON_MAP['{}'] = '/Game/YourSkeleton'.".format(
                compatible_rig, compatible_rig
            )
        )
        return None, None

    if not unreal.EditorAssetLibrary.does_asset_exist(skeleton_path):
        _fail(f"Canonical skeleton asset not found at configured path: {skeleton_path}")
        return None, None

    skeleton = unreal.EditorAssetLibrary.load_asset(skeleton_path)
    if skeleton is None:
        _fail(f"Failed to load canonical skeleton asset: {skeleton_path}")
        return None, None

    return skeleton_path, skeleton


def _find_existing_skeletal_mesh(target_path: str, preferred_names: list[str]):
    preferred_lower = {name.lower() for name in preferred_names if name}
    assets_in_folder = unreal.EditorAssetLibrary.list_assets(target_path, recursive=True, include_folder=False)
    for asset_path in assets_in_folder:
        asset = unreal.EditorAssetLibrary.load_asset(asset_path)
        if not asset or not isinstance(asset, unreal.SkeletalMesh):
            continue

        if asset.get_name().lower() in preferred_lower:
            return asset

    return None


def _import_usd_asset(usd_path: str, destination_path: str) -> list[str]:
    # UE Python USD import APIs vary by engine/plugin setup.
    # AssetImportTask is the most stable editor automation path and works when
    # the USD importer registers a factory for .usd in this project.
    task = unreal.AssetImportTask()
    task.set_editor_property("filename", usd_path)
    task.set_editor_property("destination_path", destination_path)
    task.set_editor_property("automated", True)
    task.set_editor_property("replace_existing", False)
    task.set_editor_property("save", True)

    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])

    imported_paths = task.get_editor_property("imported_object_paths") or []
    return list(imported_paths)


def _find_imported_skeletal_mesh(imported_paths: list[str], target_path: str):
    for asset_path in imported_paths:
        asset = unreal.EditorAssetLibrary.load_asset(asset_path)
        if asset and isinstance(asset, unreal.SkeletalMesh):
            return asset

    # Fallback when import does not report object paths reliably.
    assets_in_folder = unreal.EditorAssetLibrary.list_assets(target_path, recursive=True, include_folder=False)
    for asset_path in assets_in_folder:
        asset = unreal.EditorAssetLibrary.load_asset(asset_path)
        if asset and isinstance(asset, unreal.SkeletalMesh):
            return asset

    return None


def _mesh_uses_skeleton(mesh: unreal.SkeletalMesh, skeleton) -> bool:
    current = mesh.get_editor_property("skeleton")
    if current is None:
        return False
    return current.get_path_name() == skeleton.get_path_name()


def _try_subsystem_skeleton_assignment(mesh: unreal.SkeletalMesh, canonical_skeleton) -> bool:
    subsystem = unreal.get_editor_subsystem(unreal.SkeletalMeshEditorSubsystem)
    if subsystem is None:
        _log_warning("SkeletalMeshEditorSubsystem is unavailable in this UE session.")
        return False

    candidate_methods = [
        "assign_skeleton",
        "set_skeletal_mesh_skeleton",
        "set_skeleton",
    ]

    for method_name in candidate_methods:
        if not hasattr(subsystem, method_name):
            continue

        method = getattr(subsystem, method_name)
        try:
            method(mesh, canonical_skeleton)
        except Exception as exc:
            _log_warning(f"{method_name} failed: {exc}")
            continue

        reloaded_mesh = unreal.EditorAssetLibrary.load_asset(mesh.get_path_name())
        if reloaded_mesh is not None and _mesh_uses_skeleton(reloaded_mesh, canonical_skeleton):
            unreal.EditorAssetLibrary.save_loaded_asset(reloaded_mesh)
            _log_info(f"Skeleton reassigned via SkeletalMeshEditorSubsystem.{method_name}.")
            return True

    return False


def _assign_canonical_skeleton(
    mesh: unreal.SkeletalMesh,
    canonical_skeleton,
    target_path: str,
) -> tuple[bool, Optional[str], str]:
    current_skeleton = mesh.get_editor_property("skeleton")
    if current_skeleton is None:
        _fail(f"Imported skeletal mesh has no skeleton reference: {mesh.get_path_name()}")
        return False, None, "none"

    current_path = current_skeleton.get_path_name()
    canonical_path = canonical_skeleton.get_path_name()

    if current_path == canonical_path:
        _log_info("Skeletal mesh already uses canonical skeleton. No reassignment needed.")
        return True, None, "already"

    if not ENABLE_SKELETON_REASSIGN:
        _log_warning(
            "Skeleton reassignment is disabled (ENABLE_SKELETON_REASSIGN=False). "
            "Skipping reassignment to avoid UE crash-prone APIs in this build."
        )
        return True, current_path, "skipped"

    # UE5.5 marks SkeletalMesh.skeleton as read-only in Python, so direct
    # set_editor_property is not valid for reassignment.
    if _try_subsystem_skeleton_assignment(mesh, canonical_skeleton):
        return True, current_path, "subsystem"

    if not ENABLE_CONSOLIDATE_FALLBACK:
        _fail(
            "Could not reassign skeleton with available SkeletalMeshEditorSubsystem APIs. "
            "Set ENABLE_CONSOLIDATE_FALLBACK=True to try EditorAssetLibrary.consolidate_assets "
            "as a fallback in your project."
        )
        return False, current_path, "none"

    if not current_path.startswith(target_path + "/"):
        _fail(
            "Refusing consolidate fallback because duplicate skeleton is outside target folder: "
            f"{current_path}"
        )
        return False, current_path, "none"

    try:
        consolidated = unreal.EditorAssetLibrary.consolidate_assets(canonical_skeleton, [current_skeleton])
    except Exception as exc:
        _fail(
            "Consolidate fallback failed from '{}' to '{}': {}".format(
                current_path, canonical_path, exc
            )
        )
        return False, current_path, "none"

    if not consolidated:
        _fail(
            "Consolidate fallback returned false from '{}' to '{}'.".format(
                current_path, canonical_path
            )
        )
        return False, current_path, "none"

    _log_warning("Skeleton reassignment used consolidate fallback. Verify editor stability in your UE build.")
    return True, current_path, "consolidate"


def run_import(usd_file_path: str) -> bool:
    _append_trace("INFO", "--- Crowd import run started ---")
    _append_trace("INFO", f"Config USD_FILE_PATH={usd_file_path}")
    _append_trace("INFO", f"Config CONTENT_ROOT={CONTENT_ROOT}")
    _append_trace("INFO", f"Config ENABLE_SKELETON_REASSIGN={ENABLE_SKELETON_REASSIGN}")
    _append_trace("INFO", f"Config ENABLE_CONSOLIDATE_FALLBACK={ENABLE_CONSOLIDATE_FALLBACK}")

    usd_path = Path(usd_file_path)
    if not usd_path.exists():
        return _fail(f"USD file not found: {usd_path}")

    metadata = _read_sidecar(usd_path)
    if metadata is None:
        return False

    category = _get_required_string(metadata, "category")
    source_asset_name = _get_required_string(metadata, "source_asset_name")
    compatible_rig = _get_required_string(metadata, "compatible_rig")
    if category is None or source_asset_name is None or compatible_rig is None:
        return False

    target_path = _build_target_path(CONTENT_ROOT, category)
    _log_info(f"Resolved target content path: {target_path}")
    _ensure_content_folder(target_path)
    _cleanup_redirectors(target_path)

    canonical_skeleton_path, canonical_skeleton = _load_canonical_skeleton(compatible_rig)
    if canonical_skeleton is None or canonical_skeleton_path is None:
        return False

    usd_stem = usd_path.stem
    preferred_names = [source_asset_name, usd_stem]
    mesh = _find_existing_skeletal_mesh(target_path, preferred_names)

    # UE USD imports often generate a stable subfolder named after the USD stem.
    # If that folder already exists, re-import can trigger fatal rename collisions
    # when redirectors are present, so prefer reuse/abort over re-import.
    expected_import_root = f"{target_path}/{usd_stem}"
    if unreal.EditorAssetLibrary.does_directory_exist(expected_import_root):
        _cleanup_redirectors(expected_import_root)

        if _has_redirectors(expected_import_root):
            return _fail(
                "Import folder already exists and still contains redirectors after cleanup: "
                f"{expected_import_root}. Aborting to avoid known UE rename crash. "
                "Manually fix redirectors or delete the folder, then rerun."
            )

        if mesh is None:
            mesh = _find_first_skeletal_mesh(expected_import_root)

        if mesh is None:
            return _fail(
                "Import folder already exists but no SkeletalMesh was found: "
                f"{expected_import_root}. Aborting to avoid re-import crash."
            )

    imported_paths: list[str] = []
    if mesh is not None:
        _log_warning(f"Skeletal mesh already exists, skipping import: {mesh.get_path_name()}")
    else:
        imported_paths = _import_usd_asset(str(usd_path), target_path)
        mesh = _find_imported_skeletal_mesh(imported_paths, target_path)

    if mesh is None:
        _fail(
            "Import did not produce a SkeletalMesh in '{}'. If your UE build does not "
            "route USD through AssetImportTask, import once manually and rerun this script "
            "to perform skeleton reassignment/cleanup.".format(target_path)
        )
        _log_warning(
            "Alternative API path: project/plugin builds that expose USD stage import options "
            "(for example UsdStageImportOptions + USD importer subsystem/factory) can be used "
            "instead of AssetImportTask."
        )
        return False

    _log_info("Beginning skeleton compatibility stage.")
    reassigned, orphan_skeleton_path, reassign_method = _assign_canonical_skeleton(mesh, canonical_skeleton, target_path)
    if not reassigned:
        _log_warning("Skeleton reassignment failed; duplicate skeleton was left in place.")
        return False

    unreal.EditorAssetLibrary.save_loaded_asset(mesh)

    _log_info("Import summary:")
    _log_info(f"  USD: {usd_path}")
    _log_info(f"  Metadata category: {category}")
    _log_info(f"  Content destination: {target_path}")
    _log_info(f"  SkeletalMesh: {mesh.get_path_name()}")
    _log_info(f"  Canonical skeleton: {canonical_skeleton_path}")
    _log_info(f"  Reassignment method: {reassign_method}")
    if orphan_skeleton_path:
        _log_info(f"  Duplicate skeleton detected: {orphan_skeleton_path}")
        if reassign_method == "consolidate":
            _log_info("  Duplicate cleanup: handled by asset consolidation")
        elif reassign_method == "skipped":
            _log_info("  Duplicate cleanup: skipped (skeleton reassignment disabled)")
        else:
            _log_info("  Duplicate cleanup: not attempted automatically")
    else:
        _log_info("  Duplicate cleanup: not needed")

    _append_trace("INFO", "--- Crowd import run finished successfully ---")
    return True


if __name__ == "__main__":
    run_import(USD_FILE_PATH)
