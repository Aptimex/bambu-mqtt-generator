"""
Response parser for Bambu Lab printer MQTT responses.

Parses raw JSON responses from printers and normalizes them based on
printer model/firmware AMS response format differences.
"""

from typing import Any, Dict, List, Optional, Tuple

from .config_loader import ConfigLoader, load_config


class ResponseParser:
    """
    Parses and normalizes printer responses based on AMS response format.
    
    Different printer models/firmwares return different JSON structures for
    AMS and external spool data. This parser uses the format info from
    printer configs to normalize responses.
    """
    
    def __init__(self, config: ConfigLoader, model_id: str, firmware_version: str):
        self.config = config
        self.model_id = model_id
        self.firmware_version = firmware_version
        self.printer = config.get_printer(model_id)
        
        if not self.printer:
            raise ValueError(f"Unknown printer model: {model_id}")
        
        # Get AMS response format for this firmware version
        # The format details are nested in the "print" object in firmware config
        fw_config = self.printer.get("firmware_versions", {}).get(firmware_version, {})
        print_config = fw_config.get("print", {})
        self.ams_format = print_config.get("ams_response_format", "generic")
        self.ams_details = print_config.get("ams_format_details", {})
        
        # External spool key (vir_slot vs vt_tray)
        self.external_spool_key = self.ams_details.get("external_spool_key", "vt_tray")
        self.has_vir_slot = self.ams_details.get("has_vir_slot", False)
        
        # AMS structure keys
        self.ams_array_key = self.ams_details.get("ams_array_key", "ams")
        self.ams_id_key = self.ams_details.get("ams_id_key", "id")
        self.tray_array_key = self.ams_details.get("tray_array_key", "tray")
        self.tray_id_key = self.ams_details.get("tray_id_key", "id")
    
    def parse_slots(self, push_status: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Parse AMS slots from push_status response.
        
        Returns normalized slot list with consistent structure regardless
        of printer model/firmware.
        """
        slots = []
        
        # Get AMS data using the correct key for this firmware
        ams_data = push_status.get(self.ams_array_key, {})
        ams_list = ams_data.get(self.ams_array_key, [])
        
        for ams in ams_list:
            ams_id = ams.get(self.ams_id_key, 0)
            
            # Create empty slots for all 4 positions
            empty_tray = self._empty_tray_dict()
            row = [self._tray_to_slot(ams_id, i, empty_tray) for i in range(4)]
            
            # Fill in actual tray data
            trays = ams.get(self.tray_array_key, [])
            for tray in trays:
                tray_id = tray.get(self.tray_id_key, 0)
                try:
                    tray_id = int(tray_id)
                except (ValueError, TypeError):
                    continue
                if 0 <= tray_id < 4 and not self._is_empty_tray(tray):
                    row[tray_id] = self._tray_to_slot(ams_id, tray_id, tray)
            
            slots.extend(row)
        
        # Handle external spool (virtual tray)
        self._add_external_spool(push_status, slots)
        
        return slots
    
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
    
    def _tray_to_slot(self, ams_id: int, tray_id: int, tray: Dict[str, Any]) -> Dict[str, Any]:
        """Convert tray data to normalized slot format."""
        slot = dict(tray)
        slot["ids"] = {"amsID": ams_id, "slotID": tray_id}
        
        # Determine brand from filament code
        brand = "Generic"
        current_code = tray.get("tray_info_idx", "")
        filaments = self.config.get_filament_index()
        for f in filaments:
            if f.get("filament_id") == current_code:
                brand = f.get("filament_name", "Generic").split()[0]
                break
        
        t = tray.get("tray_type", "EMPTY")
        sub = tray.get("tray_sub_brands", "")
        if sub:
            t += f" ({sub})"
        
        slot["Type"] = t
        slot["Color"] = tray.get("tray_color", "xxxxxx")[:6]
        slot["Brand"] = brand
        slot["Min Temp"] = int(tray.get("nozzle_temp_min", 0) or 0)
        slot["Max Temp"] = int(tray.get("nozzle_temp_max", 0) or 0)
        slot["Bed Temp"] = int(tray.get("bed_temp", 0) or 0)
        slot["k"] = tray.get("k", 0.0)
        
        if ams_id == 255:  # External spool
            slot["displayID"] = "External Spool (no AMS)"
        else:
            slot["displayID"] = f"AMS #{ams_id} | Slot #{tray_id + 1}"
        
        return slot
    
    def _add_external_spool(self, push_status: Dict[str, Any], slots: List[Dict[str, Any]]) -> None:
        """Add external spool slot if present."""
        ext = push_status.get(self.external_spool_key)
        
        # Try fallback keys if not found
        if ext is None:
            for key in ["vir_slot", "vt_tray"]:
                if key in push_status:
                    ext = push_status[key]
                    break
        
        if ext is None:
            return
        
        # Handle both array (vir_slot) and object (vt_tray) formats
        if isinstance(ext, list):
            # vir_slot is an array - use first element
            ext = ext[0] if ext else self._empty_tray_dict()
        else:
            # vt_tray is an object
            ext = ext
        
        if self._is_empty_tray(ext):
            ext = self._empty_tray_dict()
        
        slots.append(self._tray_to_slot(255, 255, ext))


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