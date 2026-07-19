"""
Config loader for Bambu AMS payloads.
Loads and caches all configuration files from the config directory.
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, Optional, List


class ConfigLoader:
    """Loads and caches configuration files for payload generation."""
    
    _instance: Optional["ConfigLoader"] = None
    
    def __new__(cls, config_dir: Optional[Path] = None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self, config_dir: Optional[Path] = None):
        if self._initialized:
            return
        
        if config_dir is None:
            config_dir = Path(__file__).parent / "config"
        
        self.config_dir = Path(config_dir)
        self._printers: Dict[str, Dict] = {}
        self._commands: Dict[str, Dict] = {}
        self._enums: Dict[str, Dict] = {}
        self._virtual_ids: Dict[str, Any] = {}
        self._filament_presets: Dict[str, Dict] = {}
        self._feature_flags: Dict[str, Any] = {}
        self._printer_index: List[Dict] = []
        self._command_index: List[Dict] = []
        self._filament_index: List[Dict] = []
        
        self._load_all()
        self._initialized = True
    
    def _load_all(self):
        """Load all configuration files."""
        # Printers
        printers_dir = self.config_dir / "printers"
        for f in printers_dir.glob("*.json"):
            if f.name != "index.json":
                with open(f) as fp:
                    self._printers[f.stem] = json.load(fp)
        
        # Load index
        with open(printers_dir / "index.json") as fp:
            self._printer_index = json.load(fp)
        
        # Commands
        commands_dir = self.config_dir / "commands"
        for f in commands_dir.glob("*.json"):
            if f.name != "index.json":
                with open(f) as fp:
                    self._commands[f.stem] = json.load(fp)
        
        # Enums
        enums_dir = self.config_dir / "enums"
        for f in enums_dir.glob("*.json"):
            if f.name != "index.json":
                with open(f) as fp:
                    self._enums[f.stem] = json.load(fp)
        
        # Virtual IDs
        with open(self.config_dir / "virtual_ids.json") as fp:
            self._virtual_ids = json.load(fp)
        
        # Filament presets
        filament_dir = self.config_dir / "filament_presets"
        for f in filament_dir.glob("*.json"):
            if f.name != "index.json":
                with open(f) as fp:
                    self._filament_presets[f.stem] = json.load(fp)
        
        with open(filament_dir / "index.json") as fp:
            self._filament_index = json.load(fp)
        
        # Feature flags
        with open(self.config_dir / "feature_flags.json") as fp:
            self._feature_flags = json.load(fp)
    
    # --- Accessor methods ---
    
    def get_printer(self, model_id: str) -> Optional[Dict]:
        """Get printer config by model ID (e.g., 'BL-P001', 'C11')."""
        return self._printers.get(model_id)
    
    def get_printer_index(self) -> List[Dict]:
        """Get list of all available printers."""
        return self._printer_index
    
    def get_command(self, command_name: str) -> Optional[Dict]:
        """Get command spec by name."""
        return self._commands.get(command_name)
    
    def get_enum(self, enum_name: str) -> Optional[Dict]:
        """Get enum values by name."""
        return self._enums.get(enum_name)
    
    def get_virtual_ids(self) -> Dict[str, Any]:
        """Get virtual ID constants."""
        return self._virtual_ids
    
    def get_filament_preset(self, filament_id: str) -> Optional[Dict]:
        """Get filament preset by ID (e.g., 'GFU01')."""
        return self._filament_presets.get(filament_id)
    
    def get_filament_index(self) -> List[Dict]:
        """Get list of all filament presets."""
        return self._filament_index
    
    def get_feature_flags(self) -> Dict[str, Any]:
        """Get feature flags matrix."""
        return self._feature_flags
    
    def supports_feature(self, model_id: str, firmware_version: str, feature: str) -> bool:
        """Check if a printer model at a firmware version supports a feature."""
        flags = self._feature_flags.get("printer_support", {})
        model_flags = flags.get(model_id, {})
        version_flags = model_flags.get(firmware_version, {})
        return version_flags.get(feature, False)
    
    def get_firmware_versions(self, model_id: str) -> List[str]:
        """Get sorted list of firmware versions for a printer model."""
        printer = self.get_printer(model_id)
        if not printer:
            return []
        return sorted(printer.get("firmware_versions", {}).keys(), 
                      key=lambda v: tuple(map(int, v.split("."))))

    def get_closest_firmware_version(self, model_id: str, target_version: str) -> Optional[str]:
        """
        Find the closest matching firmware version for a printer model.
        
        Returns the highest available version that is <= target_version.
        If target_version is older than all available versions, returns the oldest.
        If target_version exactly matches an available version, returns it.
        
        Args:
            model_id: Printer model ID (e.g., "N1", "N7")
            target_version: Target firmware version string (e.g., "01.05.00.00")
            
        Returns:
            Closest matching firmware version or None if no versions available
        """
        versions = self.get_firmware_versions(model_id)
        if not versions:
            return None
            
        def version_tuple(v: str) -> tuple:
            return tuple(map(int, v.split(".")))
        
        target_tuple = version_tuple(target_version)
        
        # Find versions <= target
        candidates = [v for v in versions if version_tuple(v) <= target_tuple]
        
        if candidates:
            # Return highest candidate (closest but not exceeding target)
            return max(candidates, key=version_tuple)
        else:
            # Target is older than all available - return oldest
            return min(versions, key=version_tuple)

    def _normalize_printer_name(self, name: str) -> str:
        """Normalize printer name for matching: lowercase, collapse whitespace."""
        return re.sub(r'\s+', ' ', name.strip().lower())
    
    def _build_name_to_model_id_map(self) -> Dict[str, str]:
        """Build mapping from normalized printer names to model IDs."""
        mapping = {}
        for model_id, printer in self._printers.items():
            display_name = printer.get("display_name", "")
            if display_name:
                # Remove "Bambu Lab " prefix if present
                name = display_name
                if name.lower().startswith("bambu lab "):
                    name = name[10:]  # Remove "Bambu Lab "
                normalized = self._normalize_printer_name(name)
                mapping[normalized] = model_id
                
                # Also add just the model part (e.g., "X1 Carbon" -> "X1 Carbon")
                # and common aliases
        return mapping
    
    def _resolve_model_id(self, identifier: str) -> Optional[str]:
        """Resolve a model ID or printer name to a model ID.
        
        Args:
            identifier: Either a model ID (e.g., "BL-P001") or printer name 
                       (e.g., "X1 Carbon", "P1P", "A1 mini")
                       
        Returns:
            Model ID if found, None otherwise
        """
        # First check if it's already a model ID
        if identifier in self._printers:
            return identifier
        
        # Try normalized name matching
        normalized = self._normalize_printer_name(identifier)
        
        # Build name map if not cached
        if not hasattr(self, '_name_to_model_id_map'):
            self._name_to_model_id_map = self._build_name_to_model_id_map()
        
        return self._name_to_model_id_map.get(normalized)
    
    def get_payload_builder(self, model_id: str, firmware_version: str):
        """Get a PayloadBuilder for a specific printer and firmware version.
        
        Args:
            model_id: Either a model ID (e.g., "BL-P001") or printer name 
                      (e.g., "X1 Carbon", "P1P", "A1")
            firmware_version: Firmware version string (e.g., "01.05.06.06")
                              If exact version not found, closest earlier version is used.
        """
        resolved_model_id = self._resolve_model_id(model_id)
        if not resolved_model_id:
            raise ValueError(f"Unknown printer model: {model_id}")
        
        # Find closest matching firmware version
        closest_version = self.get_closest_firmware_version(resolved_model_id, firmware_version)
        if not closest_version:
            raise ValueError(f"No firmware versions available for model: {resolved_model_id}")
        if closest_version != firmware_version:
            print(f"[bambu-mqtt-generator] Firmware version '{firmware_version}' not found for {resolved_model_id}, using closest: '{closest_version}'")
        
        from .payload_builder import PayloadBuilder
        return PayloadBuilder(self, resolved_model_id, closest_version)


# Convenience function
def load_config(config_dir: Optional[Path] = None) -> ConfigLoader:
    """Get the singleton ConfigLoader instance."""
    return ConfigLoader(config_dir)


def get_payload_builder(model_id: str, firmware_version: str, config_dir: Optional[Path] = None):
    """Get a PayloadBuilder for a specific printer and firmware version."""
    config = ConfigLoader(config_dir)
    return config.get_payload_builder(model_id, firmware_version)