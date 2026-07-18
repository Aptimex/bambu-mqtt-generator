"""
bambu-mqtt-generator - Python library for generating Bambu Studio MQTT command payloads.

This library extracts configuration from Bambu Studio source code and provides
a clean interface for generating valid MQTT payloads for printer commands.
"""

from .config_loader import ConfigLoader, load_config, get_payload_builder
from .payload_builder import (
    PayloadBuilder,
    build_payload,
    ExternalSpool,
    VIRTUAL_TRAY_MAIN_ID,
    VIRTUAL_TRAY_DEPUTY_ID,
    VIRTUAL_AMS_MAIN_ID,
    VIRTUAL_AMS_DEPUTY_ID,
)

__version__ = "1.0.0"

__all__ = [
    "ConfigLoader",
    "load_config",
    "PayloadBuilder",
    "build_payload",
    "get_payload_builder",
    "ExternalSpool",
    "VIRTUAL_TRAY_MAIN_ID",
    "VIRTUAL_TRAY_DEPUTY_ID",
    "VIRTUAL_AMS_MAIN_ID",
    "VIRTUAL_AMS_DEPUTY_ID",
]