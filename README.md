# bambu-mqtt-generator

Python library for building, signing, and interpreting Bambu Lab printer MQTT
messages — primarily for reading and changing AMS and external spool filament
settings.

Payload shapes, field types, filament presets, and the AMS response format are
extracted from the Bambu Studio source by
[bambu-mqtt-extractor](https://github.com/Aptimex/bambu-mqtt-extractor), so generated
payloads *should* match what Bambu Studio itself sends for a given printer and firmware.

This library is focused on generating MQTT command payloads, but does not provide a method to send them.
Sending commands can be handled by the separate **bambu-mqtt-comms** companion library, 
which is just a Bambu-focused wrapper around the paho-mqtt library.

This library's code is primarily AI generated and maintained, but the aspects related to AMS and filament settings are validated against real printers to ensure correctness.
Functionality unrelated to that use has NOT been tested or validated. You use this library at your own risk.

## Installation

From a source checkout:

```bash
pip install .          # or: pip install -e .  for development
```

Requires Python 3.8+ and `cryptography`.

## Quick start

```python
from bambu_mqtt_generator import get_payload_builder, ExternalSpool

# Printer model and firmware version
builder = get_payload_builder("A1 mini", "01.05.00.00")

payload = builder.build_filament_setting(
    tray_info_idx="GFA00",      # Bambu PLA Basic
    tray_color="00FF00",        # "RRGGBB" or "RRGGBBAA", optional "#" prefix
    ams_id=0,                   # AMS unit 0-3, or ExternalSpool.MAIN / .DEPUTY
    tray_id=2,                  # slot 0-3 (ignored for an external spool)
)
```

```json
{
  "print": {
    "sequence_id": "21166",
    "ams_id": 0,
    "tray_id": 2,
    "slot_id": 2,
    "tray_info_idx": "GFA00",
    "setting_id": "",
    "tray_color": "00FF00FF",
    "nozzle_temp_min": 190,
    "nozzle_temp_max": 240,
    "tray_type": "PLA",
    "command": "ams_filament_setting"
  }
}
```

Temperatures and type were filled in from the `GFA00` preset because they
weren't supplied.

### Supported printers

`get_payload_builder()` takes either the printer name or the model id.

| Printer | Model ID | | Printer | Model ID |
|---|---|---|---|---|
| X1 Carbon | BL-P001 | | A1 | N2S |
| X1 | BL-P002 | | A2L | N9 |
| X1E | C13 | | H2C | O1C |
| X2D | N6 | | H2C | O1C2 |
| P1P | C11 | | H2D | O1D |
| P1S | C12 | | H2D Pro | O1E |
| P2S | N7 | | H2S | O1S |
| A1 mini | N1 | | | |

Both forms are case-insensitive, and surrounding or repeated whitespace is
ignored. An unrecognized printer raises `ValueError`.

H2C is the one name that isn't accepted: Bambu Studio ships it as two model ids
with different feature flags (`O1C2` is the `O1C2-V2` subseries), so the name
alone doesn't identify a printer. Passing `"H2C"` raises
`AmbiguousPrinterNameError` — a `ValueError` subclass — listing both candidates;
pass `"O1C"` or `"O1C2"`. The printer reports which one it is in `get_version`.

## Complete workflow

Read the AMS, change a slot, and confirm the change actually landed.

```python
import time

from bambu_mqtt_comms import BambuMQTTClient, PrinterConfig
from bambu_mqtt_generator import (
    MQTTSigner, check_command_result, get_payload_builder, load_config,
    load_pem, parse_response,
)

MODEL, FIRMWARE = "N1", "01.05.00.00"

config = load_config()
builder = get_payload_builder(MODEL, FIRMWARE)

signer = MQTTSigner(
    cert_pem=load_pem("certs/cert.pem"),
    key_pem=load_pem("certs/key.pem"),
    cert_chain_pem=load_pem("certs/cert_chain.pem"),
    crl_pem=load_pem("certs/crl.pem"),
)

printer = PrinterConfig(ip="192.168.1.100", serial="01S00A123456789",
                        access_code="abcdef12")

with BambuMQTTClient(printer) as client:
    # 1. Register the certificate; signed commands are rejected until this lands.
    if not client.install_app_cert(signer.build_app_cert_install(),
                                   cert_id=signer.get_cert_id())["trusted"]:
        raise RuntimeError("printer never trusted our certificate")

    # 2. Read the current slots.
    status = client.request_status()
    slots = parse_response({"print": status}, MODEL, FIRMWARE, config)["slots"]
    slot = next(s for s in slots
                if s["ids"]["amsID"] == 0 and s["ids"]["slotID"] == 2)

    # 3. Build, sign, and send.
    payload = builder.build_filament_setting(
        tray_info_idx="GFA00", tray_color="0000FFFF",
        ams_id=slot["ids"]["amsID"], tray_id=slot["ids"]["trayID"],
    )
    ack = client.send_and_wait(signer.sign(payload))

    result = check_command_result(ack)
    if not result["accepted"]:
        raise RuntimeError(f"rejected: {result['err_code']} {result['description']}")

    # 4. An accepted command is not proof of effect — re-read and compare.
    #    The printer applies the change asynchronously, so retry the read.
    for _ in range(5):
        time.sleep(2)
        status = client.request_status()
        slots = parse_response({"print": status}, MODEL, FIRMWARE, config)["slots"]
        after = next(s for s in slots
                     if s["ids"]["amsID"] == 0 and s["ids"]["slotID"] == 2)
        if after["tray_color"].upper() == "0000FFFF":
            break
    else:
        raise RuntimeError(f"accepted but not applied: {after['tray_color']}")
```

## Building payloads

### `build_payload()`

`build_payload()` allows you to generate any payload that Bambu Studio supports, based on the provided config files.
It validates that every field Bambu Studio always sends is present, raising `ValueError` listing what is missing. 

```python
builder.build_payload(
    "ams_filament_settings",
    sequence_id=None, ams_id=0, tray_id=2, slot_id=2,
    tray_info_idx="GFZ99", setting_id="", tray_color="FF0000FF",
    nozzle_temp_min=190, nozzle_temp_max=240, tray_type="PLA",
)
```

However, since the primary goal of this library is to support AMS filament control, there are several dedicated helper functions related to those tasks to make them easier.

### `build_filament_setting()`

The method to use for filament changes. It routes to a physical AMS or an
external spool based on `ams_id`, and derives the ids Bambu Studio would send.

| Parameter | Required | Default | Notes |
|---|---|---|---|
| `tray_info_idx` | yes | — | Filament ID, e.g. `"GFA00"` |
| `tray_color` | yes | — | `"RRGGBB"` or `"RRGGBBAA"`, optional `#`; alpha defaults to `FF` |
| `ams_id` | yes | — | `0`-`3` for a physical AMS, or `ExternalSpool.MAIN` (255) / `.DEPUTY` (254) |
| `tray_id` | yes* | — | Slot `0`-`3`; ignored for an external spool |
| `slot_id` | | from `tray_id` | Physical AMS only; supply either one and the other is derived |
| `nozzle_temp_min` | | from preset | °C |
| `nozzle_temp_max` | | from preset | °C |
| `tray_type` | | from preset | `"PLA"`, `"PETG"`, … |
| `setting_id` | | `""` | Filament preset id; empty means no user preset |
| `sequence_id` | | random | 5-digit string starting with `2` |
| `cols`, `ctype` | | omitted | Multi-color spools; see below |

\* For a physical AMS you must supply `tray_id` or `slot_id`; they must be equal.

Raises `ValueError` for an unknown filament id, an out-of-range AMS or slot id,
or a physical AMS with neither id supplied.

### AMS and external spool ids

Ids are used exactly as the printer reports them in `push_status`, and are
0-based.

| Target | `ams_id` | `tray_id` | `slot_id` |
|---|---|---|---|
| Physical AMS | `0`-`3` | `0`-`3` | same as `tray_id` |
| External spool (main) | `255` | `254` | `0` |
| External spool (deputy) | `254` | `254` | `0` |

### Multi-color spools: `cols` and `ctype`

`tray_color` holds one color. A spool that isn't one flat color is described by
the pair `cols` + `ctype`:

- **`cols`** — the colors on the spool, as `"RRGGBBAA"` strings, in order. For
  a plain spool it is just `[tray_color]`.
- **`ctype`** — *color* type: how those colors are laid out. It has nothing to
  do with the filament material.

| `ctype` | Meaning | Rendered as |
|---|---|---|
| `0` | Gradient — colors blend continuously | a linear gradient across `cols` |
| `1` | Multi-color — discrete bands | one solid segment per entry in `cols` |
| `2` | Single color | one solid color |

`ctype` only carries meaning when `cols` has two or more entries. With a single
color, Bambu Studio ignores the reported value and treats the spool as single
(`2`); printers commonly report `ctype: 0` on a single-color tray, which is not
a gradient but an unset field.

Only printers that advertise manual multi-color editing — bit 23 of the `fun2`
field in `push_status` — accept these; Bambu Studio omits both fields
otherwise and sends the plain single-color command. Many current printers do
not set that bit, so check it before sending `cols`/`ctype`.

> When cross-checking against Bambu Studio, disregard the `DevFilaColorType`
> enum in `DevFilaSystem.h` — its names (`CTYPE_MULTI = 0`,
> `CTYPE_GRADIANT = 1`) contradict the wire values above. The source
> acknowledges this in `wgtFilaManagerColorType.h`: *"DevFilaColorType's legacy
> enumerator names are misleading, so never switch on those names here."*

### Other convenience commands

| Method | Command |
|---|---|
| `build_pushall()` | `pushall` — request a full status push |
| `build_get_version()` | `get_version` |
| `build_get_access_code()` | `get_access_code` |
| `build_ams_change_filament()` | `ams_change_filament` — load/unload |
| `build_ams_control(action)` | `ams_control` — `resume`/`pause`/`abort`/`reset`/`done` |
| `build_gcode(gcode)` | `gcode_line` — raw G-code |
| `build_set_nozzle_temp()` | `set_nozzle_temp` |
| `build_payload(name, **fields)` | any command in `config/commands/` |

Several AMS operations are G-code rather than JSON commands — refresh RFID,
calibrate, and select tray all send `M620` via `build_gcode()`.

## Reading responses

**`parse_response(response, model_id, firmware_version, config=None) -> dict`**

Normalizes a `push_status` into `{"success": True, "slots": [...], "raw": ...}`.
Pass the reply wrapped as `{"print": status}`.

Each slot carries the raw printer fields (`tray_color`, `tray_info_idx`,
`tray_type`, `nozzle_temp_min`/`_max`, …) plus normalized additions:

```python
{
  "ids": {"amsID": 255, "slotID": 0, "trayID": 254, "isExternal": True},
  "Type": "PLA", "Color": "00FF00", "Brand": "Bambu",
  "Min Temp": 190, "Max Temp": 240, "Bed Temp": 55,
  "displayID": "External Spool (no AMS)",
}
```

`ids` is expressed the way `ams_filament_setting` expects it back, so a parsed
slot feeds straight into the builder:

```python
builder.build_filament_setting(
    tray_info_idx="GFA00", tray_color="FF0000",
    ams_id=slot["ids"]["amsID"], tray_id=slot["ids"]["trayID"],
)
```

When verifying a change, compare against the raw `tray_color` (8 characters,
including alpha) rather than the truncated `Color` field.

**`parse_slots(push_status, model_id, firmware_version, config=None) -> list`** —
the slot list alone, taking an unwrapped `push_status`.

**`check_command_result(response) -> dict`**

Interprets a printer acknowledgement:

| Key | Meaning |
|---|---|
| `accepted` | No non-zero `err_code` and no failure `result` |
| `explicit` | The printer actually stated a result; `False` means acceptance was inferred |
| `err_code`, `result`, `reason` | As reported |
| `description` | Human-readable meaning of `err_code`, if known |
| `scope` | Which payload section the ack was found in |

This reports only that the command was *accepted*. It is not evidence that the
change took effect — re-read the state and compare.

## Signing

Printers on firmware newer than (approximately) January 2025 reject unsigned commands by default, unless the printer is placed into LAN-Only and Developer Mode (LAN+DEV mode).

This library does not include or provide ready-to-use certificates or keys for singing. 
However, if you [obtain a key](https://github.com/danielwoz/BambuSlicerKeySaver) and its associated [certificate chain and CRL](https://bambuzled.github.io/posts/bambu-auth-control/#the-current-cert-api), this library enables you to generate certificate bootstrap commands as well as valid signatures for MQTT commands you send.

The companion **bambu-mqtt-comms** library makes it easy to send the appropriate messages to a printer, so these examples will focus on using that library for communication. 
However, they could also be "manually" sent using any generic MQTT library like `paho-mqtt` if you know the internal process.

| Credential | Needed for |
|---|---|
| `cert_pem` | signing |
| `key_pem` | signing |
| `cert_chain_pem` | bootstrap — full chain: leaf + intermediate + root |
| `crl_pem` | bootstrap — certificate revocation list |

Every MQTTSigner() argument also has a `_pem_path` variant that reads the contents from a file stored on disk; don't mix the two argument forms.

```python
signer = MQTTSigner(cert_pem=..., key_pem=...,
                    cert_chain_pem=..., crl_pem=...)

signer.get_cert_id()                    # "<serial hex>CN=<issuer CN>"
signer.build_app_cert_install()         # bootstrap message
signer.sign(payload)                    # dict  -> signed wire string
signer.sign_json('{"print": {...}}')    # string -> signed wire string
```

Signing returns a JSON **string**, not a dict. The signature covers the exact bytes
of the command object, so publish it straight to the appropriate topic — re-serializing
it risks invalidating the signature.

Without `cert_chain_pem` and `crl_pem` provided to the constructor the signer still signs, but
`build_app_cert_install()` raises `ValueError`. That is fine when Bambu Studio
(or whatever Bambu app your key came from) has already bootstrapped the required cert to the printer.

### Bootstrap flow

1. Connect.
2. Publish `app_cert_install` (unsigned) with the chain and CRL.
3. Poll `app_cert_list` until your `cert_id` is listed. The printer never
   acknowledges step 2, and registration is not instant, so a fixed sleep is
   unreliable. Registration is lost on power-cycle, and (for some printers) when a different Bambu application connects to it.
4. Send signed commands.

**bambu-mqtt-comms** does steps 2 and 3 for you:

```python
client.install_app_cert(signer.build_app_cert_install(), signer.get_cert_id())
```

### Error codes

Rejections related to signing arrive as `err_code` in the reply. `describe_error(code)` explains
one; `ERROR_CODES` is the full table of known codes.

| Code | Meaning |
|---|---|
| 84033543 | No signature block on a command that requires one |
| 84033545 | No certificate registered for this `cert_id` — bootstrap needed |
| 84033546 | Malformed payload; checked before signature validation, so it can mask other errors |
| 84033547 | Certificate chain/CRL registration incomplete or invalid |
| 84033548 | Signature doesn't verify |

### Lower-level helpers

| Function | Purpose |
|---|---|
| `sign_payload(payload, private_key_pem, cert_id)` | Sign a dict |
| `sign(payload_json, private_key_pem, cert_id)` | Sign a JSON string |
| `build_app_cert_install(seq_id, cert_chain_pem, crl_pem)` | Build the bootstrap message |
| `compute_cert_id(cert_pem)` | Derive a `cert_id` from a certificate |
| `load_pem(path)` | Read any PEM file |
| `extract_leaf_cert(chain_pem)` | Pull the leaf cert out of a chain |

## Printers and firmware

`get_payload_builder()` accepts a model id or a printer name, both
case-insensitive with extra whitespace ignored — see
[Supported printers](#supported-printers) for the full list. The same applies to
`parse_response()`, `parse_slots()`, and the `ConfigLoader` lookups below.
Unknown printers raise `ValueError`.

Bambu Studio only records a firmware entry where behavior changes, so most
versions inherit an earlier one. Passing a version with no entry of its own
selects the nearest earlier entry and logs that it did; this is expected, not
an error.

```python
from bambu_mqtt_generator import load_config

config = load_config()
config.get_firmware_versions("BL-P001")
# ['00.00.00.00', '01.01.01.00', '01.05.06.01', '01.05.06.05', '01.05.06.06', '01.06.06.00']

config.supports_feature("BL-P001", "01.05.06.06", "support_tunnel_mqtt")   # True
config.get_printer("N1")                                                   # full model config
config.get_filament_preset("GFA00")
# {'filament_id': 'GFA00', 'filament_name': 'Bambu PLA Basic @base',
#  'filament_type': 'PLA', 'nozzle_temp_min': 190, 'nozzle_temp_max': 240, ...}
```

`builder.get_filament_defaults(filament_id)` is the same preset lookup.

## Configuration

Generated by
[bambu-mqtt-extractor](https://github.com/Aptimex/bambu-mqtt-extractor).
**Do not edit configs by hand** — fix the extractor and regenerate.

| Path | Contents |
|---|---|
| `config/printers/` | 15 models, per-firmware feature flags; `names.json` maps printer names to model ids and records names claimed by more than one model |
| `config/commands/` | 48 command specs with field types and requiredness |
| `config/enums/` | 17 enum definitions |
| `config/filament_presets/` | 89 filament presets |
| `config/feature_flags.json` | Feature matrix per model/firmware |
| `config/virtual_ids.json` | Virtual tray ids (255, 254) |
| `config/ams_response_format.json` | Where AMS slots and the external spool live in a `push_status` |

### AMS response format

`ams_response_format.json` is deliberately model-independent: Bambu Studio runs
one parser over every printer and detects the shape at runtime, so consumers
should not branch on model id either.

The external spool arrives in one of two shapes, `vir_slot` taking precedence:

| Shape | Container | Slot id |
|---|---|---|
| `vir_slot` | array | each entry's own `id` — 255 main, 254 deputy |
| `vt_tray` | object | reported `id` is **discarded**; always the main slot (255) |

The second row is easy to get wrong: some printers report `"id": "254"` inside
`vt_tray` and are still the main slot, 255. `parse_response()` applies this
rule, which is why its `ids` can be fed back to the builder unadjusted.

## License and scope

MIT licensed. This is an independent project, not affiliated with or endorsed
by Bambu Lab. It speaks an undocumented protocol that vendor firmware updates
can change without notice.
