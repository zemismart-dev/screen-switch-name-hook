"""Static delivery checks for the customer-facing integration package."""

from __future__ import annotations

import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components" / "zha_namehook"
PACKAGING = ROOT


class NameHookDeliveryTests(unittest.TestCase):
    """Keep the UI setup, translations, version, and privacy contract intact."""

    def test_manifest_exposes_single_instance_config_flow(self) -> None:
        manifest = json.loads((COMPONENT / "manifest.json").read_text())
        self.assertTrue(manifest["config_flow"])
        self.assertEqual([], manifest["dependencies"])
        self.assertEqual("2.3.0", manifest["version"])
        self.assertTrue((COMPONENT / "config_flow.py").is_file())

    def test_version_matches_runtime(self) -> None:
        init_source = (COMPONENT / "__init__.py").read_text()
        self.assertRegex(init_source, r'VERSION = "2\.3\.0"')

    def test_customer_translations_and_diagnostics_exist(self) -> None:
        for relative in (
            "strings.json",
            "translations/en.json",
            "translations/zh-Hans.json",
        ):
            payload = json.loads((COMPONENT / relative).read_text())
            self.assertIn("config", payload)
            self.assertIn("exceptions", payload)
        self.assertTrue((COMPONENT / "diagnostics.py").is_file())

    def test_component_contains_no_customer_specific_network_identity(self) -> None:
        source = "\n".join(
            path.read_text(errors="replace")
            for path in COMPONENT.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        )
        self.assertIsNone(re.search(r"192\.168\.\d{1,3}\.\d{1,3}", source))
        self.assertNotIn("SUPERVISOR_TOKEN", source)

    def test_customer_guides_exist_in_both_languages(self) -> None:
        self.assertTrue((ROOT / "README.md").is_file())
        self.assertTrue((ROOT / "README_EN.md").is_file())

    def test_published_model_matrix_matches_runtime(self) -> None:
        matrix = json.loads((PACKAGING / "SUPPORTED_MODELS.json").read_text())
        published = {
            item["matter_pid"]: (
                item["matter_vid"],
                item["uhome_spid"],
                tuple(item["endpoints"]),
            )
            for item in matrix["products"]
        }
        self.assertEqual(
            {
                43832: (5020, 4929, (1,)),
                43833: (5020, 4930, (1, 2)),
                43840: (5020, 4927, (1, 2, 3)),
                43831: (5020, 4928, (1, 2, 3, 4)),
            },
            published,
        )
        runtime_source = (COMPONENT / "handlers" / "uhome.py").read_text()
        for pid, (vid, spid, endpoints) in published.items():
            endpoint_source = f"{endpoints}".replace(",)", ",)")
            self.assertIn(
                f"{pid}: UHomeProductSpec({vid}, {pid}, {spid}, {endpoint_source})",
                runtime_source,
            )


if __name__ == "__main__":
    unittest.main()
