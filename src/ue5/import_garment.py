from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import unreal

# Root folder produced by the Blender exporter.
#
# This mirrors the Blender extension's default Library Root setting
# (`~/crowd_diversity_library`). If your Blender add-on is configured to export
# somewhere else, either edit this value or set the environment variable
# `CROWD_DIVERSITY_LIBRARY_ROOT` before launching UE.
_DEFAULT_LIBRARY_ROOT = "~/crowd_diversity_library"
_LIBRARY_ROOT_ENV_VAR = "CROWD_DIVERSITY_LIBRARY_ROOT"
_LIBRARY_ROOT_ENV_VALUE = os.environ.get(_LIBRARY_ROOT_ENV_VAR)
LIBRARY_ROOT = os.path.normpath(
    os.path.expanduser(_LIBRARY_ROOT_ENV_VALUE or _DEFAULT_LIBRARY_ROOT)
)

# Root path in the UE Content Browser where imported assets are placed.
# Category folders (`characters`, `tops`, `hair`, etc.) are created below this.
CONTENT_ROOT = "/Game"

# If True, a USD is imported only when its mirrored target folder does not
# already contain assets. Existing imported USD folders are skipped.
IMPORT_ONLY_MISSING_USD = True

# Optional canonical skeleton overrides by rig ID.
#
# Leave this empty for the generic flow: character body imports are processed
# first, and their imported skeletons become the canonical skeletons for those
# rig IDs automatically. Populate this only when you want to force a specific
# existing UE skeleton path for a rig ID.
SKELETON_MAP: dict[str, str] = {}

# When True, non-body assets are reassigned onto the canonical skeleton for
# their rig ID after import.
ENABLE_SKELETON_REASSIGN = True

# When True, any garment/hair/accessory that does not end up on the canonical
# skeleton is treated as a hard failure instead of a warning.
REQUIRE_CANONICAL_SKELETON = True

# Consolidation is the fallback when UE5 Python does not expose a working direct
# skeleton reassignment API. This is useful in UE5.5, but it is more invasive
# than subsystem-based reassignment.
ENABLE_CONSOLIDATE_FALLBACK = True

# When True, the script performs best-effort cleanup after orphan skeleton
# deletion: fix up redirectors, save dirty packages, collect garbage, and ask
# the asset registry to rescan the affected folder.
RUN_POST_CLEANUP_MAINTENANCE = True

# Crash/debug trace written outside the project so it survives editor crashes.
# Override with CROWD_IMPORT_TRACE_LOG if you want a custom path.
_TRACE_LOG_ENV_VAR = "CROWD_IMPORT_TRACE_LOG"
_TRACE_LOG_ENV_VALUE = os.environ.get(_TRACE_LOG_ENV_VAR)
TRACE_LOG_PATH = _TRACE_LOG_ENV_VALUE or str(
    Path(tempfile.gettempdir()) / "crowd_import_trace.log"
)

CATEGORY_TO_FOLDER = {
    "character_body": "characters",
    "hair": "hair",
    "top": "tops",
    "bottom": "bottoms",
    "shoes": "shoes",
    "accessory": "accessories",
}


@dataclass(frozen=True)
class AssetRecord:
    usd_path: Path
    metadata: dict[str, Any]
    category: str
    source_asset_name: str
    compatible_rig: str
    usd_stem: str
    target_path: str
    expected_import_root: str


@dataclass(frozen=True)
class ResolveResult:
    mesh: Any | None
    imported_new: bool
    skipped_existing: bool


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
    return f"{content_root.rstrip('/')}/{_category_folder(category)}"


def _build_expected_import_root(target_path: str, usd_stem: str) -> str:
    return f"{target_path}/{usd_stem}"


def _build_target_from_library_structure(library_root: Path, usd_path: Path) -> tuple[str, str]:
    # Mirror the Blender-exported directory structure under CONTENT_ROOT.
    # Example:
    #   C:/.../crowd_diversity_library/tops/Jacket.usd -> /Game/tops + /Game/tops/Jacket
    try:
        rel_parent = usd_path.parent.relative_to(library_root)
    except ValueError:
        rel_parent = Path()

    rel_parent_str = rel_parent.as_posix().strip("/")
    if rel_parent_str:
        target_path = f"{CONTENT_ROOT.rstrip('/')}/{rel_parent_str}"
    else:
        target_path = CONTENT_ROOT.rstrip("/")

    expected_import_root = f"{target_path}/{usd_path.stem}"
    return target_path, expected_import_root


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

    loaded_asset = unreal.EditorAssetLibrary.load_asset(asset_path)
    if loaded_asset is not None:
        try:
            class_tokens.append(str(loaded_asset.get_class().get_name()))
        except Exception:
            pass

    return "redirector" in " ".join(class_tokens).lower()


def _cleanup_redirectors(content_path: str) -> int:
    cleaned = 0
    for asset_path in unreal.EditorAssetLibrary.list_assets(content_path, recursive=True, include_folder=False):
        if _is_redirector_asset(asset_path) and unreal.EditorAssetLibrary.delete_asset(asset_path):
            cleaned += 1

    if cleaned > 0:
        _log_warning(f"Deleted {cleaned} stale redirector asset(s) under {content_path}.")
    return cleaned


def _loaded_redirectors_in_path(content_path: str) -> list[object]:
    redirectors: list[object] = []
    for asset_path in unreal.EditorAssetLibrary.list_assets(content_path, recursive=True, include_folder=False):
        if not _is_redirector_asset(asset_path):
            continue
        loaded = unreal.EditorAssetLibrary.load_asset(asset_path)
        if loaded is not None:
            redirectors.append(loaded)
    return redirectors


def _fixup_redirectors(content_path: str) -> None:
    redirectors = _loaded_redirectors_in_path(content_path)
    if not redirectors:
        return

    asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
    if not hasattr(asset_tools, "fixup_referencers"):
        _log_warning("AssetTools.fixup_referencers is unavailable in this UE build.")
        return

    try:
        asset_tools.fixup_referencers(redirectors, False)
        _log_info(f"Fixup redirectors completed under {content_path}.")
        return
    except TypeError:
        pass
    except Exception as exc:
        _log_warning(f"Fixup redirectors failed under {content_path}: {exc}")
        return

    try:
        delete_mode = getattr(unreal, "ERedirectFixupMode", None)
        if delete_mode is not None and hasattr(delete_mode, "DELETE_FIXED_UP_REDIRECTORS"):
            asset_tools.fixup_referencers(redirectors, False, delete_mode.DELETE_FIXED_UP_REDIRECTORS)
            _log_info(f"Fixup redirectors completed under {content_path}.")
    except Exception as exc:
        _log_warning(f"Fixup redirectors failed under {content_path}: {exc}")


def _save_dirty_packages() -> None:
    saver = getattr(unreal, "EditorLoadingAndSavingUtils", None)
    if saver is None:
        return

    try:
        saver.save_dirty_packages(True, True)
        _log_info("Saved dirty map/content packages.")
        return
    except TypeError:
        pass
    except Exception as exc:
        _log_warning(f"Save dirty packages failed: {exc}")
        return

    try:
        saver.save_dirty_packages()
        _log_info("Saved dirty packages.")
    except Exception as exc:
        _log_warning(f"Save dirty packages failed: {exc}")


def _refresh_asset_registry(content_path: str) -> None:
    helpers = getattr(unreal, "AssetRegistryHelpers", None)
    if helpers is None:
        return

    try:
        registry = helpers.get_asset_registry()
        registry.scan_paths_synchronous([content_path], True, False)
        _log_info(f"Asset registry refreshed for {content_path}.")
    except Exception as exc:
        _log_warning(f"Asset registry refresh failed for {content_path}: {exc}")


def _post_cleanup_maintenance(content_path: str) -> None:
    if not RUN_POST_CLEANUP_MAINTENANCE:
        return
    _fixup_redirectors(content_path)
    _save_dirty_packages()
    try:
        unreal.SystemLibrary.collect_garbage()
        _log_info("Requested garbage collection.")
    except Exception as exc:
        _log_warning(f"Garbage collection request failed: {exc}")
    _refresh_asset_registry(content_path)


def _has_redirectors(content_path: str) -> bool:
    asset_paths = unreal.EditorAssetLibrary.list_assets(content_path, recursive=True, include_folder=False)
    return any(_is_redirector_asset(asset_path) for asset_path in asset_paths)


def _find_first_skeletal_mesh(content_path: str):
    for asset_path in unreal.EditorAssetLibrary.list_assets(content_path, recursive=True, include_folder=False):
        asset = unreal.EditorAssetLibrary.load_asset(asset_path)
        if asset and isinstance(asset, unreal.SkeletalMesh):
            return asset
    return None


def _safe_delete_if_unreferenced(asset_path: str) -> bool:
    if not asset_path:
        return True
    if not unreal.EditorAssetLibrary.does_asset_exist(asset_path):
        _log_info(f"Cleanup skip (already missing): {asset_path}")
        return True

    referencers = unreal.EditorAssetLibrary.find_package_referencers_for_asset(asset_path, load_assets_to_confirm=False)
    referencers = [ref for ref in referencers if ref]
    if referencers:
        _log_warning("Cleanup skip (asset still referenced): {} <- {}".format(asset_path, ", ".join(referencers)))
        return False

    deleted = unreal.EditorAssetLibrary.delete_asset(asset_path)
    if deleted:
        _log_info(f"Deleted unreferenced asset: {asset_path}")
        _post_cleanup_maintenance(asset_path.rsplit("/", 1)[0])
        return True

    _log_warning(f"Delete failed for unreferenced asset: {asset_path}")
    return False


def _load_canonical_skeleton(compatible_rig: str, skeleton_map: dict[str, str]):
    skeleton_path = skeleton_map.get(compatible_rig)
    if not skeleton_path:
        _fail(
            "No canonical skeleton is registered for rig ID '{}'. Import a character body for this rig first, or add an override to SKELETON_MAP.".format(
                compatible_rig
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


def _import_usd_asset(usd_path: str, destination_path: str) -> list[str]:
    task = unreal.AssetImportTask()
    task.set_editor_property("filename", usd_path)
    task.set_editor_property("destination_path", destination_path)
    task.set_editor_property("automated", True)
    task.set_editor_property("replace_existing", False)
    task.set_editor_property("save", True)
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    imported_paths = task.get_editor_property("imported_object_paths") or []
    return list(imported_paths)


def _find_imported_skeletal_mesh(imported_paths: list[str], search_root: str):
    for asset_path in imported_paths:
        asset = unreal.EditorAssetLibrary.load_asset(asset_path)
        if asset and isinstance(asset, unreal.SkeletalMesh):
            return asset
    return _find_first_skeletal_mesh(search_root)


def _mesh_uses_skeleton(mesh: unreal.SkeletalMesh, skeleton) -> bool:
    current = mesh.get_editor_property("skeleton")
    return current is not None and current.get_path_name() == skeleton.get_path_name()


def _try_subsystem_skeleton_assignment(mesh: unreal.SkeletalMesh, canonical_skeleton) -> bool:
    subsystem = unreal.get_editor_subsystem(unreal.SkeletalMeshEditorSubsystem)
    if subsystem is None:
        _log_warning("SkeletalMeshEditorSubsystem is unavailable in this UE session.")
        return False

    available_methods = [name for name in dir(subsystem) if "skeleton" in name.lower()]
    if available_methods:
        _log_info("SkeletalMeshEditorSubsystem skeleton-related methods: " + ", ".join(sorted(available_methods)))
    else:
        _log_warning("SkeletalMeshEditorSubsystem exposes no skeleton-related Python methods in this build.")

    candidate_methods = ["assign_skeleton", "set_skeletal_mesh_skeleton", "set_skeleton"]
    method_signatures: dict[str, list[tuple[str, tuple[Any, ...], dict[str, Any]]]] = {
        "assign_skeleton": [
            ("mesh,skeleton", (mesh, canonical_skeleton), {}),
            ("kw(skeletal_mesh,skeleton)", (), {"skeletal_mesh": mesh, "skeleton": canonical_skeleton}),
        ],
        "set_skeletal_mesh_skeleton": [
            ("mesh,skeleton", (mesh, canonical_skeleton), {}),
            ("kw(skeletal_mesh,skeleton)", (), {"skeletal_mesh": mesh, "skeleton": canonical_skeleton}),
            ("kw(skeletal_mesh,new_skeleton)", (), {"skeletal_mesh": mesh, "new_skeleton": canonical_skeleton}),
        ],
        "set_skeleton": [
            ("mesh,skeleton", (mesh, canonical_skeleton), {}),
            ("kw(mesh,skeleton)", (), {"mesh": mesh, "skeleton": canonical_skeleton}),
        ],
    }

    def _check_success() -> bool:
        reloaded_mesh = unreal.EditorAssetLibrary.load_asset(mesh.get_path_name())
        if reloaded_mesh is None:
            return False
        if not _mesh_uses_skeleton(reloaded_mesh, canonical_skeleton):
            return False
        unreal.EditorAssetLibrary.save_loaded_asset(reloaded_mesh)
        return True

    def _try_call(method, method_name: str, call_label: str, *args, **kwargs) -> bool:
        try:
            method(*args, **kwargs)
        except Exception as exc:
            _log_info(f"{method_name} {call_label} failed: {exc}")
            return False
        if _check_success():
            _log_info(f"Skeleton reassigned via SkeletalMeshEditorSubsystem.{method_name} ({call_label}).")
            return True
        return False

    for method_name in candidate_methods:
        if not hasattr(subsystem, method_name):
            continue
        method = getattr(subsystem, method_name)
        signatures = method_signatures.get(method_name, [])
        for call_label, args, kwargs in signatures:
            if _try_call(method, method_name, call_label, *args, **kwargs):
                return True

    _log_warning(
        "No working SkeletalMeshEditorSubsystem skeleton assignment signature was found in this build."
    )
    return False


def _assign_canonical_skeleton(mesh: unreal.SkeletalMesh, canonical_skeleton, target_path: str) -> tuple[bool, Optional[str], str]:
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
        if REQUIRE_CANONICAL_SKELETON:
            _fail("Skeleton mismatch detected but reassignment is disabled. Current='{}', Canonical='{}'".format(current_path, canonical_path))
            return False, current_path, "blocked"
        _log_warning("Skeleton reassignment is disabled (ENABLE_SKELETON_REASSIGN=False).")
        return True, current_path, "skipped"

    if _try_subsystem_skeleton_assignment(mesh, canonical_skeleton):
        return True, current_path, "subsystem"

    if not ENABLE_CONSOLIDATE_FALLBACK:
        _fail(
            "Could not reassign skeleton with available SkeletalMeshEditorSubsystem APIs. Set ENABLE_CONSOLIDATE_FALLBACK=True to try EditorAssetLibrary.consolidate_assets as a fallback."
        )
        return False, current_path, "none"

    if not current_path.startswith(target_path + "/"):
        _fail(f"Refusing consolidate fallback because duplicate skeleton is outside target folder: {current_path}")
        return False, current_path, "none"
    if current_path.startswith("/Engine/Transient"):
        _fail(f"Refusing consolidate fallback because duplicate skeleton is transient: {current_path}. Save/import assets first, then rerun.")
        return False, current_path, "none"
    if _is_redirector_asset(current_path):
        _fail(f"Refusing consolidate fallback because duplicate skeleton path currently resolves to a redirector: {current_path}. Fix/delete redirector first and rerun.")
        return False, current_path, "none"

    try:
        consolidated = unreal.EditorAssetLibrary.consolidate_assets(canonical_skeleton, [current_skeleton])
    except Exception as exc:
        _fail("Consolidate fallback failed from '{}' to '{}': {}".format(current_path, canonical_path, exc))
        return False, current_path, "none"

    if not consolidated:
        _fail("Consolidate fallback returned false from '{}' to '{}'".format(current_path, canonical_path))
        return False, current_path, "none"

    _log_warning("Skeleton reassignment used consolidate fallback. Verify editor stability in your UE build.")
    return True, current_path, "consolidate"


def _discover_assets(library_root: Path) -> list[AssetRecord]:
    discovered: list[AssetRecord] = []
    for usd_path in sorted(library_root.rglob("*.usd")):
        metadata = _read_sidecar(usd_path)
        if metadata is None:
            continue
        category = _get_required_string(metadata, "category")
        source_asset_name = _get_required_string(metadata, "source_asset_name")
        compatible_rig = _get_required_string(metadata, "compatible_rig")
        if category is None or source_asset_name is None or compatible_rig is None:
            continue

        target_path, expected_import_root = _build_target_from_library_structure(library_root, usd_path)
        discovered.append(
            AssetRecord(
                usd_path=usd_path,
                metadata=metadata,
                category=category,
                source_asset_name=source_asset_name,
                compatible_rig=compatible_rig,
                usd_stem=usd_path.stem,
                target_path=target_path,
                expected_import_root=expected_import_root,
            )
        )

    discovered.sort(key=lambda asset: (0 if asset.category == "character_body" else 1, asset.category, asset.usd_stem.lower()))
    return discovered


def _resolve_or_import_mesh(asset: AssetRecord) -> ResolveResult:
    _ensure_content_folder(asset.target_path)
    _cleanup_redirectors(asset.target_path)

    if unreal.EditorAssetLibrary.does_directory_exist(asset.expected_import_root):
        existing_assets = unreal.EditorAssetLibrary.list_assets(asset.expected_import_root, recursive=True, include_folder=False)
        if IMPORT_ONLY_MISSING_USD and existing_assets:
            _log_info(f"USD already imported; skipping: {asset.usd_path}")
            return ResolveResult(
                mesh=_find_first_skeletal_mesh(asset.expected_import_root),
                imported_new=False,
                skipped_existing=True,
            )

        _cleanup_redirectors(asset.expected_import_root)
        if _has_redirectors(asset.expected_import_root):
            _fail(
                "Import folder already exists and still contains redirectors after cleanup: "
                f"{asset.expected_import_root}. Aborting to avoid known UE rename crash."
            )
            return ResolveResult(mesh=None, imported_new=False, skipped_existing=False)

        existing_mesh = _find_first_skeletal_mesh(asset.expected_import_root)
        if existing_mesh is not None:
            _log_warning(f"Skeletal mesh already exists, skipping import: {existing_mesh.get_path_name()}")
            return ResolveResult(mesh=existing_mesh, imported_new=False, skipped_existing=True)

        _fail(f"Import folder already exists but no SkeletalMesh was found: {asset.expected_import_root}.")
        return ResolveResult(mesh=None, imported_new=False, skipped_existing=False)

    imported_paths = _import_usd_asset(str(asset.usd_path), asset.target_path)
    search_root = asset.expected_import_root if unreal.EditorAssetLibrary.does_directory_exist(asset.expected_import_root) else asset.target_path
    mesh = _find_imported_skeletal_mesh(imported_paths, search_root)
    if mesh is None:
        _fail(f"Import did not produce a SkeletalMesh for {asset.usd_path} under '{asset.target_path}'.")
    return ResolveResult(mesh=mesh, imported_new=True, skipped_existing=False)


def _register_body_skeleton(asset: AssetRecord, mesh: unreal.SkeletalMesh, skeleton_map: dict[str, str]) -> bool:
    current_skeleton = mesh.get_editor_property("skeleton")
    if current_skeleton is None:
        return _fail(f"Character body mesh has no skeleton: {mesh.get_path_name()}")

    current_path = current_skeleton.get_path_name()
    configured_path = skeleton_map.get(asset.compatible_rig)
    if configured_path and configured_path != current_path:
        _log_warning(
            "Rig ID '{}' already maps to '{}'; imported body uses '{}'. Keeping configured canonical skeleton.".format(
                asset.compatible_rig,
                configured_path,
                current_path,
            )
        )
        return True

    skeleton_map[asset.compatible_rig] = current_path
    _log_info("Registered canonical skeleton for rig ID '{}': {}".format(asset.compatible_rig, current_path))
    return True


def _process_asset(asset: AssetRecord, skeleton_map: dict[str, str]) -> tuple[bool, bool, bool]:
    resolved = _resolve_or_import_mesh(asset)
    mesh = resolved.mesh
    if mesh is None:
        return False, resolved.imported_new, resolved.skipped_existing

    if asset.category == "character_body":
        ok = _register_body_skeleton(asset, mesh, skeleton_map)
        return ok, resolved.imported_new, resolved.skipped_existing

    canonical_skeleton_path, canonical_skeleton = _load_canonical_skeleton(asset.compatible_rig, skeleton_map)
    if canonical_skeleton is None or canonical_skeleton_path is None:
        return False, resolved.imported_new, resolved.skipped_existing

    _log_info(f"Beginning skeleton compatibility stage for {asset.usd_path.name}.")
    reassigned, duplicate_skeleton_path, reassign_method = _assign_canonical_skeleton(mesh, canonical_skeleton, asset.target_path)
    if not reassigned:
        _log_warning("Skeleton reassignment failed; duplicate skeleton was left in place.")
        return False, resolved.imported_new, resolved.skipped_existing

    unreal.EditorAssetLibrary.save_loaded_asset(mesh)

    _log_info("Import summary:")
    _log_info(f"  USD: {asset.usd_path}")
    _log_info(f"  Metadata category: {asset.category}")
    _log_info(f"  Content destination: {asset.target_path}")
    _log_info(f"  SkeletalMesh: {mesh.get_path_name()}")
    _log_info(f"  Canonical skeleton: {canonical_skeleton_path}")
    _log_info(f"  Reassignment method: {reassign_method}")
    if duplicate_skeleton_path:
        _log_info(f"  Duplicate skeleton detected: {duplicate_skeleton_path}")
        _safe_delete_if_unreferenced(duplicate_skeleton_path)
    else:
        _log_info("  Duplicate cleanup: not needed")

    return True, resolved.imported_new, resolved.skipped_existing


def run_import(library_root_path: str) -> bool:
    _append_trace("INFO", "--- Crowd import run started ---")
    _append_trace("INFO", f"Config LIBRARY_ROOT={library_root_path}")
    _append_trace("INFO", f"Config CONTENT_ROOT={CONTENT_ROOT}")
    _append_trace("INFO", f"Config ENABLE_SKELETON_REASSIGN={ENABLE_SKELETON_REASSIGN}")
    _append_trace("INFO", f"Config REQUIRE_CANONICAL_SKELETON={REQUIRE_CANONICAL_SKELETON}")
    _append_trace("INFO", f"Config ENABLE_CONSOLIDATE_FALLBACK={ENABLE_CONSOLIDATE_FALLBACK}")

    library_root = Path(library_root_path)
    if _LIBRARY_ROOT_ENV_VALUE:
        _log_info(
            f"Resolved LIBRARY_ROOT from environment variable {_LIBRARY_ROOT_ENV_VAR}: {library_root}"
        )
    else:
        _log_info(
            "Resolved LIBRARY_ROOT from Blender-matching default '~/crowd_diversity_library': "
            f"{library_root}"
        )

    if not library_root.exists():
        return _fail(f"Library root not found: {library_root}")

    assets = _discover_assets(library_root)
    if not assets:
        return _fail(f"No USD assets with valid JSON sidecars were found under {library_root}")

    _log_info(f"Discovered {len(assets)} asset(s) under {library_root}.")

    skeleton_map = dict(SKELETON_MAP)
    successes = 0
    failures = 0
    imported_new_count = 0
    skipped_existing_count = 0

    for asset in assets:
        _log_info(f"Processing {asset.category} asset: {asset.usd_path.name} (rig ID: {asset.compatible_rig})")
        ok, imported_new, skipped_existing = _process_asset(asset, skeleton_map)
        if imported_new:
            imported_new_count += 1
        if skipped_existing:
            skipped_existing_count += 1

        if ok:
            successes += 1
        else:
            failures += 1

    _log_info("Batch import summary:")
    _log_info(f"  Library root: {library_root}")
    _log_info(f"  Discovered USDs: {len(assets)}")
    _log_info(f"  Newly imported USDs: {imported_new_count}")
    _log_info(f"  Skipped already-imported USDs: {skipped_existing_count}")
    _log_info(f"  Successful assets: {successes}")
    _log_info(f"  Failed assets: {failures}")
    _log_info(f"  Registered rig IDs: {', '.join(sorted(skeleton_map)) if skeleton_map else '<none>'}")

    if failures > 0:
        _append_trace("ERROR", "--- Crowd import run finished with failures ---")
        return False

    _append_trace("INFO", "--- Crowd import run finished successfully ---")
    return True


if __name__ == "__main__":
    run_import(LIBRARY_ROOT)
