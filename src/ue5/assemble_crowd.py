from __future__ import annotations

import json
import os
import random
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import unreal

# Root path where import_garment.py placed assets in the Content Browser.
CONTENT_ROOT = "/Game"

# Category subfolders mirroring the Blender library structure.
CATEGORY_FOLDERS = {
    "character_body": "characters",
    "hair": "hair",
    "top": "tops",
    "bottom": "bottoms",
    "shoes": "shoes",
    "accessory": "accessories",
}

# Slots that are mutually exclusive. Only one asset per slot is allowed.
SLOT_EXCLUSIVITY: dict[str, list[str]] = {
    "head_covering": ["hair"],
    "torso": ["top"],
    "legs": ["bottom"],
    "feet": ["shoes"],
}

# Animation assets used to drive crowd variation. One entry is selected per
# agent during spec generation. If this list is empty, agents remain unanimated.
ANIMATION_ASSET_PATHS: list[str] = [
    "/Game/Animations/Cheering_01",
    "/Game/Animations/Cheering_02",
    "/Game/Animations/Clapping_01",
]

# How many agents to spawn.
AGENT_COUNT = 50

# Crowd layout in UE units (1 unit = 1 cm).
GRID_COLUMNS = 10
AGENT_SPACING_X = 100.0
AGENT_SPACING_Y = 100.0

# Optional natural variation so the crowd does not look too grid-like.
RANDOMIZE_YAW = True
MAX_RANDOM_YAW_DEGREES = 15.0

# Random seed for reproducible crowd generation.
RANDOM_SEED = 42

# Rig ID to assemble.
TARGET_RIG_ID = "mixamo_v1"

# Missing body handling.
SKIP_MISSING_BODIES = True

# Crash/debug trace log path (Windows; silently skipped on other platforms).
TRACE_LOG_PATH = "C:/Users/Public/crowd_assemble_trace.log"

# Optional Blueprint class fallback for projects where dynamic component APIs
# are unreliable in Python. Keep this as None unless you create a dedicated
# crowd agent Blueprint manually and want to spawn that class instead.
# Example class path: "/Game/BP/BP_CrowdAgent.BP_CrowdAgent_C"
BLUEPRINT_AGENT_CLASS_PATH: str | None = None

# When True, if Actor.add_component_by_class is unavailable and no Blueprint
# fallback is configured, the script uses SkeletalMeshActor-based assembly.
# In this mode each agent is represented by one body SkeletalMeshActor plus
# zero or more garment SkeletalMeshActors driven by leader pose.
ENABLE_MULTI_ACTOR_FALLBACK = True

# Slot names expected on the optional Blueprint fallback actor. If your
# Blueprint uses different component names, adjust these values.
BLUEPRINT_COMPONENT_NAMES: dict[str, str] = {
    "character_body": "Body",
    "hair": "Hair",
    "top": "Top",
    "bottom": "Bottom",
    "shoes": "Shoes",
    "accessory": "Accessory",
}

# Blender library path used to find JSON sidecars that contain rig + slot data.
_DEFAULT_LIBRARY_ROOT = "~/crowd_diversity_library"
_LIBRARY_ROOT_ENV_VAR = "CROWD_DIVERSITY_LIBRARY_ROOT"
_LIBRARY_ROOT_ENV_VALUE = os.environ.get(_LIBRARY_ROOT_ENV_VAR)
LIBRARY_ROOT = os.path.normpath(
    os.path.expanduser(_LIBRARY_ROOT_ENV_VALUE or _DEFAULT_LIBRARY_ROOT)
)

# Outfit randomization controls.
GARMENT_PRESENCE_PROBABILITY = 0.7
# When False (default), if a category has available assets, one is always chosen.
# When True, categories can be randomly left empty using GARMENT_PRESENCE_PROBABILITY.
ALLOW_EMPTY_GARMENT_CATEGORIES = False
# When True, every spawned agent must include a top garment.
REQUIRE_TOP_COVERAGE = True
# When True, every spawned agent must include a bottom garment.
REQUIRE_BOTTOM_COVERAGE = True
RECENT_COMBO_FRACTION = 0.1


@dataclass(frozen=True)
class AssetInfo:
    content_path: str
    category: str
    compatible_rig: str
    slot: str
    exclusivity_tags: list[str]
    source_asset_name: str


@dataclass(frozen=True)
class AgentSpec:
    body: AssetInfo
    garments: dict[str, AssetInfo | None]
    animation_asset_path: str | None


def _log_info(message: str) -> None:
    _append_trace("INFO", message)
    unreal.log(f"[CrowdAssemble] {message}")


def _log_warning(message: str) -> None:
    _append_trace("WARN", message)
    unreal.log_warning(f"[CrowdAssemble] {message}")


def _log_error(message: str) -> None:
    _append_trace("ERROR", message)
    unreal.log_error(f"[CrowdAssemble] {message}")


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


def _default_slot_for_category(category: str) -> str:
    defaults = {
        "character_body": "body",
        "hair": "head_covering",
        "top": "torso",
        "bottom": "legs",
        "shoes": "feet",
        "accessory": "body",
    }
    return defaults.get(category, "body")


def _content_root_tokens() -> list[str]:
    return [token for token in CONTENT_ROOT.strip("/").split("/") if token]


def _infer_sidecar_path(library_root: Path, category: str, content_path: str) -> Optional[Path]:
    package_path = content_path.split(".", 1)[0]
    parts = [token for token in package_path.strip("/").split("/") if token]
    root_tokens = _content_root_tokens()
    category_folder = CATEGORY_FOLDERS[category]

    if len(parts) < len(root_tokens) + 2:
        return None
    if parts[: len(root_tokens)] != root_tokens:
        return None
    if parts[len(root_tokens)] != category_folder:
        return None

    # Expected imported path pattern from import_garment.py is:
    # /Game/<category_folder>/<usd_stem>/SkeletalMeshes/<mesh>
    usd_stem = parts[len(root_tokens) + 1]
    return library_root / category_folder / f"{usd_stem}.json"


def _sidecar_to_asset_info(
    category: str,
    content_path: str,
    sidecar_data: dict[str, Any] | None,
    fallback_source_name: str,
) -> AssetInfo:
    if sidecar_data is None:
        slot = _default_slot_for_category(category)
        _log_warning(
            f"No sidecar found for {content_path}; using fallback rig='{TARGET_RIG_ID}', slot='{slot}'."
        )
        return AssetInfo(
            content_path=content_path,
            category=category,
            compatible_rig=TARGET_RIG_ID,
            slot=slot,
            exclusivity_tags=[slot] if slot in SLOT_EXCLUSIVITY else [],
            source_asset_name=fallback_source_name,
        )

    compatible_rig = sidecar_data.get("compatible_rig")
    if not isinstance(compatible_rig, str) or not compatible_rig.strip():
        compatible_rig = TARGET_RIG_ID
        _log_warning(
            f"Missing compatible_rig in sidecar for {content_path}; defaulting to '{TARGET_RIG_ID}'."
        )

    slot = sidecar_data.get("slot")
    if not isinstance(slot, str) or not slot.strip():
        slot = _default_slot_for_category(category)
        _log_warning(
            f"Missing slot in sidecar for {content_path}; defaulting to '{slot}'."
        )

    tags = sidecar_data.get("exclusivity_tags")
    exclusivity_tags: list[str]
    if isinstance(tags, list):
        exclusivity_tags = [str(tag).strip() for tag in tags if str(tag).strip()]
    else:
        exclusivity_tags = []

    source_asset_name = sidecar_data.get("source_asset_name")
    if not isinstance(source_asset_name, str) or not source_asset_name.strip():
        source_asset_name = fallback_source_name

    return AssetInfo(
        content_path=content_path,
        category=category,
        compatible_rig=compatible_rig.strip(),
        slot=slot.strip(),
        exclusivity_tags=exclusivity_tags,
        source_asset_name=source_asset_name.strip(),
    )


def _discover_assets() -> dict[tuple[str, str], list[AssetInfo]]:
    grouped: dict[tuple[str, str], list[AssetInfo]] = {}
    library_root = Path(LIBRARY_ROOT)

    for category, folder in CATEGORY_FOLDERS.items():
        content_folder = f"{CONTENT_ROOT.rstrip('/')}/{folder}"
        if not unreal.EditorAssetLibrary.does_directory_exist(content_folder):
            _log_warning(f"Content folder is missing and will be skipped: {content_folder}")
            continue

        asset_paths = unreal.EditorAssetLibrary.list_assets(
            content_folder,
            recursive=True,
            include_folder=False,
        )

        skeletal_mesh_count = 0
        for asset_path in asset_paths:
            loaded = unreal.EditorAssetLibrary.load_asset(asset_path)
            if loaded is None or not isinstance(loaded, unreal.SkeletalMesh):
                continue

            skeletal_mesh_count += 1
            sidecar_data: dict[str, Any] | None = None
            fallback_name = loaded.get_name()

            sidecar_path = _infer_sidecar_path(library_root, category, asset_path)
            if sidecar_path is not None and sidecar_path.exists():
                try:
                    parsed = json.loads(sidecar_path.read_text(encoding="utf-8"))
                    if isinstance(parsed, dict):
                        sidecar_data = parsed
                    else:
                        _log_warning(f"Sidecar root is not an object: {sidecar_path}")
                except Exception as exc:
                    _log_warning(f"Failed to parse sidecar {sidecar_path}: {exc}")

            info = _sidecar_to_asset_info(category, asset_path, sidecar_data, fallback_name)
            grouped.setdefault((info.compatible_rig, category), []).append(info)

        _log_info(f"Discovered {skeletal_mesh_count} SkeletalMesh asset(s) in {content_folder}.")

    return grouped


def _build_rig_pools(grouped: dict[tuple[str, str], list[AssetInfo]], rig_id: str) -> dict[str, list[AssetInfo]]:
    pools: dict[str, list[AssetInfo]] = {}
    for category in CATEGORY_FOLDERS:
        pools[category] = list(grouped.get((rig_id, category), []))
    return pools


def _category_pick(
    category: str,
    pool: list[AssetInfo],
    used_tags: set[str],
    rng: random.Random,
) -> AssetInfo | None:
    if not pool:
        return None

    if ALLOW_EMPTY_GARMENT_CATEGORIES and rng.random() > GARMENT_PRESENCE_PROBABILITY:
        return None

    compatible_options = [
        asset for asset in pool if not any(tag in used_tags for tag in asset.exclusivity_tags)
    ]
    if not compatible_options:
        return None

    chosen = rng.choice(compatible_options)
    for tag in chosen.exclusivity_tags:
        used_tags.add(tag)
    return chosen


def _apply_slot_exclusivity(garments: dict[str, AssetInfo | None], rng: random.Random) -> None:
    for _slot, categories in SLOT_EXCLUSIVITY.items():
        chosen = [category for category in categories if garments.get(category) is not None]
        if len(chosen) <= 1:
            continue
        keep = rng.choice(chosen)
        for category in chosen:
            if category != keep:
                garments[category] = None


def _combo_key(body: AssetInfo, garments: dict[str, AssetInfo | None]) -> tuple[str, ...]:
    ordered_categories = sorted(category for category in CATEGORY_FOLDERS if category != "character_body")
    key: list[str] = [body.content_path]
    for category in ordered_categories:
        asset = garments.get(category)
        key.append(asset.content_path if asset is not None else "<none>")
    return tuple(key)


def _pick_animation_path(rng: random.Random) -> str | None:
    if not ANIMATION_ASSET_PATHS:
        return None
    return rng.choice(ANIMATION_ASSET_PATHS)


def _propose_agent_spec(pools: dict[str, list[AssetInfo]], rng: random.Random) -> AgentSpec:
    body = rng.choice(pools["character_body"])
    garments: dict[str, AssetInfo | None] = {}
    used_tags: set[str] = set()

    garment_categories = [
        category for category in CATEGORY_FOLDERS if category != "character_body"
    ]
    for category in garment_categories:
        garments[category] = _category_pick(category, pools[category], used_tags, rng)

    if REQUIRE_TOP_COVERAGE and pools["top"] and garments.get("top") is None:
        compatible_tops = [
            asset for asset in pools["top"] if not any(tag in used_tags for tag in asset.exclusivity_tags)
        ]
        if compatible_tops:
            top_asset = rng.choice(compatible_tops)
            garments["top"] = top_asset
            for tag in top_asset.exclusivity_tags:
                used_tags.add(tag)

    if REQUIRE_BOTTOM_COVERAGE and pools["bottom"] and garments.get("bottom") is None:
        compatible_bottoms = [
            asset for asset in pools["bottom"] if not any(tag in used_tags for tag in asset.exclusivity_tags)
        ]
        if compatible_bottoms:
            bottom_asset = rng.choice(compatible_bottoms)
            garments["bottom"] = bottom_asset
            for tag in bottom_asset.exclusivity_tags:
                used_tags.add(tag)

    _apply_slot_exclusivity(garments, rng)
    return AgentSpec(
        body=body,
        garments=garments,
        animation_asset_path=_pick_animation_path(rng),
    )


def _generate_agent_specs(pools: dict[str, list[AssetInfo]], count: int, rng: random.Random) -> list[AgentSpec]:
    recent_window = max(1, int(count * RECENT_COMBO_FRACTION))
    recent_keys: deque[tuple[str, ...]] = deque(maxlen=recent_window)
    specs: list[AgentSpec] = []

    for _ in range(count):
        spec = _propose_agent_spec(pools, rng)
        key = _combo_key(spec.body, spec.garments)
        if key in recent_keys:
            resampled = _propose_agent_spec(pools, rng)
            spec = resampled
            key = _combo_key(spec.body, spec.garments)
        recent_keys.append(key)
        specs.append(spec)

    return specs


def _build_transform(index: int, rng: random.Random) -> tuple[unreal.Vector, unreal.Rotator]:
    column = index % GRID_COLUMNS
    row = index // GRID_COLUMNS

    location = unreal.Vector(
        x=column * AGENT_SPACING_X,
        y=row * AGENT_SPACING_Y,
        z=0.0,
    )

    yaw = 0.0
    if RANDOMIZE_YAW:
        yaw = rng.uniform(-MAX_RANDOM_YAW_DEGREES, MAX_RANDOM_YAW_DEGREES)
    rotation = unreal.Rotator(roll=0.0, pitch=0.0, yaw=yaw)
    return location, rotation


def _load_animation_assets() -> dict[str, Any | None]:
    loaded: dict[str, Any | None] = {}
    for path in ANIMATION_ASSET_PATHS:
        if not unreal.EditorAssetLibrary.does_asset_exist(path):
            _log_warning(f"Animation asset was not found: {path}. Agents assigned to it will be unanimated.")
            loaded[path] = None
            continue

        anim = unreal.EditorAssetLibrary.load_asset(path)
        if anim is None:
            _log_warning(f"Animation asset was not found: {path}. Agents assigned to it will be unanimated.")
        loaded[path] = anim
    return loaded


def _resolve_spawn_class() -> Any:
    if not BLUEPRINT_AGENT_CLASS_PATH:
        return unreal.Actor

    cls = unreal.load_class(None, BLUEPRINT_AGENT_CLASS_PATH)
    if cls is None:
        _log_warning(
            "Configured BLUEPRINT_AGENT_CLASS_PATH could not be loaded. "
            "Falling back to plain Actor dynamic components."
        )
        return unreal.Actor
    return cls


def _spawn_actor(spawn_class: Any, location: unreal.Vector, rotation: unreal.Rotator):
    # UE5.5 exposes EditorLevelLibrary.spawn_actor_from_class with a class type,
    # a Vector location, and a Rotator. This is the standard editor-time spawn path.
    try:
        return unreal.EditorLevelLibrary.spawn_actor_from_class(spawn_class, location, rotation)
    except Exception as exc:
        _log_error(f"Actor spawn failed at ({location.x}, {location.y}, {location.z}): {exc}")
        return None


def _add_skeletal_mesh_component(actor, component_name: str):
    # Runtime component creation from Python is one of the rougher UE APIs.
    # The direct path is add_component_by_class. If your build rejects this,
    # use BLUEPRINT_AGENT_CLASS_PATH with pre-defined mesh slots instead.
    if not hasattr(actor, "add_component_by_class"):
        _log_error("Spawned actor does not expose add_component_by_class in this UE build.")
        return None

    try:
        component = actor.add_component_by_class(
            unreal.SkeletalMeshComponent,
            False,
            unreal.Transform(),
            False,
        )
    except TypeError:
        try:
            component = actor.add_component_by_class(unreal.SkeletalMeshComponent)
        except Exception as exc:
            _log_error(f"Failed to add SkeletalMeshComponent '{component_name}': {exc}")
            return None
    except Exception as exc:
        _log_error(f"Failed to add SkeletalMeshComponent '{component_name}': {exc}")
        return None

    if component is None:
        _log_error(f"add_component_by_class returned None for '{component_name}'.")
        return None

    try:
        component.rename(component_name)
    except Exception:
        pass

    return component


def _get_skeletal_mesh_components(actor) -> list[Any]:
    if not hasattr(actor, "get_components_by_class"):
        return []
    try:
        return list(actor.get_components_by_class(unreal.SkeletalMeshComponent))
    except Exception:
        return []


def _find_named_skeletal_component(actor, expected_name: str):
    expected_lower = expected_name.lower()
    components = _get_skeletal_mesh_components(actor)

    exact = [comp for comp in components if comp.get_name().lower() == expected_lower]
    if exact:
        return exact[0]

    starts = [comp for comp in components if comp.get_name().lower().startswith(expected_lower)]
    if starts:
        return starts[0]

    return None


def _set_component_mesh(component, mesh_asset) -> bool:
    try:
        component.set_editor_property("skeletal_mesh", mesh_asset)
        return True
    except Exception as exc:
        _log_error(f"Failed to set skeletal mesh on component '{component.get_name()}': {exc}")
        return False


def _destroy_actor_safely(actor) -> None:
    subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    if subsystem is not None and hasattr(subsystem, "destroy_actor"):
        try:
            subsystem.destroy_actor(actor)
            return
        except Exception:
            pass

    try:
        unreal.EditorLevelLibrary.destroy_actor(actor)
    except Exception:
        pass


def _destroy_actors_safely(actors: list[Any]) -> None:
    for actor in reversed(actors):
        if actor is not None:
            _destroy_actor_safely(actor)


def _save_current_level() -> bool:
    # Preferred in UE5.5+: LevelEditorSubsystem.save_current_level.
    level_subsystem = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    if level_subsystem is not None and hasattr(level_subsystem, "save_current_level"):
        try:
            result = level_subsystem.save_current_level()
            return bool(result)
        except Exception:
            pass

    # Fallback for older/editor utility paths.
    try:
        result = unreal.EditorLevelLibrary.save_current_level()
        return bool(result)
    except Exception:
        return False


def _assign_animation(component, animation_asset: Any | None) -> bool:
    if animation_asset is None:
        return True

    assigned = False
    try:
        component.set_editor_property("animation_mode", unreal.AnimationMode.ANIMATION_SINGLE_NODE)
        assigned = True
    except Exception:
        pass

    try:
        component.play_animation(animation_asset, True)
        assigned = True
    except Exception:
        pass

    if not assigned:
        _log_warning("Animation assignment failed for one component. Leaving it unanimated.")
    return assigned


def _set_leader_pose(garment_component, body_component) -> bool:
    # Leader pose is the preferred API for modular garments: all follower
    # meshes evaluate from the same body pose without independent simulation.
    if hasattr(garment_component, "set_leader_pose_component"):
        try:
            garment_component.set_leader_pose_component(body_component)
            return True
        except Exception:
            pass

    # Backward-compat fallback used by older UE APIs.
    if hasattr(garment_component, "set_master_pose_component"):
        try:
            garment_component.set_master_pose_component(body_component)
            return True
        except Exception:
            pass

    _log_warning("Could not set leader pose on a garment component in this UE build.")
    return False


def _load_skeletal_mesh(mesh_path: str):
    asset = unreal.EditorAssetLibrary.load_asset(mesh_path)
    if asset is None or not isinstance(asset, unreal.SkeletalMesh):
        return None
    return asset


def _get_skeletal_component_from_actor(actor):
    try:
        component = actor.get_editor_property("skeletal_mesh_component")
        if component is not None:
            return component
    except Exception:
        pass

    components = _get_skeletal_mesh_components(actor)
    if components:
        return components[0]
    return None


def run_assembly() -> bool:
    _append_trace("INFO", "--- Crowd assembly run started ---")
    _log_info(f"Config CONTENT_ROOT={CONTENT_ROOT}")
    _log_info(f"Config LIBRARY_ROOT={LIBRARY_ROOT}")
    _log_info(f"Config TARGET_RIG_ID={TARGET_RIG_ID}")
    _log_info(f"Config AGENT_COUNT={AGENT_COUNT}")
    _log_info(f"Config RANDOM_SEED={RANDOM_SEED}")
    _log_info(f"Config ANIMATION_ASSET_PATHS={len(ANIMATION_ASSET_PATHS)} entries")
    _log_info(f"Config ALLOW_EMPTY_GARMENT_CATEGORIES={ALLOW_EMPTY_GARMENT_CATEGORIES}")
    _log_info(f"Config REQUIRE_TOP_COVERAGE={REQUIRE_TOP_COVERAGE}")
    _log_info(f"Config REQUIRE_BOTTOM_COVERAGE={REQUIRE_BOTTOM_COVERAGE}")

    rng = random.Random(RANDOM_SEED)
    grouped = _discover_assets()
    pools = _build_rig_pools(grouped, TARGET_RIG_ID)

    if not pools["character_body"]:
        return _fail(
            "No character body SkeletalMesh assets were found for rig ID "
            f"'{TARGET_RIG_ID}'. Import bodies first or fix metadata sidecars."
        )

    for category in CATEGORY_FOLDERS:
        if category == "character_body":
            continue
        if not pools[category]:
            _log_warning(
                f"No '{category}' assets found for rig '{TARGET_RIG_ID}'. "
                "That category will be empty across the crowd."
            )

    if REQUIRE_TOP_COVERAGE and not pools["top"]:
        return _fail(
            "No 'top' assets are available for the target rig, but REQUIRE_TOP_COVERAGE=True. "
            "Import tops for this rig or disable REQUIRE_TOP_COVERAGE."
        )

    if REQUIRE_BOTTOM_COVERAGE and not pools["bottom"]:
        return _fail(
            "No 'bottom' assets are available for the target rig, but REQUIRE_BOTTOM_COVERAGE=True. "
            "Import bottoms for this rig or disable REQUIRE_BOTTOM_COVERAGE."
        )

    specs = _generate_agent_specs(pools, AGENT_COUNT, rng)
    loaded_animations = _load_animation_assets()
    spawn_class = _resolve_spawn_class()
    using_blueprint_slots = bool(BLUEPRINT_AGENT_CLASS_PATH)
    use_multi_actor_fallback = False

    if not using_blueprint_slots and not hasattr(unreal.Actor, "add_component_by_class"):
        if ENABLE_MULTI_ACTOR_FALLBACK:
            use_multi_actor_fallback = True
            _log_warning(
                "Actor.add_component_by_class is unavailable. Using SkeletalMeshActor multi-actor fallback mode."
            )
        else:
            return _fail(
                "This UE build does not expose Actor.add_component_by_class in Python. "
                "Set BLUEPRINT_AGENT_CLASS_PATH to a Blueprint actor with SkeletalMeshComponent slots "
                "(Body, Hair, Top, Bottom, Shoes, Accessory), or enable ENABLE_MULTI_ACTOR_FALLBACK, then rerun."
            )

    spawned = 0
    failed = 0
    skipped = 0
    combo_keys: set[tuple[str, ...]] = set()

    for index, spec in enumerate(specs):
        body_mesh = _load_skeletal_mesh(spec.body.content_path)
        if body_mesh is None:
            message = f"Body mesh missing/unloadable for agent {index}: {spec.body.content_path}"
            if SKIP_MISSING_BODIES:
                _log_warning(message)
                skipped += 1
                continue
            return _fail(message)

        location, rotation = _build_transform(index, rng)
        created_actors: list[Any] = []

        if use_multi_actor_fallback:
            actor = _spawn_actor(unreal.SkeletalMeshActor, location, rotation)
        else:
            actor = _spawn_actor(spawn_class, location, rotation)

        if actor is None:
            failed += 1
            continue

        created_actors.append(actor)

        if using_blueprint_slots:
            body_component_name = BLUEPRINT_COMPONENT_NAMES["character_body"]
            body_component = _find_named_skeletal_component(actor, body_component_name)
            if body_component is None:
                _log_error(
                    f"Blueprint fallback actor is missing body component '{body_component_name}' for agent {index}."
                )
                failed += 1
                _destroy_actors_safely(created_actors)
                continue
            if not _set_component_mesh(body_component, body_mesh):
                failed += 1
                _destroy_actors_safely(created_actors)
                continue
        elif use_multi_actor_fallback:
            body_component = _get_skeletal_component_from_actor(actor)
            if body_component is None:
                _log_error(f"SkeletalMeshActor body component missing for agent {index}.")
                failed += 1
                _destroy_actors_safely(created_actors)
                continue
            if not _set_component_mesh(body_component, body_mesh):
                failed += 1
                _destroy_actors_safely(created_actors)
                continue
        else:
            body_component = _add_skeletal_mesh_component(actor, f"Body_{index}")
            if body_component is None:
                failed += 1
                _destroy_actors_safely(created_actors)
                continue
            if not _set_component_mesh(body_component, body_mesh):
                failed += 1
                _destroy_actors_safely(created_actors)
                continue

        animation_asset = (
            loaded_animations.get(spec.animation_asset_path)
            if spec.animation_asset_path is not None
            else None
        )
        _assign_animation(body_component, animation_asset)

        agent_failed = False
        for category, garment in spec.garments.items():
            if using_blueprint_slots:
                component_name = BLUEPRINT_COMPONENT_NAMES.get(category)
                if not component_name:
                    continue
                garment_component = _find_named_skeletal_component(actor, component_name)
                if garment_component is None:
                    _log_warning(
                        f"Blueprint fallback actor missing '{component_name}' slot for category '{category}'."
                    )
                    continue

                if garment is None:
                    _set_component_mesh(garment_component, None)
                    continue

                garment_mesh = _load_skeletal_mesh(garment.content_path)
                if garment_mesh is None:
                    _log_warning(
                        f"Garment mesh missing for agent {index}, category '{category}': {garment.content_path}"
                    )
                    continue

                if not _set_component_mesh(garment_component, garment_mesh):
                    agent_failed = True
                    break

                _set_leader_pose(garment_component, body_component)
                _assign_animation(garment_component, animation_asset)
            else:
                if garment is None:
                    continue

                garment_mesh = _load_skeletal_mesh(garment.content_path)
                if garment_mesh is None:
                    _log_warning(
                        f"Garment mesh missing for agent {index}, category '{category}': {garment.content_path}"
                    )
                    continue

                if use_multi_actor_fallback:
                    garment_actor = _spawn_actor(unreal.SkeletalMeshActor, location, rotation)
                    if garment_actor is None:
                        _log_error(
                            f"Failed to spawn garment SkeletalMeshActor for agent {index}, category '{category}'."
                        )
                        agent_failed = True
                        break

                    created_actors.append(garment_actor)
                    garment_component = _get_skeletal_component_from_actor(garment_actor)
                    if garment_component is None:
                        _log_error(
                            f"Spawned garment actor has no SkeletalMeshComponent for agent {index}, category '{category}'."
                        )
                        agent_failed = True
                        break
                else:
                    garment_component = _add_skeletal_mesh_component(actor, f"{category}_{index}")
                    if garment_component is None:
                        _log_error(f"Failed to add garment component for agent {index}, category '{category}'.")
                        agent_failed = True
                        break

                if not _set_component_mesh(garment_component, garment_mesh):
                    agent_failed = True
                    break

                _set_leader_pose(garment_component, body_component)
                _assign_animation(garment_component, animation_asset)

        if agent_failed:
            failed += 1
            _destroy_actors_safely(created_actors)
            continue

        try:
            actor.set_actor_location(location, False, False)
            actor.set_actor_rotation(rotation, False)
        except Exception:
            pass

        combo_keys.add(_combo_key(spec.body, spec.garments))
        spawned += 1

    # UE may require the current level to be an existing saved level package.
    # On a brand-new unsaved level, save_current_level can fail.
    if _save_current_level():
        _log_info("Current level saved.")
    else:
        _log_warning(
            "Could not save current level automatically. Save the map once with File -> Save Current Level As..., then rerun to enable auto-save."
        )

    _log_info("Crowd assembly summary:")
    _log_info(f"  Target rig ID: {TARGET_RIG_ID}")
    _log_info(f"  Random seed: {RANDOM_SEED}")
    _log_info(f"  Requested agents: {AGENT_COUNT}")
    _log_info(f"  Spawned agents: {spawned}")
    _log_info(f"  Failed agents: {failed}")
    _log_info(f"  Skipped agents: {skipped}")
    _log_info(f"  Unique outfit combinations: {len(combo_keys)}")

    if AGENT_COUNT > 0 and (failed / AGENT_COUNT) > 0.1:
        _append_trace("ERROR", "--- Crowd assembly run finished with high failure ratio ---")
        return False

    _append_trace("INFO", "--- Crowd assembly run finished successfully ---")
    return True


if __name__ == "__main__":
    run_assembly()