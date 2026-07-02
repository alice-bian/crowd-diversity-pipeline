# Blender UE5 USD Crowd Pipeline

## Project Overview
This project implements a practical crowd-variation pipeline that starts in Blender and targets Unreal Engine 5. The Blender extension exports rigged character bodies, clothing, hair, shoes, and accessories as USD assets plus JSON metadata sidecars, so downstream tools can assemble large visual variety from reusable parts.

The approach is inspired by Sony Pictures Imageworks KPDH-style previs workflows: a small set of base characters and modular wardrobe pieces produce broad crowd diversity through combinatorics instead of one-off hero builds. Here, that idea is reimplemented with an accessible toolchain (Blender + UE5 + Python) for portfolio and production-adjacent experimentation.

This repository now includes both halves of the baseline pipeline: Blender authoring/export and a UE5 editor Python importer at `ue5_pipeline/import_garment.py` that can process an exported asset library in batch.

## Architecture
The pipeline is intentionally split into two stages: deterministic asset packaging in Blender, then deterministic assembly in UE5.

```mermaid
flowchart LR
		A[Blender Scene\nMaster Armature + Skinned Parts] --> B[Blender Extension\nCategory + Rig ID + Export]
		B --> C[USD Files\nPer-Asset Geometry + Skinning]
		B --> D[JSON Sidecars\nCategory Slot compatible_rig Provenance]
		C --> E[UE5 Python Import]
		D --> E
		E --> F[Shared Skeleton Reassignment\nCrowd Scatter and Assembly]
```

Operationally, all exportable parts are expected to be skinned to a single master armature before export. This is a pipeline contract, not a hardcoded rig-name restriction. Any rig can be the master as long as every interchangeable asset references the same logical rig ID in metadata.

That rig contract is tracked through the `compatible_rig` field in each sidecar. Rig IDs are managed at scene level in a `Rig IDs` list, then assigned per object via dropdown in the export panel. On the UE side, character body imports are processed first and their imported skeletons become the canonical skeletons for their rig IDs unless overridden in `SKELETON_MAP`.

## Library Structure
The extension writes one USD and one JSON file per exported asset into category folders:

```text
/library
	/characters/
		body_001.usd
		body_001.json
	/tops/
		jacket_001.usd
		jacket_001.json
	/hair/
	/bottoms/
	/shoes/
	/accessories/
```

JSON sidecars include:

- `category`
- `slot`
- `exclusivity_tags`
- `compatible_rig`
- `source_file`
- `export_date`
- `blender_version`
- `source_asset_name`

## Installation
Install as a Blender 5 extension:

1. Open Blender 5.x.
2. Navigate to Edit -> Preferences -> Add-ons.
3. Select Install from Disk.
4. Point Blender at this repository root (or packaged extension zip).
5. Enable `Crowd Diversity USD Pipeline`.

## Prerequisites and Required Setup

### Blender-side requirements

1. Blender 5.x with this extension enabled.
2. Export assets must be rigged/skinned to a shared logical rig contract, then assigned a `compatible_rig` value in the panel.
3. Maintain the scene-level `Rig IDs` list and assign each selected object a valid rig ID before export.
4. Set/confirm the Blender `Library Output` folder. The default is `~/crowd_diversity_library`.
5. Export creates paired `.usd` + `.json` files; UE import requires both files for each asset.

### UE5-side requirements

Enable these plugins in your UE project before running the importer:

1. `Python Editor Script Plugin` (required)
2. `USD Importer` (required)

Recommended for editor automation workflows:

1. `Editor Scripting Utilities`

Additional UE setup:

1. Ensure your UE project can read the Blender export directory (`LIBRARY_ROOT`).
2. Set/confirm `LIBRARY_ROOT` in `ue5_pipeline/import_garment.py`, or set the `CROWD_DIVERSITY_LIBRARY_ROOT` environment variable before launching UE.
3. Set/confirm `CONTENT_ROOT` in `ue5_pipeline/import_garment.py` (default: `/Game`).
4. Optional: predefine `SKELETON_MAP` entries when you need explicit rig ID -> skeleton overrides.

## Usage
Authoring and export flow:

1. Skin character bodies and modular garments/hair/accessories to the same master armature in Blender.
2. Select one or more rigged mesh assets.
3. In `Rig IDs`, define one or more rig IDs (for example, `mixamo_v1`).
4. In `Selected Asset Types`, assign each selected object both a category and a compatible rig ID.
5. Optionally run Fit Check poses (`Original`, `Neutral`, `A-Pose`, `T-Pose`) for clipping review.
6. Export selected assets to produce USD + JSON sidecars.

UE import flow:

1. Open `ue5_pipeline/import_garment.py` and confirm `LIBRARY_ROOT`, `CONTENT_ROOT`, and optional flags (for example `IMPORT_ONLY_MISSING_USD`).
2. Launch Unreal Editor for your target project.
3. Run the script using one of these methods:
	1. Output Log command: `py "<absolute path to repo>/ue5_pipeline/import_garment.py"`
	2. Tools menu: Tools -> Execute Python Script... and select `ue5_pipeline/import_garment.py`
4. The script automatically discovers USD+JSON pairs, imports character bodies first, registers canonical skeletons by `compatible_rig`, then imports/reconciles non-body assets.
5. Re-running is idempotent when `IMPORT_ONLY_MISSING_USD=True`: already-imported USD folders are skipped.
6. Check the UE Output Log summary for:
	1. `Discovered USDs`
	2. `Newly imported USDs`
	3. `Skipped already-imported USDs`
	4. `Successful assets` and `Failed assets`

## Known Limitations
UE5 USD skeletal mesh import generates a skeleton asset per import by default. The included UE5 Python importer handles redirector cleanup, idempotent re-runs, body-first canonical skeleton registration, and canonical skeleton validation.

On UE5.5, skeleton reassignment APIs can be unstable or missing in some builds/projects. The importer prefers editor subsystem reassignment when available and falls back to guarded asset consolidation when necessary.

Blender USD export has practical feature limits for this workflow: bendy bones and non-Armature deformation stacks are not reliably represented for this pipeline target. For predictable interchange, keep export assets on conventional armature-driven skinning.

Objects without a valid rig assignment in the managed rig list are skipped during export and reported as warnings, preventing silent metadata drift.

## Multi-Rig Support (Future Work)
The current implementation intentionally enforces a single-master-rig workflow because it is the most robust baseline for deterministic crowd swaps, and it mirrors common studio practice for large extras populations.

The metadata schema already includes `compatible_rig` specifically to support a future multi-rig pipeline. The current importer already uses rig IDs as its routing key and accepts optional `SKELETON_MAP` overrides (rig ID -> UE5 skeleton path). A fuller multi-rig pipeline would extend that same mechanism into crowd assembly validation and selection logic.

That extension is straightforward in principle but was deliberately scoped out of the first implementation to keep the initial system reliable, testable, and portfolio-demonstrable.

## Verification
Core pure-Python export and metadata logic is covered in `tests/test_core.py`, including category path mapping, output path generation, and sidecar serialization behavior.
