"""Tests for Matter name response validation."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


MODULE_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "zha_namehook"
    / "handlers"
    / "matter.py"
)
SPEC = importlib.util.spec_from_file_location("zha_namehook_matter", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MATTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MATTER)


class MatterResponseValidationTests(unittest.TestCase):
    """Validate Matter Server success and failure response handling."""

    def test_top_level_error_code_is_rejected(self) -> None:
        response = {"message_id": "1", "error_code": 8, "details": "write failed"}
        self.assertIn("error_code=8", MATTER._matter_response_error(response))

    def test_nested_unsupported_cluster_is_rejected(self) -> None:
        response = {
            "result": {
                "1/65/0": {
                    "TLVValue": None,
                    "Reason": "InteractionModelError: UnsupportedCluster (0xc3)",
                }
            }
        }
        self.assertEqual(
            "InteractionModelError", MATTER._matter_response_error(response)
        )

    def test_success_response_has_no_error(self) -> None:
        response = {
            "message_id": "1",
            "result": {"1/65/0": [{"label": "room", "value": "Desk"}]},
        }
        self.assertIsNone(MATTER._matter_response_error(response))

    def test_readback_requires_exact_nested_value(self) -> None:
        response = {
            "result": {"1/65/0": [{"label": "room", "value": "Desk"}]}
        }
        self.assertTrue(MATTER._response_contains_value(response, "Desk"))
        self.assertFalse(MATTER._response_contains_value(response, "Des"))


if __name__ == "__main__":
    unittest.main()
