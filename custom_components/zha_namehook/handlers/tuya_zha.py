"""Tuya ZHA display-name datapoint writer."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Iterable, Mapping
from typing import Any

_LOGGER = logging.getLogger(__name__)

TUYA_MCU_CLUSTER_ID = 0xEF00
NAME_DP_BY_CHANNEL = {
    1: 105,
    2: 106,
    3: 107,
    4: 108,
}


def _normalize_ieee(value: Any) -> str:
    """Return a stable lower-case IEEE string."""

    if value is None:
        return ""

    try:
        from zigpy.types import EUI64

        return str(EUI64.convert(value)).lower()
    except (ImportError, ValueError, TypeError):
        return str(value).lower()


def get_channel_from_entity_id(entity_id: str) -> int:
    """Infer a screen-switch channel from common ZHA entity ids."""

    patterns = (
        r"_(?:switch|channel|gang|outlet|light|l)(\d+)$",
        r"_(?:switch|channel|gang|outlet|light)_(\d+)$",
        r"_(\d+)$",
    )
    for pattern in patterns:
        match = re.search(pattern, entity_id)
        if match:
            num = int(match.group(1))
            return ((num - 1) % 4) + 1

    return 1


def _zha_ieee_from_device_entry(device_entry) -> str | None:
    """Return a ZHA IEEE identifier from a HA device registry entry."""

    for domain, identifier in getattr(device_entry, "identifiers", set()):
        if domain == "zha":
            return str(identifier)
    return None


def _iter_zha_data_candidates(zha_data: Any) -> Iterable[Any]:
    """Yield likely ZHA gateway/proxy/application objects across HA versions."""

    seen: set[int] = set()
    queue: list[Any] = [zha_data]

    if isinstance(zha_data, Mapping):
        queue.extend(zha_data.values())

    while queue:
        obj = queue.pop(0)
        if obj is None or id(obj) in seen:
            continue
        seen.add(id(obj))
        yield obj

        if isinstance(obj, Mapping):
            queue.extend(value for value in obj.values() if id(value) not in seen)

        for attr in (
            "gateway_proxy",
            "gateway",
            "zha_gateway",
            "application_controller",
            "application",
            "app_controller",
            "zigpy_app_controller",
        ):
            child = getattr(obj, attr, None)
            if child is not None and id(child) not in seen:
                queue.append(child)


def _lookup_mapping_by_ieee(mapping: Any, ieee: str) -> Any | None:
    """Find a device/proxy in a dict-like mapping by IEEE."""

    if not isinstance(mapping, Mapping):
        return None

    wanted = _normalize_ieee(ieee)
    for key, value in mapping.items():
        if _normalize_ieee(key) == wanted:
            return value

        for attr in ("ieee", "ieee_str"):
            if _normalize_ieee(getattr(value, attr, None)) == wanted:
                return value

        nested_device = getattr(value, "device", None)
        if nested_device is not None and _normalize_ieee(
            getattr(nested_device, "ieee", None)
        ) == wanted:
            return value

    return None


def _device_from_zha_data(hass, ieee: str) -> Any | None:
    """Find a ZHA device/proxy using known HA/ZHA storage layouts."""

    zha_data = hass.data.get("zha")
    if zha_data is None:
        _LOGGER.warning("ZHA data is not available in hass.data")
        return None

    for candidate in _iter_zha_data_candidates(zha_data):
        for attr in ("device_proxies", "devices"):
            device = _lookup_mapping_by_ieee(getattr(candidate, attr, None), ieee)
            if device is not None:
                _LOGGER.debug("Found ZHA device via %s.%s", type(candidate), attr)
                return device

    _LOGGER.warning("Could not find ZHA device/proxy for ieee=%s", ieee)
    return None


def _zigpy_endpoint_from_device(device_or_proxy: Any, endpoint_id: int = 1) -> Any | None:
    """Return the zigpy endpoint for a ZHA device/proxy."""

    device = getattr(device_or_proxy, "device", device_or_proxy)
    endpoints = getattr(device, "endpoints", None)
    if not endpoints:
        _LOGGER.warning("ZHA device %s has no endpoints", device_or_proxy)
        return None

    endpoint = endpoints.get(endpoint_id)
    if endpoint is None:
        _LOGGER.warning("ZHA device %s has no endpoint %s", device_or_proxy, endpoint_id)
        return None

    return getattr(endpoint, "zigpy_endpoint", endpoint)


def _mcu_cluster_from_endpoint(zigpy_endpoint: Any) -> Any | None:
    """Return the Tuya MCU cluster from a zigpy endpoint."""

    in_clusters = getattr(zigpy_endpoint, "in_clusters", None)
    if isinstance(in_clusters, Mapping):
        cluster = in_clusters.get(TUYA_MCU_CLUSTER_ID)
        if cluster is not None:
            return cluster

    for attr in ("tuya_mcu", "manufacturer", "tuya_manufacturer"):
        cluster = getattr(zigpy_endpoint, attr, None)
        if cluster is not None:
            return cluster

    _LOGGER.warning("Endpoint %s does not expose Tuya MCU cluster 0xEF00", zigpy_endpoint)
    return None


def _next_sequence(mcu_cluster: Any) -> int | None:
    """Return the next zigpy sequence number across cluster implementations."""

    endpoint = getattr(mcu_cluster, "endpoint", None)
    device = getattr(endpoint, "device", None)
    candidates = (
        getattr(device, "application", None),
        getattr(device, "application_controller", None),
        getattr(endpoint, "application", None),
        getattr(mcu_cluster, "application", None),
    )

    for candidate in candidates:
        get_sequence = getattr(candidate, "get_sequence", None)
        if callable(get_sequence):
            return get_sequence()

    return None


def _build_tuya_command(sequence: int, dp_id: int, name: str) -> Any | None:
    """Build Tuya command objects only when the ZHA dependencies are present."""

    try:
        from zhaquirks.tuya import TuyaCommand, TuyaData, TuyaDatapointData
    except ImportError:
        _LOGGER.warning(
            "ZHA screen-name support is unavailable because zigpy/zhaquirks "
            "is not installed"
        )
        return None

    class RawBytes(TuyaData):
        """Raw byte payload for one Tuya screen-name datapoint."""

        def __init__(self, value: bytes):
            self.raw = value

        def serialize(self) -> bytes:
            length = len(self.raw)
            return b"\x00" + length.to_bytes(2, "big") + self.raw

    return TuyaCommand(
        status=0,
        tsn=sequence,
        datapoints=[
            TuyaDatapointData(
                int(dp_id),
                RawBytes(str(name).encode("utf-8")),
            )
        ],
    )


def _make_command_coro(mcu_cluster: Any, tuya_command: Any):
    """Build a cluster command coroutine across zigpy/zhaquirks signatures."""

    command_id = getattr(mcu_cluster, "mcu_write_command", None)
    if command_id is None:
        _LOGGER.warning("Tuya MCU cluster has no mcu_write_command attribute")
        return None

    last_error: TypeError | None = None
    for kwargs in (
        {"expect_reply": False, "manufacturer": None},
        {"expect_reply": False},
        {},
    ):
        try:
            return mcu_cluster.command(command_id, tuya_command, **kwargs)
        except TypeError as err:
            last_error = err

    _LOGGER.warning("Could not build Tuya MCU command: %s", last_error)
    return None


def _schedule_command(mcu_cluster: Any, command_coro) -> None:
    """Schedule a cluster command with the best available task helper."""

    create_catching_task = getattr(mcu_cluster, "create_catching_task", None)
    if callable(create_catching_task):
        create_catching_task(command_coro)
        return

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(command_coro)
    except RuntimeError:
        asyncio.ensure_future(command_coro)


def _update_cached_name(zigpy_endpoint: Any, channel: int, name: str) -> None:
    """Update local cluster cache when a matching attribute exists."""

    attr_name = f"name_update_{channel}"
    for cluster_attr in ("on_off", "tuya_mcu"):
        cluster = getattr(zigpy_endpoint, cluster_attr, None)
        if cluster is None:
            continue

        update_attribute = getattr(cluster, "update_attribute", None)
        if callable(update_attribute):
            update_attribute(attr_name, name)


async def write_tuya_zha_name(
    hass, *, ieee: str, channel: int, name: str, dp_id: int | None = None
) -> bool:
    """Write a display name to the Tuya ZHA screen-switch name datapoint."""

    resolved_dp_id = int(dp_id) if dp_id is not None else NAME_DP_BY_CHANNEL.get(channel)
    if resolved_dp_id is None or not 1 <= resolved_dp_id <= 255:
        _LOGGER.warning("Unsupported display-name channel: %s", channel)
        return False

    device = _device_from_zha_data(hass, ieee)
    if device is None:
        return False

    zigpy_endpoint = _zigpy_endpoint_from_device(device)
    if zigpy_endpoint is None:
        return False

    mcu_cluster = _mcu_cluster_from_endpoint(zigpy_endpoint)
    if mcu_cluster is None:
        return False

    sequence = _next_sequence(mcu_cluster)
    if sequence is None:
        _LOGGER.warning("Could not obtain a zigpy sequence number for ieee=%s", ieee)
        return False

    tuya_command = _build_tuya_command(
        sequence,
        resolved_dp_id,
        str(name),
    )
    if tuya_command is None:
        return False

    command_coro = _make_command_coro(mcu_cluster, tuya_command)
    if command_coro is None:
        return False

    _schedule_command(mcu_cluster, command_coro)
    _update_cached_name(zigpy_endpoint, channel, str(name))

    _LOGGER.info(
        "Tuya ZHA display name sent: ieee=%s channel=%s dp=%s name=%s",
        ieee,
        channel,
        resolved_dp_id,
        name,
    )
    return True


async def handle_tuya_zha_name_update(
    hass,
    entity_id: str,
    device_entry,
    new_name: str,
    *,
    channel: int | None = None,
    entity_channels: dict[str, int] | None = None,
    entity_name_dps: dict[str, int] | None = None,
) -> bool:
    """Handle a HA entity-name update by writing the matching Tuya name DP."""

    ieee = _zha_ieee_from_device_entry(device_entry)
    if not ieee:
        _LOGGER.debug("Device for %s is not a ZHA device", entity_id)
        return False

    resolved_channel = (
        int(channel)
        if channel is not None
        else (entity_channels or {}).get(entity_id, get_channel_from_entity_id(entity_id))
    )
    resolved_dp_id = (entity_name_dps or {}).get(entity_id)

    return await write_tuya_zha_name(
        hass,
        ieee=ieee,
        channel=resolved_channel,
        name=str(new_name),
        dp_id=resolved_dp_id,
    )
