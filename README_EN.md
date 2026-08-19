# Screen Switch Name Hook — Customer Installation

Version: 2.3.0

[简体中文](README.md) · [Changelog](CHANGELOG.md) · [Supported models JSON](SUPPORTED_MODELS.json)

[![Open this repository in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=zemismart-dev&repository=screen-switch-name-hook&category=integration)

## Purpose

This integration synchronizes each Home Assistant switch entity name to the physical display of a supported Zemismart screen switch. Zigbee (ZHA) and Matter devices share the same integration:

- Matter remains responsible for relay control; screen labels use the device's local uHome protocol.
- Zigbee remains controlled by ZHA; screen labels use the Tuya MCU `0xEF00` name datapoint on endpoint 1.

Matter customers do not enter an IP address. The integration uses the Matter operational network ID and `_matter._tcp.local` to find the current IPv4 address, then validates MAC, SPID, and the exact active endpoint set before every write path. Zigbee devices do not use IP, but must already be joined through ZHA and have the correct Tuya name-DP mapping.

## Supported Matter products

All verified products use Matter VID `5020 / 0x139C`.

| Gangs | Matter PID | uHome SPID | Active endpoints |
| --- | --- | --- | --- |
| 1 | `43832 / 0xAB38` | `4929 / 0x1341` | `1` |
| 2 | `43833 / 0xAB39` | `4930 / 0x1342` | `1, 2` |
| 3 | `43840 / 0xAB40` | `4927 / 0x133F` | `1, 2, 3` |
| 4 | `43831 / 0xAB37` | `4928 / 0x1340` | `1, 2, 3, 4` |

Do not infer the PID from the gang count. The 4-gang PID is `0xAB37`. SPID is not the Matter PID.

## Zigbee (ZHA) support

Zigbee and Matter use the same integration entry and the same `zha_namehook.sync_entity_name` action, but use different transports. The default ZHA name DPs are:

| Channel | Default name DP |
| --- | --- |
| 1 | `105` |
| 2 | `106` |
| 3 | `107` |
| 4 | `108` |

Use these defaults only for Tuya/Zemismart screen switches whose capture, product documentation, or quirk confirms that mapping. Other Zigbee models require explicit overrides in `configuration.yaml`:

```yaml
zha_namehook:
  entity_channels:
    switch.screen_switch_1: 1
    switch.screen_switch_2: 2
  entity_name_dps:
    switch.screen_switch_1: 106
    switch.screen_switch_2: 107
```

Restart Home Assistant after changing YAML. The existing UI integration entry can remain installed. The `106/107` values above demonstrate an override and are not universal values.

## Installation

### Install with HACS (recommended)

1. Make sure HACS is installed and configured in Home Assistant.
2. Click the “Open this repository in HACS” button above.
3. If it does not open automatically, add
   `https://github.com/zemismart-dev/screen-switch-name-hook` as a HACS custom repository with category “Integration”.
4. Download `Screen Switch Name Hook` in HACS.
5. Restart Home Assistant Core.
6. Open Settings → Devices & services → Add integration.
7. Search for `Screen Switch Name Hook` and submit the setup form.
8. Confirm that the target switch is paired through Matter or ZHA and that every relay endpoint can be controlled.

### Manual installation

1. Download the release ZIP or repository source from GitHub.
2. Copy the complete `custom_components/zha_namehook` folder to `/config/custom_components/`.
3. Restart Home Assistant Core.
4. Open Settings → Devices & services → Add integration and add `Screen Switch Name Hook`.

Legacy installations may keep `zha_namehook:` in `configuration.yaml`. New installations do not require YAML.

## Changing a screen name

Recommended: open Settings → Devices & services → Entities, open one switch endpoint, change its Name, and save. Matter endpoint selection prefers the entity unique ID. ZHA uses the YAML mapping and falls back to common entity-ID channel suffixes only when no mapping is configured.

Changing only the `entity_id` does not change the entity Name and does not trigger screen synchronization.

You can also call this action from Developer tools → Actions:

```yaml
action: zha_namehook.sync_entity_name
data:
  entity_id: switch.smart_switch_switch_1
  name: Living Room
```

Omit `name` to use the current entity name. `channel` is normally unnecessary and is provided only as an endpoint override.

ZHA also has a direct action:

```yaml
action: zha_namehook.set_display_name
data:
  ieee: "00:11:22:33:44:55:66:77"
  channel: 1
  name: Living Room
```

Replace the IEEE value with the customer's ZHA device address. Never publish a real IEEE in public logs or issues.

## Name rules

- Leading and trailing whitespace is removed.
- Matter uHome names are limited to 12 Unicode code points; extra characters are truncated without splitting a UTF-8 Chinese character.
- Empty names are rejected.
- Unicode normalization is not applied.
- Emoji and uncommon characters depend on the device font.
- Matter writes one endpoint per request and requires exact device readback.
- ZHA writes raw UTF-8 bytes to the Tuya DP; actual length and font limits depend on that Zigbee firmware.

## Network requirements

- These network requirements apply only to the Matter uHome route. Zigbee only requires the device to remain connected to the same ZHA instance.
- Home Assistant and the Matter switch must be on a mutually reachable trusted LAN.
- Matter mDNS `_matter._tcp.local` must be available.
- UDP 3678 from Home Assistant to the device must be allowed.
- Guest Wi-Fi/AP isolation is unsupported. Routed VLANs require correct mDNS and UDP 3678 forwarding.
- uHome UDP 3678 is a vendor-private, unauthenticated cleartext protocol. Never expose it to the Internet.

## Matter reliability and safety controls

- No fixed IP is stored; an address change triggers exact Matter mDNS rebinding.
- A cached-address timeout gets a quick same-address retry before mDNS refresh.
- If the write ACK is lost, the physical name is read back and success is accepted only on an exact match.
- Rapid changes on one endpoint are serialized with latest-wins behavior.
- Source IP/port, message type, request ID, success, MAC, SPID, and exact active endpoints are validated.
- The name protocol does not issue Matter OnOff commands. Still confirm the relay state required by the site after testing.

## Troubleshooting

1. Search Home Assistant logs for `zha_namehook`.
2. Download integration diagnostics. Diagnostics omit device IP, MAC, Matter operational ID, and names.
3. Matter: confirm normal relay control and that mDNS and UDP 3678 are not blocked.
4. Zigbee: confirm ZHA is in use, input cluster `0xEF00` exists, and the model's real name DP is mapped.
5. Matter: require `uHome screen name verified` and device readback; an HTTP response alone is not proof.
6. ZHA: `Tuya ZHA display name sent` means the frame was handed to the Zigbee stack, not that the screen was read back. Observe the physical display during delivery validation.

## Validated scope

- Home Assistant Core `2026.8.0`
- Home Assistant OS `17.3`
- Live device: 2-gang Matter PID `0xAB39`, uHome SPID `0x1342`, observed firmware `3.7`
- The 1/3/4-gang parameters come from the same validated development handoff matrix, but those products were not each rerun on this company Home Assistant host for this release.
- The ZHA route retains the existing Tuya `0xEF00` name-DP implementation. Each Zigbee model still requires capture/DP-table evidence and physical screen validation.

Before a large rollout, validate each PID once on the customer's Home Assistant version and network, including entity rename, physical display observation, status readback, and DHCP address change.
