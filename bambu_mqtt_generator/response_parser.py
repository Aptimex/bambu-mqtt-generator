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
    "slot_presence": {
        "exist_bits": {"tray_key": "tray_exist_bits", "base": 16},
        "ams_type": {"key": "info", "base": 16, "bit_offset": 0,
                     "bit_count": 4, "default": 1},
        "tray_index": {
            "by_ams_type": {
                "1": {"base": 0, "ams_offset": 0, "ams_stride": 4},
                "2": {"base": 0, "ams_offset": 0, "ams_stride": 4},
                "3": {"base": 0, "ams_offset": 0, "ams_stride": 4},
                "4": {"base": 16, "ams_offset": 128, "ams_stride": 1},
                "5": {"base": 24, "ams_offset": 0, "ams_stride": 0},
            }
        },
        "slot_count": {"by_ams_type": {"1": 4, "2": 4, "3": 4, "4": 1}},
        "virtual_slots_always_present": True,
        "filament_info_known": {"requires_all_non_empty": ["tray_type", "tray_color"]},
        "state_field": {
            "key": "state",
            "values": {
                "9": {"present": True, "info_known": False},
                "10": {"present": False, "info_known": False},
                "11": {"present": True, "info_known": True},
            },
        },
    },
}

#: Type shown for a slot holding a spool the AMS has not identified.
UNKNOWN_FILAMENT_TYPE = "UNKNOWN"

#: Type shown for a slot with no spool in it.
EMPTY_FILAMENT_TYPE = "EMPTY"


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

    Slots carry two flags that are easy to conflate:

      * ``present`` — a spool is physically in the slot. This is bit
        ``tray_index`` of ``ams.tray_exist_bits``, which is what Bambu Studio
        uses; the per-tray ``state`` value is only a fallback, since Bambu
        Studio never reads it.
      * ``info_known`` — the printer knows what filament that spool is. A slot
        can be present with nothing else reported about it (an unidentified
        third-party spool, ``state`` 9), and its settings can still be written.
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
        presence = fmt.get("slot_presence") or _DEFAULT_AMS_FORMAT["slot_presence"]

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

        defaults = _DEFAULT_AMS_FORMAT["slot_presence"]
        exist_bits = presence.get("exist_bits", defaults["exist_bits"])
        self.tray_exist_bits_key = exist_bits.get("tray_key", "tray_exist_bits")
        self.exist_bits_base = exist_bits.get("base", 16)

        ams_type = presence.get("ams_type", defaults["ams_type"])
        self.ams_type_key = ams_type.get("key", "info")
        self.ams_type_base = ams_type.get("base", 16)
        self.ams_type_bit_offset = ams_type.get("bit_offset", 0)
        self.ams_type_bit_count = ams_type.get("bit_count", 4)
        self.default_ams_type = ams_type.get("default", 1)

        self.tray_index_by_type = presence.get("tray_index", {}).get("by_ams_type", {})
        self.slot_count_by_type = presence.get("slot_count", {}).get("by_ams_type", {})
        self.virtual_slots_always_present = presence.get(
            "virtual_slots_always_present", True
        )
        self.info_known_fields = presence.get("filament_info_known", {}).get(
            "requires_all_non_empty", ["tray_type", "tray_color"]
        )
        state_field = presence.get("state_field", defaults["state_field"])
        self.state_key = state_field.get("key", "state")
        self.state_values = state_field.get("values", {})

    def parse_slots(self, push_status: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Parse AMS slots from a push_status response.

        Returns a normalized slot list with a consistent structure regardless
        of printer model/firmware, including any external spools. Every slot an
        AMS has is listed, whether or not it holds a spool, so slot numbering
        stays stable.
        """
        slots = []

        ams_data = push_status.get(self.ams_container_key) or {}
        ams_list = ams_data.get(self.ams_array_key) or []
        exist_bits = self._exist_bits(ams_data)

        for ams in ams_list:
            if not isinstance(ams, dict):
                continue
            ams_id = self._to_int(ams.get(self.ams_id_key), default=0)
            ams_type = self._ams_type(ams)

            trays = {}
            for tray in ams.get(self.tray_array_key) or []:
                if not isinstance(tray, dict):
                    continue
                tray_id = self._to_int(tray.get(self.tray_id_key))
                if tray_id is not None:
                    trays[tray_id] = tray

            for tray_id in range(self._slot_count(ams_type, trays)):
                tray = trays.get(tray_id, {})
                slots.append(
                    self._tray_to_slot(
                        ams_id,
                        tray_id,
                        tray,
                        present=self._is_present(
                            exist_bits, ams_id, ams_type, tray_id, tray
                        ),
                    )
                )

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
        """Display defaults for fields a slot did not report."""
        return {
            "tray_info_idx": "",
            "tray_type": "",
            "tray_color": "xxxxxxxx",
            "tray_sub_brands": "",
            "nozzle_temp_min": "0",
            "nozzle_temp_max": "0",
            "bed_temp": "0",
            "k": 0.0,
        }

    def _exist_bits(self, ams_data: Dict[str, Any]) -> Optional[int]:
        """The tray_exist_bits mask, or None if the printer did not report it."""
        raw = ams_data.get(self.tray_exist_bits_key)
        if raw is None:
            return None
        try:
            return int(str(raw).strip(), self.exist_bits_base)
        except (TypeError, ValueError):
            return None

    def _ams_type(self, ams: Dict[str, Any]) -> int:
        """The DevAmsType of an AMS, from bits of its hex `info` field."""
        raw = ams.get(self.ams_type_key)
        if raw is None:
            return self.default_ams_type
        try:
            value = int(str(raw).strip(), self.ams_type_base)
        except (TypeError, ValueError):
            return self.default_ams_type
        mask = (1 << self.ams_type_bit_count) - 1
        return (value >> self.ams_type_bit_offset) & mask

    def _slot_count(self, ams_type: int, trays: Dict[int, Any]) -> int:
        """How many slots this AMS has, never fewer than it reported trays for."""
        configured = self.slot_count_by_type.get(str(ams_type))
        reported = max(trays) + 1 if trays else 0
        if configured is None:
            return max(reported, 1)
        return max(int(configured), reported)

    def _tray_index(self, ams_type: int, ams_id: int, tray_id: int) -> Optional[int]:
        """
        The slot's bit position in tray_exist_bits.

        Bambu Studio's DevAms::GetTrayId: a global index across every AMS,
        laid out differently per AMS type. Types it has no mapping for return
        None (there it asserts and returns -1).
        """
        mapping = self.tray_index_by_type.get(str(ams_type))
        if not mapping:
            return None
        index = (
            mapping.get("base", 0)
            + (ams_id - mapping.get("ams_offset", 0)) * mapping.get("ams_stride", 0)
            + tray_id
        )
        return index if index >= 0 else None

    def _state_meaning(self, tray: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """What the tray's `state` value says, if it is one we know."""
        state = self._to_int(tray.get(self.state_key))
        if state is None:
            return None
        return self.state_values.get(str(state))

    def _is_present(
        self,
        exist_bits: Optional[int],
        ams_id: int,
        ams_type: int,
        tray_id: int,
        tray: Dict[str, Any],
    ) -> bool:
        """Whether a spool is physically in the slot, identified or not."""
        if exist_bits is not None:
            index = self._tray_index(ams_type, ams_id, tray_id)
            if index is not None:
                return bool((exist_bits >> index) & 1)

        # No exist bits (or an AMS type Bambu Studio has no mapping for): fall
        # back to the per-tray state value, then to whether anything was
        # reported about the filament at all.
        meaning = self._state_meaning(tray)
        if meaning is not None:
            return bool(meaning.get("present"))
        return self._has_filament_info(tray)

    def _has_filament_info(self, tray: Dict[str, Any]) -> bool:
        """Whether the printer knows which filament is in the slot."""
        if not tray:
            return False
        meaning = self._state_meaning(tray)
        if meaning is not None and not meaning.get("info_known", True):
            return False
        return all(
            str(tray.get(field) or "").strip() for field in self.info_known_fields
        )

    def _is_virtual(self, ams_id: int) -> bool:
        """Whether an AMS id refers to an external spool rather than an AMS."""
        return ams_id in (self.virtual_main_id, self.virtual_deputy_id)

    def _tray_to_slot(
        self,
        ams_id: int,
        tray_id: int,
        tray: Dict[str, Any],
        present: Optional[bool] = None,
        info_known: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Convert tray data to the normalized slot format."""
        if info_known is None:
            info_known = self._has_filament_info(tray)
        if present is None:
            present = info_known

        # Everything the printer reported, with display defaults standing in
        # for the fields it left out or blanked (an unloaded or unidentified
        # slot reports little more than its id and state).
        slot = dict(tray)
        for key, default in self._empty_tray_dict().items():
            if slot.get(key) in ("", None):
                slot[key] = default

        # The smallest set of data that reliably identifies a slot
        slot["ids"] = {
            "amsID": ams_id,
            "slotID": 0 if self._is_virtual(ams_id) else tray_id,
        }

        # Determine brand from the filament code.
        brand = "Generic"
        current_code = slot.get("tray_info_idx", "")
        if current_code:
            for f in self.config.get_filament_index():
                if f.get("filament_name") and f.get("filament_id") == current_code:
                    brand = f["filament_name"].split()[0]
                    break

        if info_known:
            t = slot.get("tray_type") or UNKNOWN_FILAMENT_TYPE
            sub = slot.get("tray_sub_brands", "")
            if sub:
                t += f" ({sub})"
        else:
            # A present-but-unidentified spool is not an empty slot: its
            # filament settings can still be written.
            t = UNKNOWN_FILAMENT_TYPE if present else EMPTY_FILAMENT_TYPE

        slot["Type"] = t
        slot["Color"] = (slot.get("tray_color") or "xxxxxxxx")[:6]
        slot["Brand"] = brand
        slot["Min Temp"] = self._to_int(slot.get("nozzle_temp_min"), 0) or 0
        slot["Max Temp"] = self._to_int(slot.get("nozzle_temp_max"), 0) or 0
        slot["Bed Temp"] = self._to_int(slot.get("bed_temp"), 0) or 0
        slot["k"] = slot.get("k", 0.0)
        slot["present"] = present
        slot["info_known"] = info_known

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

        An external spool has no exist bit and Bambu Studio never calls one
        empty (StatusPanel.cpp skips the check for virtual slots), so these are
        reported as present regardless of what is on the holder.
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
                    spools.append(
                        self._tray_to_slot(
                            ams_id, 0, entry,
                            present=True if self.virtual_slots_always_present else None,
                        )
                    )
                return spools

            # Object form: the reported id is discarded in favour of the
            # configured slot id (the A1 reports 254 here but is the main slot).
            if not isinstance(raw, dict):
                continue
            ams_id = key_spec.get("forced_id", self.virtual_main_id)
            return [
                self._tray_to_slot(
                    ams_id, 0, raw,
                    present=True if self.virtual_slots_always_present else None,
                )
            ]

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