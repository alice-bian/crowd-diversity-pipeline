# Blender UE5 USD Crowd Pipeline

This repository contains a Blender 5 extension that turns a folder of rigged garment meshes into a USD-based asset library for a crowd diversity pipeline.

## What is included

- Blender extension manifest at [blender_manifest.toml](blender_manifest.toml)
- Add-on package at [crowd_diversity_pipeline](crowd_diversity_pipeline)
- Batch USD export operator with JSON metadata sidecars
- Per-object category assignment in the panel so mixed types can be exported in one run
- Fit-check operator with `Original`, `Neutral`, `A-Pose`, and `T-Pose` options
- 3D Viewport sidebar panel for library output, selected-asset category assignment, and fit-check actions
- Export convenience behavior: deselect all after export and open the Library Root folder in Explorer/Finder

## Installation in Blender

1. Open Blender 5.x.
2. Go to Edit > Preferences > Add-ons.
3. Choose Install from Disk and select the extension package (zip or root extension folder).
4. Enable the add-on named "Crowd Diversity USD Pipeline".

## Usage

1. Select one or more rigged mesh objects in Blender.
2. Choose an output library root in the add-on preferences or panel.
3. In `Selected Asset Types`, assign a category for each selected object (`Hair`, `Top`, `Bottom`, `Shoes`, `Accessory`).
4. Optional: run `Fit Check` using `Neutral`, `A-Pose`, or `T-Pose`.
5. `Fit Check` poses the original rigged assets (it does not create temporary duplicate meshes).
6. To restore the saved pre-fit-check state, choose `Original` and run `Fit Check`.
7. Click `Export Selected Assets`.
8. The add-on exports one USD + one JSON sidecar per selected object, deselects all objects, and opens the Library Root folder.

## Metadata Sidecar

Each exported asset writes a JSON sidecar containing at least:

- `category`
- `slot`
- `exclusivity_tags`
- `source_file`
- `export_date`
- `blender_version`
- `source_asset_name`

## Verification

- Core export and metadata helpers are covered by [tests/test_core.py](tests/test_core.py).
- Syntax checks can be run with `python -m py_compile` on files in [crowd_diversity_pipeline](crowd_diversity_pipeline).
