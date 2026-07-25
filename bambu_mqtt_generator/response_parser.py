"""
Response parser for Bambu Lab printer MQTT responses.

Parses raw JSON responses from printers and normalizes them based on
printer model/firmware AMS response format differences.
"""

from typing import Any, Dict, List, Optional, Tuple

from .config_loader import ConfigLoader, load_config
from .sign_mqtt import describe_error


# Fallback contract, used only if ams_response_format.json is missing.
# Mirrors Bambu Studio's DeviceManager.cpp / DevFilaSystem.cpp.
_DEFAULT_AMS_FORMAT = {
    "ams": {
        "container_key": "ams",
        "array_key": "ams",
        "id_key": "id",
        "tray_array_key": "tray",
        "tray_id_key": "id",
    },
    "external_spool": {
        "keys": [
            {"key": "vir_slot", "container": "array", "id_source": "entry"},
            {"key": "vt_tray", "container": "object", "id_source": "forced",
             "forced_id": 255},
        ],
        "main_id": 255,
        "deputy_id": 254,
        "packed_id": {"applies_when_id_greater_than": 255},
    },
}

# Trays per physical AMS unit.
_TRAYS_PER_AMS = 4


class ResponseParser:
    """
    Parses and normalizes printer responses.

    Bambu Studio applies one parser to every printer model and firmware and
    decides at runtime which shape it received, so this does the same rather
    than branching on model id. Two shapes exist for the external spool:

      * ``vir_slot`` — an array whose entries each carry their own id
        (255 = main, 254 = deputy).
      * ``vt_tray`` — a single object whose reported id is DISCARDED; the slot
        is always the main slot (255). The A1 reports ``"id": "254"`` here and
        is still the main slot.

    ``vir_slot`` takes precedence when both are present.
    """

    def __init__(self, config: ConfigLoader, model_id: str, firmware_version: str):
        self.config = config
        self.firmware_version = firmware_version
        self.printer = config.get_printer(model_id)

        if not self.printer:
            raise ValueError(f"Unknown printer model: {model_id}")

        # Accepts names and any casing, so record what it resolved to.
        self.model_id = self.printer.get("model_id", model_id)

        fmt = config.get_ams_response_format() or _DEFAULT_AMS_FORMAT
        ams = fmt.get("ams", _DEFAULT_AMS_FORMAT["ams"])
        external = fmt.get("external_spool", _DEFAULT_AMS_FORMAT["external_spool"])

        self.ams_container_key = ams.get("container_key", "ams")
        self.ams_array_key = ams.get("array_key", "ams")
        self.ams_id_key = ams.get("id_key", "id")
        self.tray_array_key = ams.get("tray_array_key", "tray")
        self.tray_id_key = ams.get("tray_id_key", "id")

        self.external_spool_keys = external.get(
            "keys", _DEFAULT_AMS_FORMAT["external_spool"]["keys"]
        )
        self.virtual_main_id = external.get("main_id", 255)
        self.virtual_deputy_id = external.get("deputy_id", 254)
        self.packed_id_threshold = external.get("packed_id", {}).get(
            "applies_when_id_greater_than", 255
        )

    def parse_slots(self, push_status: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Parse AMS slots from a push_status response.

        Returns a normalized slot list with a consistent structure regardless
        of printer model/firmware, including any external spools.
        """
        slots = []

        ams_data = push_status.get(self.ams_container_key) or {}
        ams_list = ams_data.get(self.ams_array_key) or []

        for ams in ams_list:
            if not isinstance(ams, dict):
                continue
            ams_id = self._to_int(ams.get(self.ams_id_key), default=0)

            # Every AMS reports four slots; fill the gaps so slot numbering is
            # stable whether or not a tray is loaded.
            empty_tray = self._empty_tray_dict()
            row = [
                self._tray_to_slot(ams_id, i, empty_tray)
                for i in range(_TRAYS_PER_AMS)
            ]

            for tray in ams.get(self.tray_array_key) or []:
                if not isinstance(tray, dict):
                    continue
                tray_id = self._to_int(tray.get(self.tray_id_key))
                if tray_id is None:
                    continue
                if 0 <= tray_id < _TRAYS_PER_AMS and not self._is_empty_tray(tray):
                    row[tray_id] = self._tray_to_slot(ams_id, tray_id, tray)

            slots.extend(row)

        slots.extend(self._parse_external_spools(push_status))

        return slots

    @staticmethod
    def _to_int(value: Any, default: Optional[int] = None) -> Optional[int]:
        """Coerce a printer-reported id (usually a string) to int."""
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    
    def _empty_tray_dict(self) -> Dict[str, Any]:
        """Return empty tray dict for this firmware."""
        return {
            "tray_info_idx": "",
            "tray_type": "EMPTY",
            "tray_color": "xxxxxxxx",
            "tray_sub_brands": "",
            "nozzle_temp_min": "0",
            "nozzle_temp_max": "0",
            "bed_temp": "0",
            "k": 0.0,
        }
    
    def _is_empty_tray(self, tray: Dict[str, Any]) -> bool:
        """Check if tray is empty."""
        return not tray.get("tray_type")
    
    def _is_virtual(self, ams_id: int) -> bool:
        """Whether an AMS id refers to an external spool rather than an AMS."""
        return ams_id in (self.virtual_main_id, self.virtual_deputy_id)

    def _tray_to_slot(self, ams_id: int, tray_id: int, tray: Dict[str, Any]) -> Dict[str, Any]:
        """Convert tray data to the normalized slot format."""
        slot = dict(tray)

        # Ids are reported the way ams_filament_setting expects them back, so a
        # slot read from push_status can be fed straight into the payload
        # builder. External spools always command slot_id 0 / tray_id 254.
        if self._is_virtual(ams_id):
            slot["ids"] = {
                "amsID": ams_id,
                "slotID": 0,
                "trayID": self.virtual_deputy_id,
                "isExternal": True,
            }
        else:
            slot["ids"] = {
                "amsID": ams_id,
                "slotID": tray_id,
                "trayID": tray_id,
                "isExternal": False,
            }

        # Determine brand from the filament code.
        brand = "Generic"
        current_code = tray.get("tray_info_idx", "")
        if current_code:
            for f in self.config.get_filament_index():
                if f.get("filament_id") == current_code:
                    name = f.get("filament_name") or ""
                    if name:
                        brand = name.split()[0]
                    break

        t = tray.get("tray_type", "EMPTY")
        sub = tray.get("tray_sub_brands", "")
        if sub:
            t += f" ({sub})"

        slot["Type"] = t
        slot["Color"] = (tray.get("tray_color") or "xxxxxxxx")[:6]
        slot["Brand"] = brand
        slot["Min Temp"] = self._to_int(tray.get("nozzle_temp_min"), 0) or 0
        slot["Max Temp"] = self._to_int(tray.get("nozzle_temp_max"), 0) or 0
        slot["Bed Temp"] = self._to_int(tray.get("bed_temp"), 0) or 0
        slot["k"] = tray.get("k", 0.0)

        if ams_id == self.virtual_main_id:
            slot["displayID"] = "External Spool (no AMS)"
        elif ams_id == self.virtual_deputy_id:
            slot["displayID"] = "External Spool (deputy)"
        else:
            slot["displayID"] = f"AMS #{ams_id} | Slot #{tray_id + 1}"

        return slot

    def _decode_slot_id(self, raw_id: Any) -> Optional[int]:
        """
        Decode an external-spool id.

        Ids above 255 pack the ams id in bits 8-15 and the slot id in bits 0-7;
        Bambu Studio decodes them as ``(id >> 8) + (id & 0xff)``.
        """
        value = self._to_int(raw_id)
        if value is None:
            return None
        if value > self.packed_id_threshold:
            return (value >> 8) + (value & 0xFF)
        return value

    def _parse_external_spools(self, push_status: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Parse external spool slots, matching Bambu Studio's detection order.

        The first key present wins: `vir_slot` (array, ids per entry), then
        `vt_tray` (object, id forced to the main slot). If neither is present
        the printer has no external spool.
        """
        for key_spec in self.external_spool_keys:
            key = key_spec.get("key")
            if key not in push_status:
                continue
            raw = push_status[key]

            if key_spec.get("container") == "array":
                if not isinstance(raw, list):
                    continue
                spools = []
                for entry in raw:
                    if not isinstance(entry, dict):
                        continue
                    ams_id = self._decode_slot_id(entry.get("id"))
                    if ams_id is None:
                        ams_id = self.virtual_main_id
                    tray = entry if not self._is_empty_tray(entry) else self._empty_tray_dict()
                    spools.append(self._tray_to_slot(ams_id, 0, tray))
                return spools

            # Object form: the reported id is discarded in favour of the
            # configured slot id (the A1 reports 254 here but is the main slot).
            if not isinstance(raw, dict):
                continue
            ams_id = key_spec.get("forced_id", self.virtual_main_id)
            tray = raw if not self._is_empty_tray(raw) else self._empty_tray_dict()
            return [self._tray_to_slot(ams_id, 0, tray)]

        return []


def parse_response(
    response: Dict[str, Any],
    model_id: str,
    firmware_version: str,
    config: Optional[ConfigLoader] = None,
) -> Dict[str, Any]:
    """
    Parse and normalize a printer response.
    
    Args:
        response: Raw response dict from printer
        model_id: Printer model ID (e.g., "BL-P001", "C11")
        firmware_version: Firmware version string (e.g., "01.05.06.06")
        config: Optional ConfigLoader instance (uses default if not provided)
        
    Returns:
        Normalized response dict with success, data, slots (if push_status)
    """
    if config is None:
        config = load_config()
    
    parser = ResponseParser(config, model_id, firmware_version)
    
    # Extract push_status from response
    push_status = None
    if "print" in response and response["print"].get("command") == "push_status":
        push_status = response["print"]
    elif "print" in response:
        push_status = response["print"]
    
    if push_status:
        slots = parser.parse_slots(push_status)
        return {
            "success": True,
            "slots": slots,
            "raw": response,
        }
    
    return {
        "success": True,
        "data": response,
        "raw": response,
    }


#: Scopes a command acknowledgement can arrive in, in priority order.
_ACK_SCOPES = ("print", "security", "pushing", "info", "system")


def check_command_result(response: Dict[str, Any]) -> Dict[str, Any]:
    """
    Interpret a printer's acknowledgement of a command.

    The printer answers a command on device/<serial>/report with the same
    sequence_id and — depending on the command and firmware — an ``err_code``
    and/or a ``result`` field. A command is treated as accepted when there is
    no non-zero ``err_code`` and ``result`` is not a failure string.

    Note this reports only whether the printer *accepted* the command, not
    whether its effect is visible yet; for that, re-read the state (e.g. via
    a pushall) and compare.

    Args:
        response: Raw response dict from the printer.

    Returns:
        Dict with:
            accepted: bool — printer did not reject the command.
            explicit: bool — the printer actually stated a result/err_code
                      (False means acceptance was inferred from the absence
                      of an error).
            err_code: int or None
            result: str or None (as sent by the printer)
            reason: str or None
            description: str or None — human-readable meaning of err_code
            scope: str or None — which scope the ack was found in
    """
    err_code = None
    result = None
    reason = None
    scope_found = None

    for scope in _ACK_SCOPES:
        section = response.get(scope)
        if not isinstance(section, dict):
            continue
        err_code = section.get("err_code")
        result = section.get("result")
        reason = section.get("reason")
        scope_found = scope
        if err_code is not None or result is not None:
            break

    result_str = result.lower() if isinstance(result, str) else None
    failed = bool(err_code) or result_str in ("fail", "failed", "failure")

    return {
        "accepted": not failed,
        "explicit": err_code is not None or result is not None,
        "err_code": err_code,
        "result": result,
        "reason": reason,
        "description": describe_error(err_code),
        "scope": scope_found,
    }


# Convenience function
def parse_slots(
    push_status: Dict[str, Any],
    model_id: str,
    firmware_version: str,
    config: Optional[ConfigLoader] = None,
) -> List[Dict[str, Any]]:
    """Parse just the slots from a push_status response."""
    if config is None:
        config = load_config()
    parser = ResponseParser(config, model_id, firmware_version)
    return parser.parse_slots(push_status)