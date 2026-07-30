"""
bambu-mqtt-generator - Python library for generating Bambu Studio MQTT command payloads.

This library extracts configuration from Bambu Studio source code and provides
a clean interface for generating valid MQTT payloads for printer commands.
"""

from .config_loader import (
    AmbiguousCommandNameError,
    AmbiguousPrinterNameError,
    ConfigLoader,
    load_config,
    get_payload_builder,
)
from .payload_builder import (
    PayloadBuilder,
    build_payload,
    get_tray_type,
    ExternalSpool,
    VIRTUAL_TRAY_MAIN_ID,
    VIRTUAL_TRAY_DEPUTY_ID,
    VIRTUAL_AMS_MAIN_ID,
    VIRTUAL_AMS_DEPUTY_ID,
)
from .response_parser import (
    ResponseParser,
    parse_response,
    parse_slots,
    check_command_result,
)
from .sign_mqtt import (
    sign_payload,
    sign,
    build_app_cert_install,
    MQTTSigner,
    compute_cert_id,
    load_pem,
    extract_leaf_cert,
    describe_error,
    ERROR_CODES,
)

__version__ = "1.0.0"

__all__ = [
    "AmbiguousCommandNameError",
    "AmbiguousPrinterNameError",
    "ConfigLoader",
    "load_config",
    "PayloadBuilder",
    "build_payload",
    "get_payload_builder",
    "get_tray_type",
    "ExternalSpool",
    "VIRTUAL_TRAY_MAIN_ID",
    "VIRTUAL_TRAY_DEPUTY_ID",
    "VIRTUAL_AMS_MAIN_ID",
    "VIRTUAL_AMS_DEPUTY_ID",
    "ResponseParser",
    "parse_response",
    "parse_slots",
    "check_command_result",
    "describe_error",
    "ERROR_CODES",
    "sign_payload",
    "sign",
    "build_app_cert_install",
    "MQTTSigner",
    "compute_cert_id",
    "load_pem",
    "extract_leaf_cert",
]