"""Zemismart uHome UDP screen-name protocol for Matter switches."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import ipaddress
import json
import logging
import re
import time
from typing import Any

_LOGGER = logging.getLogger(__name__)

UHOME_PORT = 3678
UHOME_TIMEOUT = 3.0
UHOME_FAST_TIMEOUT = 1.25
UHOME_CACHE_RETRY_TIMEOUT = 1.0
UHOME_DISCOVERY_TIMEOUT = 3.0
MATTER_SERVICE_TYPE = "_matter._tcp.local."
MAX_NAME_CODEPOINTS = 12
RUNTIME_DATA_KEY = "uhome_runtime"
HEX_RE = re.compile(r"^[0-9A-Fa-f]+$")
MATTER_OPERATIONAL_ID_RE = re.compile(
    r"deviceid_([0-9A-Fa-f]{16}-[0-9A-Fa-f]{16})-MatterNodeDevice"
)


class UHomeError(RuntimeError):
    """Base error for uHome transport, protocol, and validation failures."""


class UHomeTimeout(UHomeError):
    """No strictly matching uHome response arrived before the deadline."""


class UHomeProtocolError(UHomeError):
    """A matching uHome response was malformed or failed readback."""


class UHomeDeviceRejected(UHomeError):
    """The uHome device returned a non-zero success value."""


class UHomeIdentityMismatch(UHomeError):
    """The uHome responder did not match the Matter device identity."""


@dataclass(frozen=True, slots=True)
class UHomeProductSpec:
    """Expected private-protocol identity for one Zemismart Matter product."""

    matter_vid: int
    matter_pid: int
    uhome_spid: int
    endpoints: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class UHomeTarget:
    """Resolved and identity-bound local uHome target."""

    host: str
    mac: str
    operational_id: str


SUPPORTED_PRODUCTS: dict[int, UHomeProductSpec] = {
    43832: UHomeProductSpec(5020, 43832, 4929, (1,)),
    43833: UHomeProductSpec(5020, 43833, 4930, (1, 2)),
    43840: UHomeProductSpec(5020, 43840, 4927, (1, 2, 3)),
    43831: UHomeProductSpec(5020, 43831, 4928, (1, 2, 3, 4)),
}


def normalize_mac(value: Any) -> str | None:
    """Return a 12-character uppercase MAC without separators."""

    normalized = re.sub(r"[^0-9A-Fa-f]", "", str(value or "")).upper()
    return normalized if len(normalized) == 12 else None


def normalize_display_name(value: Any) -> str:
    """Normalize a display name to the device's 12-code-point limit."""

    return str(value or "").strip()[:MAX_NAME_CODEPOINTS]


def decode_display_text(value: Any) -> str:
    """Decode the hex UTF-8 representation used by status replies."""

    text = str(value or "")
    if text and len(text) % 2 == 0 and HEX_RE.fullmatch(text):
        try:
            decoded = bytes.fromhex(text).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return text
        if decoded and all(char.isprintable() or char.isspace() for char in decoded):
            return decoded
    return text


def matter_operational_id_from_device_entry(device_entry: Any) -> str | None:
    """Extract the operational service instance from a Matter device entry."""

    for domain, identifier in getattr(device_entry, "identifiers", set()):
        if domain != "matter":
            continue
        match = MATTER_OPERATIONAL_ID_RE.search(str(identifier))
        if match:
            return match.group(1).upper()
    return None


def mac_from_hostname(hostname: str | None) -> str | None:
    """Extract the MAC encoded in these devices' mDNS hostnames."""

    if not hostname:
        return None
    first_label = str(hostname).rstrip(".").split(".", 1)[0]
    return normalize_mac(first_label)


def product_spec_from_device_entry(device_entry: Any) -> UHomeProductSpec | None:
    """Return the private-protocol spec for a supported Zemismart product."""

    manufacturer = str(getattr(device_entry, "manufacturer", "") or "").lower()
    if not manufacturer.startswith("zemismart"):
        return None

    raw_pid = getattr(device_entry, "model_id", None)
    try:
        pid = int(str(raw_pid), 0)
    except (TypeError, ValueError):
        return None
    return SUPPORTED_PRODUCTS.get(pid)


def _request_body(msg_type: str, data: dict[str, Any], request_id: str) -> bytes:
    """Build one compact UTF-8 uHome request."""

    return json.dumps(
        {
            "msg_type": msg_type,
            "payload": {"id": request_id, "data": data},
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _parse_matching_reply(
    raw: bytes,
    addr: tuple[str, int],
    *,
    expected_ip: str,
    expected_port: int,
    expected_id: str,
) -> dict[str, Any] | None:
    """Validate source, envelope, request id, and success for a reply."""

    if addr[0] != expected_ip or int(addr[1]) != expected_port:
        return None

    try:
        response = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(response, dict):
        return None

    payload = response.get("payload")
    if (
        response.get("msg_type") != "device_status_reply"
        or not isinstance(payload, dict)
        or str(payload.get("id")) != expected_id
    ):
        return None

    try:
        success = int(payload.get("success"))
    except (TypeError, ValueError) as err:
        raise UHomeProtocolError(
            "matching reply has no numeric payload.success"
        ) from err
    if success != 0:
        raise UHomeDeviceRejected(
            f"device rejected request: success={success} "
            f"message={payload.get('message')!r}"
        )
    return response


class _RequestProtocol(asyncio.DatagramProtocol):
    """Send one request and accept only its strictly matching reply."""

    def __init__(
        self,
        request: bytes,
        *,
        expected_ip: str,
        expected_port: int,
        expected_id: str,
        response_future: asyncio.Future[dict[str, Any]],
    ) -> None:
        self._request = request
        self._expected_ip = expected_ip
        self._expected_port = expected_port
        self._expected_id = expected_id
        self._response_future = response_future

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        transport.sendto(self._request)  # type: ignore[attr-defined]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if self._response_future.done():
            return
        try:
            response = _parse_matching_reply(
                data,
                addr,
                expected_ip=self._expected_ip,
                expected_port=self._expected_port,
                expected_id=self._expected_id,
            )
        except UHomeError as err:
            self._response_future.set_exception(err)
            return
        if response is not None:
            self._response_future.set_result(response)

    def error_received(self, exc: Exception) -> None:
        if not self._response_future.done():
            self._response_future.set_exception(exc)


async def async_send_request(
    host: str,
    msg_type: str,
    data: dict[str, Any],
    *,
    port: int = UHOME_PORT,
    timeout: float = UHOME_TIMEOUT,
) -> dict[str, Any]:
    """Send one uHome request and return a strictly validated response."""

    target_ip = str(ipaddress.IPv4Address(host))
    request_id = str(time.time_ns())
    request = _request_body(msg_type, data, request_id)
    loop = asyncio.get_running_loop()
    response_future: asyncio.Future[dict[str, Any]] = loop.create_future()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: _RequestProtocol(
            request,
            expected_ip=target_ip,
            expected_port=port,
            expected_id=request_id,
            response_future=response_future,
        ),
        remote_addr=(target_ip, port),
    )
    try:
        return await asyncio.wait_for(response_future, timeout=timeout)
    except asyncio.TimeoutError as err:
        raise UHomeTimeout("timeout waiting for a matching device reply") from err
    finally:
        transport.close()


async def async_query_status(
    host: str, *, timeout: float = UHOME_TIMEOUT
) -> dict[str, Any]:
    """Query device status without changing state."""

    return await async_send_request(host, "device_status", {}, timeout=timeout)


async def async_set_display_name(
    host: str,
    endpoint: int,
    name: str,
    *,
    timeout: float = UHOME_TIMEOUT,
) -> tuple[dict[str, Any], str]:
    """Write one endpoint's screen name."""

    normalized = normalize_display_name(name)
    if not normalized:
        raise ValueError("display name must not be empty")
    response = await async_send_request(
        host,
        "device_status_set",
        {
            "display_panel_text": [
                {"endpoint": int(endpoint), "diy_text": normalized}
            ]
        },
        timeout=timeout,
    )
    return response, normalized


def summarize_status(response: dict[str, Any]) -> dict[str, Any]:
    """Extract identity, active endpoints, and decoded display names."""

    payload = response.get("payload")
    if not isinstance(payload, dict):
        raise UHomeProtocolError("response payload is missing")
    data = payload.get("data")
    if not isinstance(data, dict):
        data = {}
    prop = data.get("property")
    if not isinstance(prop, dict):
        prop = {}

    mac = normalize_mac(payload.get("mac")) or normalize_mac(data.get("mac"))

    spid_value = payload.get("spid")
    if spid_value is None:
        spid_value = data.get("spid")
    try:
        spid = int(spid_value) if spid_value is not None else None
    except (TypeError, ValueError):
        spid = None

    endpoints: list[int] = []
    raw_endpoints = prop.get("endpoint")
    if isinstance(raw_endpoints, list):
        for value in raw_endpoints:
            try:
                endpoint = int(value)
            except (TypeError, ValueError):
                continue
            if endpoint not in endpoints:
                endpoints.append(endpoint)
    endpoints.sort()

    names: dict[int, str] = {}
    panel = prop.get("display_panel_text")
    if isinstance(panel, list):
        for item in panel:
            if not isinstance(item, dict):
                continue
            try:
                endpoint = int(item.get("endpoint"))
            except (TypeError, ValueError):
                continue
            names[endpoint] = decode_display_text(item.get("diy_text"))

    return {
        "mac": mac,
        "spid": spid,
        "endpoints": endpoints,
        "display_names": names,
    }


def validate_identity(
    summary: dict[str, Any],
    *,
    expected_mac: str,
    spec: UHomeProductSpec,
) -> None:
    """Require exact MAC, SPID, and active endpoint set."""

    normalized_mac = normalize_mac(expected_mac)
    if normalized_mac is None or summary.get("mac") != normalized_mac:
        raise UHomeIdentityMismatch(
            f"MAC mismatch: expected {normalized_mac}, received {summary.get('mac')}"
        )
    if summary.get("spid") != spec.uhome_spid:
        raise UHomeIdentityMismatch(
            f"SPID mismatch: expected {spec.uhome_spid}, "
            f"received {summary.get('spid')}"
        )
    expected_endpoints = list(spec.endpoints)
    if summary.get("endpoints") != expected_endpoints:
        raise UHomeIdentityMismatch(
            f"endpoint mismatch: expected {expected_endpoints}, "
            f"received {summary.get('endpoints')}"
        )


async def async_resolve_uhome_target(hass, device_entry: Any) -> UHomeTarget:
    """Resolve the Matter service and bind its MAC-derived hostname to uHome."""

    operational_id = matter_operational_id_from_device_entry(device_entry)
    if operational_id is None:
        raise UHomeIdentityMismatch("Matter operational service id is missing")

    from homeassistant.components.zeroconf import async_get_async_instance
    from zeroconf import IPVersion

    zeroconf = await async_get_async_instance(hass)
    service_name = f"{operational_id}.{MATTER_SERVICE_TYPE}"
    service_info = await zeroconf.async_get_service_info(
        MATTER_SERVICE_TYPE,
        service_name,
        timeout=int(UHOME_DISCOVERY_TIMEOUT * 1000),
    )
    if service_info is None:
        raise UHomeError(f"mDNS service not found: {operational_id}")

    expected_mac = mac_from_hostname(service_info.server)
    if expected_mac is None:
        raise UHomeIdentityMismatch(
            f"Matter mDNS hostname does not contain a MAC: {service_info.server!r}"
        )
    addresses = service_info.parsed_addresses(IPVersion.V4Only)
    if not addresses:
        raise UHomeError(f"Matter mDNS service has no IPv4 address: {operational_id}")

    return UHomeTarget(
        host=str(ipaddress.IPv4Address(addresses[0])),
        mac=expected_mac,
        operational_id=operational_id,
    )


def _runtime_state(hass) -> dict[str, Any]:
    """Return the shared cache and per-endpoint latest-wins state."""

    domain_data = hass.data.setdefault("zha_namehook", {})
    return domain_data.setdefault(
        RUNTIME_DATA_KEY,
        {
            "targets": {},
            "locks": {},
            "generations": {},
        },
    )


async def _async_query_verified(
    target: UHomeTarget,
    spec: UHomeProductSpec,
    *,
    timeouts: tuple[float, ...] = (UHOME_FAST_TIMEOUT, UHOME_TIMEOUT),
) -> dict[str, Any]:
    """Query and identity-check status, retrying only transport timeouts."""

    last_error: OSError | UHomeTimeout | None = None
    for timeout in timeouts:
        try:
            summary = summarize_status(
                await async_query_status(target.host, timeout=timeout)
            )
            validate_identity(summary, expected_mac=target.mac, spec=spec)
            return summary
        except (OSError, UHomeTimeout) as err:
            last_error = err
    if last_error is not None:
        raise last_error
    raise UHomeProtocolError("no uHome status-query attempt was made")


async def _async_get_verified_target(
    hass,
    device_entry: Any,
    spec: UHomeProductSpec,
) -> tuple[UHomeTarget, dict[str, Any]]:
    """Use a verified cache first, then rebind through exact Matter mDNS."""

    operational_id = matter_operational_id_from_device_entry(device_entry)
    if operational_id is None:
        raise UHomeIdentityMismatch("Matter operational service id is missing")

    runtime = _runtime_state(hass)
    targets: dict[str, UHomeTarget] = runtime["targets"]
    cached = targets.get(operational_id)
    if cached is not None:
        try:
            return cached, await _async_query_verified(
                cached,
                spec,
                timeouts=(UHOME_CACHE_RETRY_TIMEOUT, UHOME_CACHE_RETRY_TIMEOUT),
            )
        except (OSError, UHomeTimeout):
            targets.pop(operational_id, None)
            _LOGGER.debug(
                "Cached uHome target did not respond; refreshing exact Matter mDNS: "
                "operational_id=%s host=%s",
                operational_id,
                cached.host,
            )

    target = await async_resolve_uhome_target(hass, device_entry)
    summary = await _async_query_verified(target, spec)
    targets[operational_id] = target
    return target, summary


async def _async_write_and_verify(
    target: UHomeTarget,
    spec: UHomeProductSpec,
    *,
    endpoint_id: int,
    name: str,
) -> None:
    """Write with ACK-loss recovery and exact post-write readback."""

    last_error: OSError | UHomeError | None = None
    for attempt, timeout in enumerate((UHOME_FAST_TIMEOUT, UHOME_TIMEOUT), start=1):
        try:
            await async_set_display_name(
                target.host,
                endpoint_id,
                name,
                timeout=timeout,
            )
        except (OSError, UHomeTimeout) as err:
            last_error = err
            # The write may have reached the device even when its ACK was lost.
            try:
                after = await _async_query_verified(target, spec)
            except (OSError, UHomeTimeout) as readback_error:
                last_error = readback_error
                if attempt == 1:
                    continue
                raise readback_error
            if after["display_names"].get(endpoint_id) == name:
                _LOGGER.info(
                    "uHome write ACK was lost but readback matched: host=%s "
                    "endpoint=%s name=%s",
                    target.host,
                    endpoint_id,
                    name,
                )
                return
            if attempt == 1:
                continue
            raise UHomeProtocolError(
                f"write timed out and readback did not match endpoint {endpoint_id}"
            ) from err

        after = await _async_query_verified(target, spec)
        actual_name = after["display_names"].get(endpoint_id)
        if actual_name == name:
            return
        last_error = UHomeProtocolError(
            f"readback mismatch on endpoint {endpoint_id}: "
            f"expected {name!r}, received {actual_name!r}"
        )
        if attempt == 1:
            continue
        raise last_error

    if last_error is not None:
        raise last_error
    raise UHomeProtocolError("no uHome write attempt was made")


async def write_uhome_display_name_for_matter(
    hass,
    *,
    device_entry: Any,
    endpoint_id: int,
    name: str,
) -> bool:
    """Identity-check, write, and read back one Zemismart screen name."""

    spec = product_spec_from_device_entry(device_entry)
    if spec is None:
        return False
    if endpoint_id not in spec.endpoints:
        _LOGGER.warning(
            "Refusing uHome name write for unsupported endpoint: pid=%s endpoint=%s",
            spec.matter_pid,
            endpoint_id,
        )
        return False

    normalized_name = normalize_display_name(name)
    if not normalized_name:
        _LOGGER.warning("Refusing empty uHome display name")
        return False

    raw_trimmed_name = str(name or "").strip()
    if normalized_name != raw_trimmed_name:
        _LOGGER.warning(
            "Truncated uHome display name to %s Unicode code points: original=%s "
            "normalized=%s",
            MAX_NAME_CODEPOINTS,
            raw_trimmed_name,
            normalized_name,
        )

    operational_id = matter_operational_id_from_device_entry(device_entry)
    if operational_id is None:
        _LOGGER.warning("Matter operational service id is missing")
        return False

    runtime = _runtime_state(hass)
    operation_key = f"{operational_id}:{endpoint_id}"
    generations: dict[str, int] = runtime["generations"]
    generations[operation_key] = generations.get(operation_key, 0) + 1
    generation = generations[operation_key]
    locks: dict[str, asyncio.Lock] = runtime["locks"]
    lock = locks.setdefault(operation_key, asyncio.Lock())

    try:
        async with lock:
            if generations[operation_key] != generation:
                _LOGGER.debug(
                    "Skipping superseded uHome name update: operational_id=%s "
                    "endpoint=%s name=%s",
                    operational_id,
                    endpoint_id,
                    normalized_name,
                )
                return True

            target, before = await _async_get_verified_target(
                hass, device_entry, spec
            )
            if before["display_names"].get(endpoint_id) == normalized_name:
                _LOGGER.info(
                    "uHome screen name already current: pid=%s operational_id=%s "
                    "host=%s endpoint=%s name=%s",
                    spec.matter_pid,
                    operational_id,
                    target.host,
                    endpoint_id,
                    normalized_name,
                )
                return True

            try:
                await _async_write_and_verify(
                    target,
                    spec,
                    endpoint_id=endpoint_id,
                    name=normalized_name,
                )
            except (OSError, UHomeTimeout):
                # A cached address can change between verification and write.
                runtime["targets"].pop(operational_id, None)
                target = await async_resolve_uhome_target(hass, device_entry)
                await _async_query_verified(target, spec)
                runtime["targets"][operational_id] = target
                await _async_write_and_verify(
                    target,
                    spec,
                    endpoint_id=endpoint_id,
                    name=normalized_name,
                )
    except (OSError, ValueError, UHomeError) as err:
        _LOGGER.warning(
            "Could not write Zemismart uHome screen name: pid=%s endpoint=%s "
            "name=%s error=%s",
            spec.matter_pid,
            endpoint_id,
            normalized_name,
            err,
        )
        return False

    _LOGGER.info(
        "Zemismart uHome screen name verified: pid=%s operational_id=%s "
        "host=%s endpoint=%s name=%s",
        spec.matter_pid,
        target.operational_id,
        target.host,
        endpoint_id,
        normalized_name,
    )
    return True
