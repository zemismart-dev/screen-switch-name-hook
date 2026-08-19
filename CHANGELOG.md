# Screen Switch Name Hook changelog

## 2.3.0 — 2026-08-19

- Added Home Assistant UI setup and Simplified Chinese/English translations.
- Added exact Matter mDNS discovery, cached-address retry and DHCP rebinding.
- Added strict uHome reply, MAC, SPID and active-endpoint validation.
- Added write ACK-loss recovery with mandatory screen-name readback.
- Added per-endpoint serialization and latest-wins behavior for rapid renames.
- Added actionable service errors, privacy-safe diagnostics and bilingual customer guides.
- Removed the hard dependency on ZHA so Matter-only customer installations can load.
- Published the verified 1/2/3/4-gang VID, PID, SPID and endpoint matrix.

## 2.2.0 — 2026-08-19

- Added the Zemismart uHome UDP 3678 route for supported Matter screen switches.
- Added automatic Matter operational-ID to IPv4 resolution and post-write readback.
- Kept standard Matter User Label as the fallback for unrelated Matter devices.

## 2.1.1

- Rejected unsupported Matter User Label responses instead of reporting false success.
