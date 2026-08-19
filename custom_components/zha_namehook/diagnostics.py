"""Diagnostics for Screen Switch Name Hook."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from . import DATA_RUNTIME, DOMAIN, VERSION
from .handlers.uhome import SUPPORTED_PRODUCTS


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict:
    """Return non-sensitive integration and support-matrix diagnostics."""

    domain_data = hass.data.get(DOMAIN, {})
    runtime = domain_data.get(DATA_RUNTIME, {})
    uhome_runtime = domain_data.get("uhome_runtime", {})
    return {
        "integration_version": VERSION,
        "config_entry_version": entry.version,
        "runtime_loaded": bool(runtime),
        "cached_matter_target_count": len(uhome_runtime.get("targets", {})),
        "supported_matter_products": [
            {
                "matter_vid": spec.matter_vid,
                "matter_pid": spec.matter_pid,
                "uhome_spid": spec.uhome_spid,
                "endpoints": list(spec.endpoints),
            }
            for spec in SUPPORTED_PRODUCTS.values()
        ],
    }
