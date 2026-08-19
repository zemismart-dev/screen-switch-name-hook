"""Matter screen-switch endpoint name routing and User Label fallback."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from aiohttp import ClientError, ClientSession, WSMsgType

_LOGGER = logging.getLogger(__name__)

MATTER_SERVER = "core-matter-server"
MATTER_PORT = 5580
RECV_TIMEOUT = 5

USER_LABEL_CLUSTER_ID = 0x0041
LABEL_LIST_ATTRIBUTE_ID = 0x0000

MATTER_ERROR_MARKERS = (
    "InteractionModelError",
    "UnsupportedCluster",
    "UnsupportedAttribute",
    "UnsupportedWrite",
)


def _matter_node_id_from_device_entry(device_entry: Any) -> int | None:
    """Extract the Matter node id from a HA device registry entry."""

    for domain, identifier in getattr(device_entry, "identifiers", set()):
        if domain != "matter":
            continue

        raw_id = str(identifier)
        if "MatterNodeDevice" not in raw_id:
            continue

        parts = raw_id.split("-")
        if len(parts) < 3:
            continue

        try:
            return int(parts[-2], 16)
        except ValueError:
            _LOGGER.debug("Could not parse Matter node id from identifier=%s", raw_id)

    return None


def _matter_endpoint_from_unique_id(unique_id: str | None) -> int | None:
    """Extract endpoint id from a Matter entity unique id when available."""

    if not unique_id:
        return None

    parts = str(unique_id).split("-")
    try:
        device_marker_index = parts.index("MatterNodeDevice")
    except ValueError:
        return None

    endpoint_index = device_marker_index + 1
    if endpoint_index >= len(parts):
        return None

    try:
        endpoint_id = int(parts[endpoint_index])
    except ValueError:
        return None

    return endpoint_id if endpoint_id > 0 else None


def _matter_endpoint_from_entity_id(entity_id: str) -> int:
    """Fallback endpoint inference for older Matter entity ids."""

    tail = entity_id.rsplit(".", 1)[-1]
    parts = tail.split("_")
    for value in reversed(parts):
        if value.isdigit():
            endpoint_id = int(value)
            return ((endpoint_id - 1) % 4) + 1

    return 1


async def _send_json(ws, message: dict[str, Any]) -> None:
    """Send a JSON command to the Matter Server."""

    _LOGGER.debug(
        "Sending Matter Server message: %s",
        json.dumps(message, ensure_ascii=False),
    )
    await ws.send_json(message)


async def _recv_json(ws, expected_id: str, timeout: int) -> dict[str, Any] | None:
    """Receive the response matching a Matter Server message id."""

    while True:
        try:
            message = await asyncio.wait_for(ws.receive(), timeout=timeout)
        except asyncio.TimeoutError:
            _LOGGER.warning(
                "Timed out waiting for Matter Server response message_id=%s",
                expected_id,
            )
            return None

        if message.type == WSMsgType.TEXT:
            data = json.loads(message.data)
            if str(data.get("message_id")) == expected_id:
                _LOGGER.debug(
                    "Received Matter Server response: %s",
                    json.dumps(data, ensure_ascii=False),
                )
                return data

            _LOGGER.debug(
                "Ignoring non-target Matter Server message: %s",
                json.dumps(data, ensure_ascii=False),
            )
            continue

        if message.type in (WSMsgType.CLOSED, WSMsgType.CLOSE, WSMsgType.ERROR):
            _LOGGER.warning(
                "Matter Server websocket closed while waiting for message_id=%s",
                expected_id,
            )
            return None


def _matter_response_error(response: dict[str, Any] | None) -> str | None:
    """Return a readable error when a Matter Server response failed."""

    if response is None:
        return "no response"

    error_code = response.get("error_code")
    if error_code not in (None, 0, "0"):
        details = response.get("details") or response.get("error")
        return f"error_code={error_code}: {details or 'unknown error'}"

    for key in ("error", "exception"):
        value = response.get(key)
        if value:
            return f"{key}={value}"

    rendered = json.dumps(response, ensure_ascii=False, default=str)
    for marker in MATTER_ERROR_MARKERS:
        if marker in rendered:
            return marker

    return None


def _response_contains_value(value: Any, expected: str) -> bool:
    """Return whether a nested Matter response contains an exact string value."""

    if value == expected:
        return True
    if isinstance(value, dict):
        return any(_response_contains_value(item, expected) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_response_contains_value(item, expected) for item in value)
    return False


async def write_matter_user_label(
    hass,
    *,
    node_id: int,
    endpoint_id: int,
    name: str,
) -> bool:
    """Write a Matter User Label value to one endpoint."""

    attribute_path = (
        f"{endpoint_id}/{USER_LABEL_CLUSTER_ID}/{LABEL_LIST_ATTRIBUTE_ID}"
    )
    label_value = [{"label": "room", "value": str(name)}]
    uri = f"ws://{MATTER_SERVER}:{MATTER_PORT}/ws"

    try:
        async with ClientSession() as session:
            async with session.ws_connect(uri) as ws:
                read_msg_id = f"read_label_node{node_id}_ep{endpoint_id}"
                await _send_json(
                    ws,
                    {
                        "message_id": read_msg_id,
                        "command": "read_attribute",
                        "args": {
                            "node_id": node_id,
                            "attribute_path": attribute_path,
                        },
                    },
                )
                read_resp = await _recv_json(ws, read_msg_id, RECV_TIMEOUT)
                read_error = _matter_response_error(read_resp)
                if read_error:
                    _LOGGER.warning(
                        "Matter endpoint does not expose a writable User Label: "
                        "node=%s endpoint=%s error=%s",
                        node_id,
                        endpoint_id,
                        read_error,
                    )
                    return False

                write_msg_id = f"write_label_node{node_id}_ep{endpoint_id}"
                await _send_json(
                    ws,
                    {
                        "message_id": write_msg_id,
                        "command": "write_attribute",
                        "args": {
                            "node_id": node_id,
                            "attribute_path": attribute_path,
                            "value": label_value,
                        },
                    },
                )
                write_resp = await _recv_json(ws, write_msg_id, RECV_TIMEOUT)

                write_error = _matter_response_error(write_resp)
                if write_error:
                    _LOGGER.warning(
                        "Matter User Label write failed: node=%s endpoint=%s "
                        "name=%s error=%s",
                        node_id,
                        endpoint_id,
                        name,
                        write_error,
                    )
                    return False

                verify_msg_id = f"verify_label_node{node_id}_ep{endpoint_id}"
                await _send_json(
                    ws,
                    {
                        "message_id": verify_msg_id,
                        "command": "read_attribute",
                        "args": {
                            "node_id": node_id,
                            "attribute_path": attribute_path,
                        },
                    },
                )
                verify_resp = await _recv_json(ws, verify_msg_id, RECV_TIMEOUT)
    except (ClientError, TimeoutError, OSError, json.JSONDecodeError) as err:
        _LOGGER.warning(
            "Could not write Matter User Label node=%s endpoint=%s name=%s: %s",
            node_id,
            endpoint_id,
            name,
            err,
        )
        return False

    verify_error = _matter_response_error(verify_resp)
    if verify_error:
        _LOGGER.warning(
            "Matter User Label readback failed: node=%s endpoint=%s name=%s "
            "error=%s",
            node_id,
            endpoint_id,
            name,
            verify_error,
        )
        return False

    if not _response_contains_value(verify_resp, str(name)):
        _LOGGER.warning(
            "Matter User Label readback did not match: node=%s endpoint=%s "
            "expected=%s response=%s",
            node_id,
            endpoint_id,
            name,
            json.dumps(verify_resp, ensure_ascii=False, default=str),
        )
        return False

    _LOGGER.info(
        "Matter User Label verified: node=%s endpoint=%s name=%s",
        node_id,
        endpoint_id,
        name,
    )
    return True


async def handle_matter_name_update(
    hass,
    entity_id: str,
    device_entry,
    new_name: str,
    *,
    endpoint_id: int | None = None,
    unique_id: str | None = None,
) -> bool:
    """Handle a HA entity-name update using the product's supported route."""

    node_id = _matter_node_id_from_device_entry(device_entry)
    if node_id is None:
        _LOGGER.debug("Device for %s is not a Matter node device", entity_id)
        return False

    resolved_endpoint = (
        endpoint_id
        or _matter_endpoint_from_unique_id(unique_id)
        or _matter_endpoint_from_entity_id(entity_id)
    )

    # Zemismart's Matter screen switches expose relay control through Matter,
    # but screen labels through the private uHome UDP protocol. Import lazily
    # so the generic Matter response helpers remain standalone-testable.
    from .uhome import (
        product_spec_from_device_entry,
        write_uhome_display_name_for_matter,
    )

    if product_spec_from_device_entry(device_entry) is not None:
        return await write_uhome_display_name_for_matter(
            hass,
            device_entry=device_entry,
            endpoint_id=resolved_endpoint,
            name=str(new_name),
        )

    return await write_matter_user_label(
        hass,
        node_id=node_id,
        endpoint_id=resolved_endpoint,
        name=str(new_name),
    )
