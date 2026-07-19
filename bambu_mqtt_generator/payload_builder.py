"""
Payload builder for Bambu Studio MQTT commands.
Generates properly formatted JSON payloads for various printer commands.
"""

from typing import Any, Dict, List, Optional, Union
from dataclasses import dataclass, field
from pathlib import Path
import random

from .config_loader import ConfigLoader, load_config
from .sign_mqtt import MQTTSigner, sign_payload


# Virtual tray IDs for external spools
VIRTUAL_TRAY_MAIN_ID = 255
VIRTUAL_TRAY_DEPUTY_ID = 254
VIRTUAL_AMS_MAIN_ID = 255
VIRTUAL_AMS_DEPUTY_ID = 254

# Predefined AMS IDs for external spools
class ExternalSpool:
    """Constants for external spool (virtual tray) AMS IDs."""
    MAIN = VIRTUAL_AMS_MAIN_ID       # 255 - Main external spool
    DEPUTY = VIRTUAL_AMS_DEPUTY_ID   # 254 - Deputy external spool


@dataclass
class PayloadBuilder:
    """Builds MQTT command payloads using config validation."""
    
    config: ConfigLoader = field(default_factory=load_config)
    model_id: str = ""
    firmware_version: str = ""
    _sequence_counter: int = 0
    signer: Optional[MQTTSigner] = field(default=None)
    
    def __post_init__(self):
        if isinstance(self.config, str):
            # Allow passing config dir as string
            from .config_loader import load_config
            self.config = load_config(Path(self.config))
        elif self.config is None:
            self.config = load_config()
    
    @staticmethod
    def _normalize_tray_color(color: str) -> str:
        """Normalize tray_color to 8-char RGBA hex.
        
        Accepts 6-char (RRGGBB) or 8-char (RRGGBBAA) hex strings.
        If 6 chars provided, defaults alpha to FF (fully opaque).
        """
        if not color:
            return ""
        # Remove # prefix if present
        color = color.lstrip("#")
        if len(color) == 6:
            return color + "FF"
        elif len(color) == 8:
            return color
        else:
            raise ValueError(f"tray_color must be 6 or 8 hex characters, got: {color}")
    
    def _next_sequence_id(self) -> str:
        """Generate next sequence ID as a random 8-digit number string."""
        return str(random.randint(10_000_000, 99_999_999))
    
    def _resolve_enum_ref(self, field_name: str, value: Any) -> Any:
        """Resolve enum references if needed."""
        # For now, just return the value as-is
        # In the future, could validate against enum values
        return value
    
    def _validate_required(self, command_spec: Dict, section: str, provided: Dict) -> List[str]:
        """Validate required fields are present."""
        missing = []
        for field_name in command_spec.get("required_fields", []):
            field_def = command_spec["field_definitions"].get(field_name, {})
            if field_def.get("section") == section and field_name not in provided:
                # Skip if field has default: null (treated as optional)
                if field_def.get("default") is None:
                    continue
                missing.append(field_name)
        return missing
    
    def build_payload(self, command_name: str, **kwargs) -> Dict[str, Any]:
        """
        Build a complete MQTT payload for a command.
        
        Args:
            command_name: The command name (e.g., 'ams_filament_settings', 'get_version')
            **kwargs: Field values for the command
            
        Returns:
            Complete JSON payload dict
        """
        command_spec = self.config.get_command(command_name)
        if not command_spec:
            raise ValueError(f"Unknown command: {command_name}")
        
        # Build payload structure
        payload = {}
        sections = command_spec.get("sections", {})
        
        # First pass: Add all provided fields
        for section_name, section_fields in sections.items():
            payload[section_name] = {}
            
            for field_name, value in kwargs.items():
                field_def = command_spec["field_definitions"].get(field_name, {})
                if field_def.get("section") == section_name:
                    # Handle special fields
                    if field_name == "sequence_id" and value is None:
                        value = self._next_sequence_id()
                    payload[section_name][field_name] = self._resolve_enum_ref(field_name, value)
        
        # Second pass: Add default values for fields not provided
        for section_name, section_fields in sections.items():
            for field_name, field_def in section_fields.items():
                if field_name not in payload.get(section_name, {}):
                    default = field_def.get("default")
                    if default is not None:
                        payload[section_name][field_name] = default
        
        # Remove None values from payload (fields with default: null should be omitted)
        for section_name in payload:
            payload[section_name] = {k: v for k, v in payload[section_name].items() if v is not None}
        
        # Third pass: Validate required fields (after defaults added)
        for section_name, section_fields in sections.items():
            missing = self._validate_required(command_spec, section_name, payload.get(section_name, {}))
            if missing:
                raise ValueError(f"Missing required fields for {section_name}: {missing}")
        
        return payload
    
    def build_ams_filament_setting(
        self,
        ams_id: int,
        tray_id: int,
        slot_id: int,
        tray_info_idx: str,
        tray_color: str,
        setting_id: str = "",
        nozzle_temp_min: int = 0,
        nozzle_temp_max: int = 0,
        tray_type: str = "",
        sequence_id: Optional[str] = None,
        cols: Optional[List[str]] = None,
        ctype: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Build ams_filament_settings payload (change filament color/type on AMS or external spool).
        
        For external spool (main): ams_id=255, tray_id=254, slot_id=0
        For external spool (deputy): ams_id=254, tray_id=254, slot_id=0
        For AMS: ams_id=1-4, tray_id=slot_id (0-3)
        
        Args:
            ams_id: AMS ID (255=main external, 254=deputy external, 1-4=physical AMS)
            tray_id: Tray ID (254 for virtual trays, 0-3 for AMS slots)
            slot_id: Slot ID (0 for external, 0-3 for AMS)
            tray_info_idx: Filament ID (e.g., 'GFU01' for Bambu PLA Basic)
            setting_id: Preset setting ID from filament profile (default: "", no user preset)
            tray_color: RGBA hex color (e.g., '00FF00FF' for green)
            nozzle_temp_min: Minimum nozzle temperature (default: 0)
            nozzle_temp_max: Maximum nozzle temperature (default: 0)
            tray_type: Filament type (e.g., 'PLA', 'PETG') (default: "")
            sequence_id: Optional custom sequence ID
            cols: Optional color array for multi-color
            ctype: Optional color type
        """
        # Normalize tray_color: accept 6 or 8 hex chars, default alpha to FF
        tray_color = self._normalize_tray_color(tray_color)
        return self.build_payload(
            "ams_filament_settings",
            sequence_id=sequence_id,
            ams_id=ams_id,
            tray_id=tray_id,
            slot_id=slot_id,
            tray_info_idx=tray_info_idx,
            setting_id=setting_id,
            tray_color=tray_color,
            nozzle_temp_min=nozzle_temp_min,
            nozzle_temp_max=nozzle_temp_max,
            tray_type=tray_type,
            cols=cols,
            ctype=ctype,
        )
    
    def build_external_spool_setting(
        self,
        tray_info_idx: str,
        tray_color: str,
        nozzle_temp_min: int = 0,
        nozzle_temp_max: int = 0,
        tray_type: str = "",
        setting_id: str = "",
        is_main: bool = True,
        sequence_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Convenience method to set external spool (virtual tray) settings.
        
        Args:
            tray_info_idx: Filament ID (e.g., 'GFU01' for Bambu PLA Basic)
            tray_color: RGBA hex color (e.g., '00FF00FF' for green)
            nozzle_temp_min: Minimum nozzle temperature (default: 0)
            nozzle_temp_max: Maximum nozzle temperature (default: 0)
            tray_type: Filament type (e.g., 'PLA', 'PETG') (default: "")
            setting_id: Preset setting ID from filament profile (default: "", no user preset)
            is_main: True for main external spool (255), False for deputy (254)
            sequence_id: Optional custom sequence ID
        """
        virtual = self.config.get_virtual_ids()
        ams_id = virtual["VIRTUAL_TRAY_MAIN_ID"] if is_main else virtual["VIRTUAL_TRAY_DEPUTY_ID"]
        tray_id = virtual["VIRTUAL_TRAY_DEPUTY_ID"]  # Always 254 for virtual trays
        
        return self.build_ams_filament_setting(
            ams_id=ams_id,
            tray_id=tray_id,
            slot_id=0,
            tray_info_idx=tray_info_idx,
            setting_id=setting_id,
            tray_color=tray_color,
            nozzle_temp_min=nozzle_temp_min,
            nozzle_temp_max=nozzle_temp_max,
            tray_type=tray_type,
            sequence_id=sequence_id,
        )
    
    def build_filament_setting(
        self,
        tray_info_idx: str,
        tray_color: str,
        ams_id: int,
        tray_id: Optional[int] = None,
        nozzle_temp_min: Optional[int] = None,
        nozzle_temp_max: Optional[int] = None,
        tray_type: Optional[str] = None,
        setting_id: str = "",
        slot_id: Optional[int] = None,
        sequence_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Unified interface for filament settings on any tray (external spool or physical AMS).
        
        **External spool (virtual tray):**
        - ams_id=ExternalSpool.MAIN (255) or ExternalSpool.DEPUTY (254)
        - tray_id is ignored; auto-set to 254 and slot_id to 0
        
        **Physical AMS:**
        - ams_id=1-4, provide tray_id or slot_id (0-3, the other is derived)
        
        For optional parameters (nozzle_temp_min, nozzle_temp_max, tray_type), if not provided,
        defaults are looked up from the filament presets using tray_info_idx.
        
        Args:
            tray_info_idx: Filament ID (e.g., 'GFU01')
            tray_color: RGBA hex color (6 or 8 chars, # prefix OK)
            ams_id: AMS ID (ExternalSpool.MAIN=255, ExternalSpool.DEPUTY=254, 1-4=physical AMS) - REQUIRED
            tray_id: Tray ID (0-3 for physical AMS; ignored for virtual/external) - REQUIRED
            nozzle_temp_min: Minimum nozzle temperature (default: from filament preset)
            nozzle_temp_max: Maximum nozzle temperature (default: from filament preset)
            tray_type: Filament type (e.g., 'PLA', 'PETG') (default: from filament preset)
            setting_id: Preset setting ID (default: "", no user preset)
            slot_id: Slot ID (0-3 for physical AMS; ignored for virtual)
            sequence_id: Optional custom sequence ID
        """
        virtual = self.config.get_virtual_ids()
        
        # Look up filament defaults for optional parameters
        filament_preset = self.config.get_filament_preset(tray_info_idx)
        if not filament_preset:
            raise ValueError(f"Unknown filament code: '{tray_info_idx}'. Valid codes: {[p.get('filament_id') for p in self.config.get_filament_index()]}")
        
        if nozzle_temp_min is None:
            nozzle_temp_min = filament_preset.get("nozzle_temp_min", 0)
        if nozzle_temp_max is None:
            nozzle_temp_max = filament_preset.get("nozzle_temp_max", 0)
        if tray_type is None:
            tray_type = filament_preset.get("filament_type", "")
        
# Determine if external spool (virtual tray)
        is_virtual = False
        
        if ams_id in (VIRTUAL_AMS_MAIN_ID, VIRTUAL_AMS_DEPUTY_ID):
            is_virtual = True
        
        if is_virtual:
            # External spool: use virtual tray IDs, ignore provided tray_id
            tray_id = VIRTUAL_TRAY_DEPUTY_ID  # Always 254
            slot_id = 0
        else:
            # Physical AMS: tray_id == slot_id (0-3)
            # Allow providing either tray_id or slot_id; derive the other
            if slot_id is not None and tray_id is None:
                tray_id = slot_id
            if slot_id is None and tray_id is not None:
                slot_id = tray_id
            
            # Now validate both are provided
            if tray_id is None or slot_id is None:
                raise ValueError("For physical AMS, must provide tray_id or slot_id (the other is derived)")
            if not (1 <= ams_id <= 4):
                raise ValueError(f"Physical AMS ams_id must be 1-4, got {ams_id}")
            if not (0 <= tray_id <= 3):
                raise ValueError(f"Physical AMS tray_id must be 0-3, got {tray_id}")
            if not (0 <= slot_id <= 3):
                raise ValueError(f"Physical AMS slot_id must be 0-3, got {slot_id}")
            if tray_id != slot_id:
                raise ValueError(f"Physical AMS tray_id ({tray_id}) must equal slot_id ({slot_id})")
        
        return self.build_ams_filament_setting(
            ams_id=ams_id,
            tray_id=tray_id,
            slot_id=slot_id,
            tray_info_idx=tray_info_idx,
            setting_id=setting_id,
            tray_color=tray_color,
            nozzle_temp_min=nozzle_temp_min,
            nozzle_temp_max=nozzle_temp_max,
            tray_type=tray_type,
            sequence_id=sequence_id,
            cols=None,
            ctype=None,
        )
    
    def build_get_version(self, sequence_id: Optional[str] = None) -> Dict[str, Any]:
        """Build get_version payload."""
        return self.build_payload("get_version", sequence_id=sequence_id)
    
    def build_get_access_code(self, sequence_id: Optional[str] = None) -> Dict[str, Any]:
        """Build get_access_code payload."""
        return self.build_payload("get_access_code", sequence_id=sequence_id)
    
    def build_pushall(self, sequence_id: Optional[str] = None) -> Dict[str, Any]:
        """Build pushall (request full status) payload."""
        return self.build_payload("request_push_all", sequence_id=sequence_id)
    
    def build_ams_change_filament(
        self,
        ams_id: int,
        slot_id: int,
        load: bool,
        curr_temp: int = 0,
        tar_temp: int = 0,
        target: Optional[int] = None,
        extruder_id: Optional[int] = None,
        sequence_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Build ams_change_filament payload (load/unload filament).
        
        For unload: load=False, target=255, slot_id=255
        For load: load=True, target=ams_id (or tray_id), slot_id=actual_slot
        """
        if target is None:
            target = 255 if not load else ams_id
        
        return self.build_payload(
            "ams_change_filament",
            sequence_id=sequence_id,
            ams_id=ams_id,
            slot_id=slot_id,
            target=target,
            curr_temp=curr_temp,
            tar_temp=tar_temp,
            extruder_id=extruder_id,
        )
    
    def build_ams_control(
        self,
        action: str,
        sequence_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build ams_control payload (resume/pause/abort/reset/done)."""
        valid_actions = ["resume", "pause", "abort", "reset", "done"]
        if action not in valid_actions:
            raise ValueError(f"Invalid action: {action}. Valid: {valid_actions}")
        return self.build_payload("ams_control", sequence_id=sequence_id, param=action)
    
    def build_set_nozzle(
        self,
        nozzle_type: str,
        nozzle_diameter: float,
        sequence_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build set_nozzle payload."""
        return self.build_payload(
            "set_nozzle",
            sequence_id=sequence_id,
            nozzle_type=nozzle_type,
            nozzle_diameter=nozzle_diameter,
        )
    
    def build_gcode(self, gcode: str, sequence_id: Optional[str] = None) -> Dict[str, Any]:
        """Build raw G-code payload."""
        return self.build_payload("gcode", sequence_id=sequence_id, gcode=gcode)
    
    def get_mqtt_topic(self, device_id: str) -> str:
        """Get the MQTT topic for sending commands to a printer."""
        return f"device/{device_id}/request"
    
    def get_filament_defaults(self, filament_id: str) -> Optional[Dict[str, Any]]:
        """Get default settings for a known filament preset."""
        return self.config.get_filament_preset(filament_id)


# Convenience function
def build_payload(command_name: str, **kwargs) -> Dict[str, Any]:
    """Quick payload builder using default config."""
    builder = PayloadBuilder()
    return builder.build_payload(command_name, **kwargs)