import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.blender.core import (
    CATEGORY_FOLDERS,
    build_export_output_path,
    build_metadata,
    get_addon_id,
    get_preferences_bl_idname,
    write_metadata_sidecar,
)


class TestCore(unittest.TestCase):
    def test_category_folders_are_mapped(self):
        self.assertEqual(CATEGORY_FOLDERS["character_body"], "characters")
        self.assertEqual(CATEGORY_FOLDERS["top"], "tops")
        self.assertEqual(CATEGORY_FOLDERS["accessory"], "accessories")

    def test_get_addon_id_resolution(self):
        self.assertEqual(get_addon_id("crowd_diversity_pipeline"), "crowd_diversity_pipeline")
        self.assertEqual(
            get_addon_id("my_extension.crowd_diversity_pipeline"),
            "my_extension",
        )
        self.assertEqual(
            get_addon_id("my_extension.src.blender"),
            "my_extension",
        )
        self.assertEqual(
            get_addon_id("bl_ext.user_default.crowd_diversity_pipeline"),
            "crowd_diversity_pipeline",
        )
        self.assertEqual(
            get_addon_id("bl_ext.user_default.crowd_diversity_pipeline.src.blender"),
            "crowd_diversity_pipeline",
        )
        self.assertEqual(get_addon_id(None), "crowd_diversity_pipeline")

    def test_get_preferences_bl_idname_resolution(self):
        self.assertEqual(
            get_preferences_bl_idname("bl_ext.user_default.crowd_diversity_pipeline"),
            "bl_ext.user_default.crowd_diversity_pipeline",
        )
        self.assertEqual(
            get_preferences_bl_idname("bl_ext.user_default.crowd_diversity_pipeline.src.blender"),
            "bl_ext.user_default.crowd_diversity_pipeline",
        )
        self.assertEqual(
            get_preferences_bl_idname("my_extension.src.blender"),
            "my_extension",
        )

    def test_build_export_output_path_uses_category_folder(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            library_root = Path(tmp_dir) / "library"
            output_path = build_export_output_path(str(library_root), "top", "My Shirt")
            self.assertTrue(output_path.endswith(f"{Path('tops') / 'My_Shirt.usd'}"))

    def test_metadata_and_sidecar_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            export_path = str(Path(tmp_dir) / "sample.usd")
            metadata = build_metadata(
                category="hair",
                object_name="Buzzcut",
                source_file="/tmp/source.blend",
                slot="head",
                exclusivity_tags=["head_covering"],
                compatible_rig="mixamo_v1",
            )
            sidecar_path = write_metadata_sidecar(export_path, metadata)

            self.assertTrue(Path(sidecar_path).exists())
            saved = json.loads(Path(sidecar_path).read_text(encoding="utf-8"))
            self.assertEqual(saved["category"], "hair")
            self.assertEqual(saved["slot"], "head")
            self.assertEqual(saved["exclusivity_tags"], ["head_covering"])
            self.assertEqual(saved["compatible_rig"], "mixamo_v1")


if __name__ == "__main__":
    unittest.main()
