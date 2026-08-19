"""Sync screen-switch entity names to device-side display labels."""

from __future__ import annotations

import asyncio
import logging
import platform
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_registry import EVENT_ENTITY_REGISTRY_UPDATED

from .handlers.matter import handle_matter_name_update
from .handlers.tuya_zha import handle_tuya_zha_name_update, write_tuya_zha_name

DOMAIN = "zha_namehook"
VERSION = "2.3.0"

DATA_RUNTIME = "runtime"

CONF_ENTITY_CHANNELS = "entity_channels"
CONF_ENTITY_NAME_DPS = "entity_name_dps"
CONF_STARTUP_SYNC_IEEES = "startup_sync_ieees"
CONF_STARTUP_SYNC_DELAY = "startup_sync_delay"

SERVICE_SET_DISPLAY_NAME = "set_display_name"
SERVICE_SYNC_ENTITY_NAME = "sync_entity_name"

DEFAULT_STARTUP_SYNC_DELAY = 12

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = vol.Schema(
    {
        vol.Optional(DOMAIN): vol.Any(
            None,
            vol.Schema(
                {
                    vol.Optional(CONF_ENTITY_CHANNELS, default={}): {
                        cv.entity_id: vol.All(vol.Coerce(int), vol.Range(min=1, max=4))
                    },
                    vol.Optional(CONF_ENTITY_NAME_DPS, default={}): {
                        cv.entity_id: vol.All(vol.Coerce(int), vol.Range(min=1, max=255))
                    },
                    vol.Optional(CONF_STARTUP_SYNC_IEEES, default=[]): vol.All(
                        cv.ensure_list, [cv.string]
                    ),
                    vol.Optional(
                        CONF_STARTUP_SYNC_DELAY, default=DEFAULT_STARTUP_SYNC_DELAY
                    ): vol.All(vol.Coerce(float), vol.Range(min=0, max=300)),
                }
            ),
        )
    },
    extra=vol.ALLOW_EXTRA,
)

SET_DISPLAY_NAME_SCHEMA = vol.Schema(
    {
        vol.Required("ieee"): cv.string,
        vol.Required("channel"): vol.All(vol.Coerce(int), vol.Range(min=1, max=4)),
        vol.Required("name"): cv.string,
    }
)

SYNC_ENTITY_NAME_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_id,
        vol.Optional("channel"): vol.All(vol.Coerce(int), vol.Range(min=1, max=4)),
        vol.Optional("name"): cv.string,
    }
)


def _entry_display_name(
    hass: HomeAssistant, entry: er.RegistryEntry, fallback_entity_id: str
) -> str | None:
    """Return the best user-visible name for an entity registry entry."""

    for attr in ("name", "name_by_user"):
        value = getattr(entry, attr, None)
        if value:
            return str(value)

    state = hass.states.get(fallback_entity_id)
    if state is not None:
        friendly_name = state.attributes.get("friendly_name")
        if friendly_name:
            return str(friendly_name)

    original_name = getattr(entry, "original_name", None)
    if original_name:
        return str(original_name)

    return None


def _event_has_name_change(data: dict[str, Any]) -> bool:
    """Return true when an entity registry update changed a visible name."""

    changes = data.get("changes") or {}
    return any(key in changes for key in ("name", "name_by_user", "original_name"))


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up optional legacy YAML configuration."""

    hass.data.setdefault(DOMAIN, {})
    if DOMAIN in config:
        await _async_setup_runtime(
            hass,
            config.get(DOMAIN) or {},
            source="yaml",
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Screen Switch Name Hook from the Home Assistant UI."""

    await _async_setup_runtime(hass, {}, source=entry.entry_id)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a UI-created config entry."""

    domain_data = hass.data.get(DOMAIN, {})
    runtime = domain_data.get(DATA_RUNTIME)
    if runtime is None:
        return True

    runtime["sources"].discard(entry.entry_id)
    if runtime["sources"]:
        return True

    runtime["cancel_listener"]()
    for task in tuple(runtime["tasks"]):
        task.cancel()
    for service in (SERVICE_SET_DISPLAY_NAME, SERVICE_SYNC_ENTITY_NAME):
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)
    domain_data.pop(DATA_RUNTIME, None)
    return True


async def _async_setup_runtime(
    hass: HomeAssistant,
    domain_config: dict[str, Any],
    *,
    source: str,
) -> None:
    """Register services and listeners exactly once for YAML or config flow."""

    domain_data = hass.data.setdefault(DOMAIN, {})
    existing = domain_data.get(DATA_RUNTIME)
    if existing is not None:
        existing["sources"].add(source)
        return

    entity_channels = {
        str(entity_id): int(channel)
        for entity_id, channel in domain_config.get(CONF_ENTITY_CHANNELS, {}).items()
    }
    entity_name_dps = {
        str(entity_id): int(dp_id)
        for entity_id, dp_id in domain_config.get(CONF_ENTITY_NAME_DPS, {}).items()
    }
    startup_sync_ieees = {
        str(ieee).lower() for ieee in domain_config.get(CONF_STARTUP_SYNC_IEEES, [])
    }
    startup_sync_delay = float(
        domain_config.get(CONF_STARTUP_SYNC_DELAY, DEFAULT_STARTUP_SYNC_DELAY)
    )

    runtime: dict[str, Any] = {
        "sources": {source},
        CONF_ENTITY_CHANNELS: entity_channels,
        CONF_ENTITY_NAME_DPS: entity_name_dps,
        CONF_STARTUP_SYNC_IEEES: startup_sync_ieees,
        "tasks": set(),
    }
    domain_data[DATA_RUNTIME] = runtime

    _LOGGER.info(
        "Initializing %s v%s on HA %s, Python %s, platform %s",
        DOMAIN,
        VERSION,
        HA_VERSION,
        platform.python_version(),
        platform.platform(),
    )

    async def _handle_set_display_name(call: ServiceCall) -> None:
        success = await write_tuya_zha_name(
            hass,
            ieee=str(call.data["ieee"]),
            channel=int(call.data["channel"]),
            name=str(call.data["name"]),
        )
        if not success:
            raise HomeAssistantError(
                "The ZHA display name could not be written. Check the device "
                "mapping and Home Assistant logs."
            )

    async def _handle_sync_entity_name(call: ServiceCall) -> None:
        entity_id = str(call.data["entity_id"])
        registry = er.async_get(hass)
        entry = registry.async_get(entity_id)
        if entry is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="entity_not_found",
                translation_placeholders={"entity_id": entity_id},
            )

        device_entry = _device_entry_for_entity(hass, entry)
        if device_entry is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="device_not_found",
                translation_placeholders={"entity_id": entity_id},
            )

        new_name = call.data.get("name") or _entry_display_name(hass, entry, entity_id)
        if not new_name:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="name_missing",
                translation_placeholders={"entity_id": entity_id},
            )

        success = await _sync_device_display_name(
            hass,
            entry,
            entity_id,
            device_entry,
            str(new_name),
            channel=call.data.get("channel"),
            entity_channels=entity_channels,
            entity_name_dps=entity_name_dps,
        )
        if success is not True:
            raise HomeAssistantError(
                f"Could not sync the display name for {entity_id}. Check that "
                "the device is supported. For Matter, check mDNS and UDP 3678; "
                "for ZHA, check cluster 0xEF00 and the configured name DP."
            )

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_DISPLAY_NAME,
        _handle_set_display_name,
        schema=SET_DISPLAY_NAME_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SYNC_ENTITY_NAME,
        _handle_sync_entity_name,
        schema=SYNC_ENTITY_NAME_SCHEMA,
    )

    async def _entity_registry_update_handler(event) -> None:
        await async_entity_registry_update_handler(
            hass,
            event,
            entity_channels=entity_channels,
            entity_name_dps=entity_name_dps,
        )

    cancel_listener = hass.bus.async_listen(
        EVENT_ENTITY_REGISTRY_UPDATED, _entity_registry_update_handler
    )
    runtime["cancel_listener"] = cancel_listener

    if startup_sync_ieees:
        task = hass.async_create_task(
            _startup_sync_known_zha_names(
                hass,
                startup_sync_ieees=startup_sync_ieees,
                entity_channels=entity_channels,
                entity_name_dps=entity_name_dps,
                delay=startup_sync_delay,
            )
        )
        runtime["tasks"].add(task)
        task.add_done_callback(runtime["tasks"].discard)

    _LOGGER.info(
        "%s ready: services registered, entity listener active, startup sync devices=%s",
        DOMAIN,
        len(startup_sync_ieees),
    )


def _device_entry_for_entity(
    hass: HomeAssistant, entry: er.RegistryEntry
) -> dr.DeviceEntry | None:
    """Return the device registry entry for an entity registry entry."""

    device_id = getattr(entry, "device_id", None)
    if not device_id:
        return None

    return dr.async_get(hass).async_get(device_id)


async def _startup_sync_known_zha_names(
    hass: HomeAssistant,
    *,
    startup_sync_ieees: set[str],
    entity_channels: dict[str, int],
    entity_name_dps: dict[str, int],
    delay: float,
) -> None:
    """Sync configured devices once after Home Assistant has started."""

    await asyncio.sleep(delay)
    registry = er.async_get(hass)

    for entry in registry.entities.values():
        if not getattr(entry, "device_id", None):
            continue

        device_entry = _device_entry_for_entity(hass, entry)
        if device_entry is None:
            continue

        zha_ieee = _zha_ieee_from_device(device_entry)
        if not zha_ieee or zha_ieee.lower() not in startup_sync_ieees:
            continue

        name = _entry_display_name(hass, entry, entry.entity_id)
        if not name:
            continue

        await handle_tuya_zha_name_update(
            hass,
            entry.entity_id,
            device_entry,
            name,
            entity_channels=entity_channels,
            entity_name_dps=entity_name_dps,
        )


async def async_entity_registry_update_handler(
    hass: HomeAssistant,
    event,
    *,
    entity_channels: dict[str, int] | None = None,
    entity_name_dps: dict[str, int] | None = None,
) -> None:
    """Dispatch entity name updates to the matching device display-name writer."""

    data = event.data
    if data.get("action") != "update" or not _event_has_name_change(data):
        return

    entity_id = data.get("entity_id")
    if not entity_id:
        return

    registry = er.async_get(hass)
    entry = registry.async_get(entity_id)
    if entry is None:
        _LOGGER.debug("Updated entity %s is not in the registry", entity_id)
        return

    new_name = _entry_display_name(hass, entry, entity_id)
    if not new_name:
        _LOGGER.debug("Updated entity %s has no name to sync", entity_id)
        return

    device_entry = _device_entry_for_entity(hass, entry)
    if device_entry is None:
        _LOGGER.debug("Updated entity %s has no device entry", entity_id)
        return

    success = await _sync_device_display_name(
        hass,
        entry,
        entity_id,
        device_entry,
        new_name,
        entity_channels=entity_channels or {},
        entity_name_dps=entity_name_dps or {},
    )
    if success is False:
        _LOGGER.warning("Failed to sync display name for entity %s", entity_id)


def _zha_ieee_from_device(device_entry: dr.DeviceEntry) -> str | None:
    """Return a ZHA IEEE identifier from a device registry entry."""

    for domain, identifier in device_entry.identifiers:
        if domain == "zha":
            return str(identifier)
    return None


def _is_matter_device(device_entry: dr.DeviceEntry) -> bool:
    """Return true when a device registry entry belongs to Matter."""

    return any(domain == "matter" for domain, _ in device_entry.identifiers)


async def _sync_device_display_name(
    hass: HomeAssistant,
    entry: er.RegistryEntry,
    entity_id: str,
    device_entry: dr.DeviceEntry,
    new_name: str,
    *,
    channel: int | None = None,
    entity_channels: dict[str, int] | None = None,
    entity_name_dps: dict[str, int] | None = None,
) -> bool | None:
    """Sync a display name to ZHA Tuya DPs or Matter User Label."""

    if _zha_ieee_from_device(device_entry):
        return await handle_tuya_zha_name_update(
            hass,
            entity_id,
            device_entry,
            new_name,
            channel=channel,
            entity_channels=entity_channels or {},
            entity_name_dps=entity_name_dps or {},
        )

    if _is_matter_device(device_entry):
        return await handle_matter_name_update(
            hass,
            entity_id,
            device_entry,
            new_name,
            endpoint_id=channel,
            unique_id=getattr(entry, "unique_id", None),
        )

    _LOGGER.debug("Entity %s is not a supported ZHA or Matter device", entity_id)
    return None
