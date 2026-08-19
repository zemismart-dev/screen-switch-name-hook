"""Tests for the optional Tuya ZHA screen-name route."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


MODULE_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "zha_namehook"
    / "handlers"
    / "tuya_zha.py"
)
SPEC = importlib.util.spec_from_file_location("zha_namehook_tuya_zha", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
TUYA_ZHA = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = TUYA_ZHA
SPEC.loader.exec_module(TUYA_ZHA)


class TuyaZhaRoutingTests(unittest.TestCase):
    """Keep Matter-only imports safe and ZHA channel defaults explicit."""

    def test_module_imports_without_loading_zha_dependencies(self) -> None:
        self.assertFalse(hasattr(TUYA_ZHA, "EUI64"))
        self.assertFalse(hasattr(TUYA_ZHA, "TuyaCommand"))

    def test_default_name_dp_matrix(self) -> None:
        self.assertEqual({1: 105, 2: 106, 3: 107, 4: 108}, TUYA_ZHA.NAME_DP_BY_CHANNEL)

    def test_common_entity_ids_map_to_channels(self) -> None:
        self.assertEqual(1, TUYA_ZHA.get_channel_from_entity_id("switch.panel_switch_1"))
        self.assertEqual(2, TUYA_ZHA.get_channel_from_entity_id("switch.panel_channel2"))
        self.assertEqual(3, TUYA_ZHA.get_channel_from_entity_id("light.panel_gang_3"))
        self.assertEqual(4, TUYA_ZHA.get_channel_from_entity_id("switch.panel_4"))


if __name__ == "__main__":
    unittest.main()
