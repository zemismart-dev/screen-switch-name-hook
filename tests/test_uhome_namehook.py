"""Tests for Zemismart uHome name transport and identity validation."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock


MODULE_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "zha_namehook"
    / "handlers"
    / "uhome.py"
)
SPEC = importlib.util.spec_from_file_location("zha_namehook_uhome", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
UHOME = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = UHOME
SPEC.loader.exec_module(UHOME)


def _reply(
    request_id: str = "123", *, success: int = 0, msg_type: str = "device_status_reply"
) -> bytes:
    return json.dumps(
        {
            "msg_type": msg_type,
            "payload": {"id": request_id, "success": success, "data": {}},
        }
    ).encode()


class UHomeHelpersTests(unittest.TestCase):
    """Validate product routing, normalization, and strict reply matching."""

    def test_two_gang_product_and_operational_id(self) -> None:
        device = SimpleNamespace(
            manufacturer="Zemismart Technology Limited",
            model_id="43833",
            identifiers={
                (
                    "matter",
                    "deviceid_0123456789ABCDEF-1111111111111111-MatterNodeDevice",
                )
            },
        )
        spec = UHOME.product_spec_from_device_entry(device)
        self.assertIsNotNone(spec)
        self.assertEqual(5020, spec.matter_vid)
        self.assertEqual(4930, spec.uhome_spid)
        self.assertEqual((1, 2), spec.endpoints)
        self.assertEqual(
            "0123456789ABCDEF-1111111111111111",
            UHOME.matter_operational_id_from_device_entry(device),
        )

    def test_name_trim_and_hex_decode(self) -> None:
        self.assertEqual(
            "一二三四五六七八九十一二",
            UHOME.normalize_display_name("  一二三四五六七八九十一二三  "),
        )
        self.assertEqual("switch1", UHOME.decode_display_text("73776974636831"))

    def test_reply_requires_exact_source_port_type_and_id(self) -> None:
        kwargs = {
            "expected_ip": "192.0.2.10",
            "expected_port": 3678,
            "expected_id": "123",
        }
        self.assertIsNone(
            UHOME._parse_matching_reply(
                _reply(), ("192.0.2.11", 3678), **kwargs
            )
        )
        self.assertIsNone(
            UHOME._parse_matching_reply(
                _reply(), ("192.0.2.10", 40000), **kwargs
            )
        )
        self.assertIsNone(
            UHOME._parse_matching_reply(
                _reply(request_id="other"), ("192.0.2.10", 3678), **kwargs
            )
        )
        self.assertIsNone(
            UHOME._parse_matching_reply(
                _reply(msg_type="device_status_set"),
                ("192.0.2.10", 3678),
                **kwargs,
            )
        )
        self.assertEqual(
            0,
            UHOME._parse_matching_reply(
                _reply(), ("192.0.2.10", 3678), **kwargs
            )["payload"]["success"],
        )

    def test_nonzero_success_is_rejected(self) -> None:
        with self.assertRaises(UHOME.UHomeDeviceRejected):
            UHOME._parse_matching_reply(
                _reply(success=5),
                ("192.0.2.10", 3678),
                expected_ip="192.0.2.10",
                expected_port=3678,
                expected_id="123",
            )

    def test_status_identity_and_active_endpoints(self) -> None:
        response = {
            "payload": {
                "success": 0,
                "mac": "02:00:00:00:00:01",
                "spid": "4930",
                "data": {
                    "property": {
                        "endpoint": [2, 1],
                        "display_panel_text": [
                            {"endpoint": 1, "diy_text": "73776974636831"},
                            {"endpoint": 2, "diy_text": "二路"},
                            {"endpoint": 3, "diy_text": "ignored-but-parsed"},
                        ],
                    }
                },
            }
        }
        summary = UHOME.summarize_status(response)
        self.assertEqual([1, 2], summary["endpoints"])
        self.assertEqual("switch1", summary["display_names"][1])
        UHOME.validate_identity(
            summary,
            expected_mac="020000000001",
            spec=UHOME.SUPPORTED_PRODUCTS[43833],
        )

        bad = dict(summary, endpoints=[1, 2, 3])
        with self.assertRaises(UHOME.UHomeIdentityMismatch):
            UHOME.validate_identity(
                bad,
                expected_mac="020000000001",
                spec=UHOME.SUPPORTED_PRODUCTS[43833],
            )


class UHomeReliabilityTests(unittest.IsolatedAsyncioTestCase):
    """Validate ACK-loss recovery and per-endpoint latest-wins behavior."""

    async def test_lost_ack_is_accepted_only_after_matching_readback(self) -> None:
        target = UHOME.UHomeTarget(
            host="192.0.2.10",
            mac="020000000001",
            operational_id="0123456789ABCDEF-1111111111111111",
        )
        original_set = UHOME.async_set_display_name
        original_query = UHOME._async_query_verified
        set_mock = AsyncMock(side_effect=UHOME.UHomeTimeout("lost ACK"))
        query_mock = AsyncMock(return_value={"display_names": {1: "客厅灯"}})
        UHOME.async_set_display_name = set_mock
        UHOME._async_query_verified = query_mock
        try:
            await UHOME._async_write_and_verify(
                target,
                UHOME.SUPPORTED_PRODUCTS[43833],
                endpoint_id=1,
                name="客厅灯",
            )
        finally:
            UHOME.async_set_display_name = original_set
            UHOME._async_query_verified = original_query

        self.assertEqual(1, set_mock.await_count)
        self.assertEqual(1, query_mock.await_count)

    async def test_queued_older_name_is_skipped_in_favor_of_latest(self) -> None:
        hass = SimpleNamespace(data={})
        device = SimpleNamespace(
            manufacturer="Zemismart Technology Limited",
            model_id="43833",
            identifiers={
                (
                    "matter",
                    "deviceid_0123456789ABCDEF-1111111111111111-MatterNodeDevice",
                )
            },
        )
        target = UHOME.UHomeTarget(
            host="192.0.2.10",
            mac="020000000001",
            operational_id="0123456789ABCDEF-1111111111111111",
        )
        key = f"{target.operational_id}:1"
        runtime = UHOME._runtime_state(hass)
        held_lock = UHOME.asyncio.Lock()
        await held_lock.acquire()
        runtime["locks"][key] = held_lock

        original_get = UHOME._async_get_verified_target
        original_write = UHOME._async_write_and_verify
        get_mock = AsyncMock(
            return_value=(target, {"display_names": {1: "switch1"}})
        )
        write_mock = AsyncMock()
        UHOME._async_get_verified_target = get_mock
        UHOME._async_write_and_verify = write_mock
        try:
            older = UHOME.asyncio.create_task(
                UHOME.write_uhome_display_name_for_matter(
                    hass,
                    device_entry=device,
                    endpoint_id=1,
                    name="旧名称",
                )
            )
            await UHOME.asyncio.sleep(0)
            latest = UHOME.asyncio.create_task(
                UHOME.write_uhome_display_name_for_matter(
                    hass,
                    device_entry=device,
                    endpoint_id=1,
                    name="新名称",
                )
            )
            await UHOME.asyncio.sleep(0)
            held_lock.release()
            self.assertTrue(await older)
            self.assertTrue(await latest)
        finally:
            UHOME._async_get_verified_target = original_get
            UHOME._async_write_and_verify = original_write

        self.assertEqual(1, write_mock.await_count)
        self.assertEqual("新名称", write_mock.await_args.kwargs["name"])


if __name__ == "__main__":
    unittest.main()
