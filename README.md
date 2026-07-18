# bambu-mqtt-generator

Python library for generating MQTT JSON payloads to control Bambu Lab printer AMS and external spool settings.

Extracted from Bambu Studio source code, this library provides validated payload generation with printer model + firmware awareness.

## Installation

```bash
pip install -e /path/to/bambu-mqtt-generator
```

## Quick Start

```python
from bambu_ams_payloads import get_payload_builder, ExternalSpool
import json

# 1. Create builder for your printer model + firmware version
# Model ID: BL-P001 (X1 Carbon), BL-P002 (X1), C11 (P1P), C12 (P1S), C13 (X1E), N2S (A1), etc.
# Printer name: "X1 Carbon", "P1P", "A1", "A1 mini", "X1E", "P1S", etc. (case-insensitive)
# Firmware version: "01.05.06.06", "01.03.50.01", etc.
builder = get_payload_builder("BL-P001", "01.05.06.06")
# Or use printer name:
builder = get_payload_builder("X1 Carbon", "01.05.06.06")
builder = get_payload_builder("A1", "01.00.00.00")
builder = get_payload_builder("A1 mini", "01.00.00.00")

# Build the payload using the unified method
payload = builder.build_filament_setting(
    tray_info_idx="GFA00",      # Filament ID (e.g., "GFA00", "GFU01")
    tray_color="00FF00",        # RGBA hex: 6-char "RRGGBB" or 8-char "RRGGBBAA", optional `#` prefix
    ams_id=ExternalSpool.MAIN,  # REQUIRED: ExternalSpool.MAIN (255) or DEPUTY (254), or 1-4 for physical AMS
    tray_id=0,                   # REQUIRED (ignored for virtual/external trays)
)

# Send via MQTT
# Topic: device/<your_device_id>/request
import json
print(json.dumps(payload, indent=2))
```

**Output:**

```json
{
  "print": {
    "sequence_id": "84608333",
    "ams_id": 255,
    "tray_id": 254,
    "slot_id": 0,
    "tray_info_idx": "GFA00",
    "setting_id": "",
    "tray_color": "00FF00FF",
    "nozzle_temp_min": 220,
    "nozzle_temp_max": 220,
    "tray_type": "PLA",
    "cols": null,
    "ctype": null,
    "command": "ams_filament_setting"
  }
}
```

## Complete Usage Examples

```python
from bambu_ams_payloads import get_payload_builder, ExternalSpool

builder = get_payload_builder("X1 Carbon", "01.05.06.06")

# === External Spool (Main) ===
payload = builder.build_filament_setting(
    tray_info_idx="GFA00",      # Bambu PLA Basic
    tray_color="00FF00",        # Green (6-char OK)
    ams_id=ExternalSpool.MAIN,  # 255
    tray_id=0,                  # Required (ignored for virtual)
)

# External Spool (Deputy)
payload = builder.build_filament_setting(
    tray_info_idx="GFA00",
    tray_color="00FF00",
    ams_id=ExternalSpool.DEPUTY,  # 254
    tray_id=0,
)

# === Physical AMS ===
# Provide tray_id (slot_id derived) or slot_id (tray_id derived)
payload = builder.build_filament_setting(
    tray_info_idx="GFA00",
    tray_color="00FF00",
    ams_id=1,      # AMS unit 1-4
    tray_id=0,     # Tray 0-3 (slot_id derived)
    # slot_id=0,   # Or provide slot_id (tray_id derived)
)

# Use filament preset defaults (auto-fills temps & type)
filament = builder.get_filament_defaults("GFU02")  # Bambu PETG Basic
payload = builder.build_filament_setting(
    tray_info_idx=filament["filament_id"],
    tray_color=filament["color"],
    ams_id=ExternalSpool.MAIN,
    tray_id=0,
    setting_id="my-preset-id",
    # nozzle_temp_min/max, tray_type auto-filled from preset
)

# Explicit override of preset values
payload = builder.build_filament_setting(
    tray_info_idx="GFA00",
    tray_color="00FF00",
    ams_id=ExternalSpool.MAIN,
    tray_id=0,
    nozzle_temp_min=200,      # Override preset
    nozzle_temp_max=240,      # Override preset
    tray_type="CUSTOM",       # Override preset
)

# Custom sequence_id (defaults to random 8-digit)
payload = builder.build_filament_setting(
    tray_info_idx="GFA00",
    tray_color="00FF00",
    ams_id=ExternalSpool.MAIN,
    tray_id=0,
    sequence_id="12345678",
)

# Other commands
payload = builder.build_get_version()
payload = builder.build_get_access_code()
payload = builder.build_payload("request_push_all")
payload = builder.build_ams_change_filament(...)
payload = builder.build_ams_control("resume")  # "resume", "pause", "abort", "reset", "done"
```

## Required vs Optional Parameters

### `build_filament_setting()`

| Parameter | Required | Default | Notes |
|---|---|---|---|
| `tray_info_idx` | ✅ | - | Filament ID (e.g., "GFA00", "GFU01") |
| `tray_color` | ✅ | - | RGBA hex: 6-char "RRGGBB" or 8-char "RRGGBBAA", optional `#` prefix |
| `ams_id` | ✅ | - | `ExternalSpool.MAIN` (255), `ExternalSpool.DEPUTY` (254), or 1-4 for physical AMS |
| `tray_id` | ✅ | - | Tray ID (0-3 for physical AMS, ignored for external) |
| `nozzle_temp_min` | | from preset | Min nozzle temp (°C) |
| `nozzle_temp_max` | | from preset | Max nozzle temp (°C) |
| `tray_type` | | from preset | Filament type: "PLA", "PETG", "ABS", etc. |
| `setting_id` | | `""` | Filament preset ID |
| `slot_id` | | auto | Physical AMS: slot 0-3 (defaults to `tray_id`) |
| `sequence_id` | | random 8-digit | Custom sequence ID |

**ExternalSpool constants:**

| Constant | Value | Description |
|---|---|---|
| `ExternalSpool.MAIN` | 255 | Main external spool |
| `ExternalSpool.DEPUTY` | 254 | Deputy external spool |

**Routing logic:**

| Args provided | Treated as |
|---|---|
| `ams_id=ExternalSpool.MAIN` | External spool (MAIN) |
| `ams_id=ExternalSpool.DEPUTY` | External spool (DEPUTY) |
| `ams_id=1-4` + `tray_id=0-3` | Physical AMS (slot_id = tray_id) |
| `ams_id=1-4` + `slot_id=0-3` | Physical AMS (tray_id = slot_id) |

**Error handling:** Raises `TypeError` if `ams_id` missing. Raises `ValueError` if physical AMS IDs incomplete or invalid.

## Other Available Commands

| Method | Command | Purpose |
|---|---|---|
| `build_get_version()` | `get_version` | Request printer firmware version |
| `build_get_access_code()` | `get_access_code` | Get LAN access code |
| `build_payload("request_push_all")` | `pushall` | Request full status push |
| `build_ams_change_filament()` | `ams_change_filament` | Load/unload filament |
| `build_ams_control()` | `ams_control` | Resume/pause/abort AMS |
| `build_payload("pushing", sequence_id="1")` | `pushing` | Start/stop status push |
| `build_gcode()` | `gcode` | Execute raw G-code |

...and 40+ more (see `config/commands/` for full list).

All commands accept optional `sequence_id` (defaults to random 8-digit string).

## Printer Model IDs

| Model ID | Printer |
|---|---|
| BL-P001 | X1 Carbon |
| BL-P002 | X1 |
| C11 | P1P |
| C12 | P1S |
| C13 | X1E |
| N1 | A1 mini |
| N2S | A1 |
| N6 | X2D |
| N7 | P2S |
| N9 | A2L |
| O1C | H2C |
| O1C2 | H2C (v2) |
| O1D | H2D |
| O1E | H2D Pro |
| O1S | H2S |

Use `config.get_printer(model_id)` to get full details for any model.

The builder accepts either a **model ID** or a **printer name**. Printer name matching is case-insensitive and ignores extra whitespace.

```python
builder = get_payload_builder("  X1 Carbon  ", "01.05.06.06")  # Works!
builder = get_payload_builder("a1 mini", "01.00.00.00")        # Works!
```

Unknown printers raise `ValueError: Unknown printer model: ...`

## Firmware Versions

```python
# Get available firmware versions for a printer
config = load_config()
versions = config.get_firmware_versions("BL-P001")
# ['00.00.00.00', '01.01.01.00', '01.05.06.01', '01.05.06.05', '01.05.06.06', '01.06.06.00']

# Check feature support
config.supports_feature("BL-P001", "01.05.06.06", "support_tunnel_mqtt")  # True
```

## MQTT Topic

All commands are sent to:
```
device/<your_printer_device_id>/request
```

The response comes on the notification topic for that device.

## Filament Presets

All 96 filament presets are extracted from Bambu Studio profiles, including Bambu Lab official filaments and Generic/third-party filaments from Anker, Creality, Voron, etc. Each preset includes `nozzle_temp_min`, `nozzle_temp_max`, `bed_temp`, `color`, and `filament_type` auto-filled from base profile inheritance.

See `config/filament_presets/` for the complete list of available filaments.

Use `builder.get_filament_defaults("GFA00")` to get defaults for any filament ID.

## Configuration Source

All configs are extracted from Bambu Studio source code:
- `config/printers/` - 15 models with per-firmware feature flags
- `config/commands/` - 53 command specs with field validation
- `config/enums/` - 17 enum definitions
- `config/filament_presets/` - 96 filament presets
- `config/feature_flags.json` - Feature matrix per model/firmware
- `config/virtual_ids.json` - Virtual tray IDs (255, 254)