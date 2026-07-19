"""
MQTT message signing for Bambu Lab printers using X.509 certificates.

Based on the Bambu Connect certificate and private key publicly disclosed January 2025.
Protocol reference: https://github.com/Doridian/OpenBambuAPI/blob/main/cloud-x509-auth.md

Usage:
    from bambu_mqtt_generator import PayloadBuilder, load_config
    from bambu_mqtt_generator.sign_mqtt import sign_payload, build_app_cert_install, MQTTSigner
    
    # Build payload using PayloadBuilder
    config = load_config()
    builder = config.get_payload_builder("X1 Carbon", "01.05.06.06")
    payload = builder.build_ams_filament_setting(ams_id=255, tray_id=254, ...)
    
    # Sign the payload for printers requiring message signing
    # User must provide their own cert/key (e.g., from Bambu Connect)
    signed_payload = sign_payload(payload, private_key_pem=PRIVATE_KEY_PEM, cert_id=CERT_ID)
    
    # For printer bootstrap (first connection), send app_cert_install first:
    app_cert_msg = build_app_cert_install(
        sequence_id="12345",
        cert_chain_pem=CERT_CHAIN_PEM,
        crl_pem=CRL_PEM,
    )
    
    # Or use the convenience class:
    signer = MQTTSigner(
        cert_pem=CERT_PEM,
        key_pem=PRIVATE_KEY_PEM,
        cert_chain_pem=CERT_CHAIN_PEM,
        crl_pem=CRL_PEM,
    )
    signed = signer.sign(payload)
    app_cert = signer.build_app_cert_install("12345")
"""

import json
import re
from base64 import b64encode
from typing import Optional

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import load_pem_private_key


def compute_cert_id(cert_pem: str) -> str:
    """
    Compute the cert_id string for a given certificate PEM.

    Format (confirmed from OpenBambuAPI captures):
        cert_id = "{cert_serial_hex}CN={issuer_CN}"

    where cert_serial_hex is the 32-char lowercase hex of the cert's
    tbsCertificate.serialNumber, and issuer_CN is the Issuer's Common Name.

    Example (cert serial a4e8faaa…, issuer GLOF3813734089.bambulab.com):
        "a4e8faaa1a38e3650a0ea590d192383fCN=GLOF3813734089.bambulab.com"
    """
    cert = x509.load_pem_x509_certificate(cert_pem.encode())
    serial_hex = format(cert.serial_number, "032x")
    issuer_cn = cert.issuer.get_attributes_for_oid(
        x509.oid.NameOID.COMMON_NAME
    )[0].value
    return f"{serial_hex}CN={issuer_cn}"


def sign_payload(
    payload: dict,
    private_key_pem: str,
    cert_id: str,
) -> str:
    """
    Sign an MQTT command payload as required by post-January 2025 firmware
    and return the EXACT wire bytes (as a str) that BambuStudio publishes.

    Canonical bytes-to-sign (per OpenBambuAPI cloud-x509-auth.md):
        bytes_to_sign = b'{"print":' + json.dumps(payload[top_key],
                            sort_keys=True, separators=(",", ":")) + b'}'

    Only the top-level command object (e.g., "print") is signed — no
    user_id or other envelope fields are included in the signed content.

    The published wire message is compact JSON, header-first, with the
    "print" object byte-identical to the canonical signed bytes:
        {"header":<compact sorted header>,"print":<canonical inner>}
    BambuStudio does NOT include a top-level user_id field.
    """
    # Determine the top-level command key from payload (e.g., "pushing", "info", "print", "security")
    top_key = next(k for k in payload if k != "header")
    canonical_inner = json.dumps(
        payload[top_key], sort_keys=True, separators=(",", ":")
    )
    canonical_inner_bytes = canonical_inner.encode("utf-8")
    # Always sign with "print" as the key (Bambu protocol expects "print" on wire)
    payload_bytes = b'{"print":' + canonical_inner_bytes + b"}"

    key = load_pem_private_key(private_key_pem.encode(), password=None)
    signature = key.sign(payload_bytes, padding.PKCS1v15(), hashes.SHA256())
    sign_string = b64encode(signature).decode()

    header = {
        "sign_ver": "v1.0",
        "sign_alg": "RSA_SHA256",
        "sign_string": sign_string,
        "cert_id": cert_id,
        "payload_len": len(payload_bytes),
    }
    header_json = json.dumps(header, sort_keys=True, separators=(",", ":"))

    # Wire format always uses "print" as the top-level key
    return (
        '{"header":' + header_json
        + ',"print":' + canonical_inner + "}"
    )


def build_app_cert_install(
    sequence_id: str,
    cert_chain_pem: str,
    crl_pem: str,
) -> str:
    """
    Build the unsigned `app_cert_install` bootstrap message that BambuStudio
    publishes to device/<serial>/request BEFORE any signed command 
    (or just connect to the printer using the app that the key came from, once per boot)
    It registers the full 3-cert chain + CRL. Compact JSON, sorted keys.
    """
    msg = {
        "security": {
            "app_cert": cert_chain_pem,
            "command": "app_cert_install",
            "crl": crl_pem,
            "sequence_id": str(sequence_id),
        }
    }
    return json.dumps(msg, sort_keys=True, separators=(",", ":"))


def sign(
    payload_json: str,
    private_key_pem: str,
    cert_id: str,
) -> str:
    """
    Sign an arbitrary JSON payload string and return the signed wire format.
    
    This is the main entry point for signing any JSON string (not just payloads
    generated by this library). The input should be a JSON object containing
    the command (e.g., {"print": {...}} or {"security": {...}}).
    
    Args:
        payload_json: JSON string containing the payload to sign (e.g., '{"print": {...}}')
        private_key_pem: PEM-encoded RSA private key
        cert_id: Certificate ID string
    
    Returns:
        Signed MQTT wire message as a JSON string with header and signed payload.
    """
    payload = json.loads(payload_json)
    return sign_payload(payload, private_key_pem, cert_id)


# ─── File loading helpers ────────────────────────────────────────────────────

def load_pem(path: str) -> str:
    """Load a PEM file (cert, key, chain, or CRL) from file."""
    with open(path, "r") as f:
        return f.read()


def extract_leaf_cert(chain_pem: str) -> str:
    """
    Extract the leaf certificate (first PEM block) from a certificate chain.
    """
    match = re.search(
        r"(-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----)",
        chain_pem,
        re.DOTALL,
    )
    if not match:
        raise ValueError("No certificate found in chain PEM")
    return match.group(1)


class MQTTSigner:
    """
    Convenience class for signing MQTT payloads with custom credentials.
    
    Usage:
        signer = MQTTSigner(
            cert_pem_path="/path/to/cert.pem",
            key_pem_path="/path/to/key.pem",
            cert_chain_pem_path="/path/to/chain.pem",
            crl_pem_path="/path/to/crl.pem",
        )
        signed = signer.sign(payload)
        app_cert = signer.build_app_cert_install("12345")
    """
    
    def __init__(
        self,
        cert_pem_path: Optional[str] = None,
        key_pem_path: Optional[str] = None,
        cert_chain_pem_path: Optional[str] = None,
        crl_pem_path: Optional[str] = None,
        cert_pem: Optional[str] = None,
        key_pem: Optional[str] = None,
        cert_chain_pem: Optional[str] = None,
        crl_pem: Optional[str] = None,
    ):
        """
        Initialize signer with credentials.
        
        Required: cert_pem (or cert_pem_path) and key_pem (or key_pem_path) for signing.
        Optional: cert_chain_pem and crl_pem (or their _path variants) for bootstrap messages.
        """
        # Load leaf cert and private key (required for signing)
        if cert_pem_path or key_pem_path:
            if not (cert_pem_path and key_pem_path):
                raise ValueError("Both cert_pem_path and key_pem_path must be provided")
            self.cert_pem = load_pem(cert_pem_path)
            self.key_pem = load_pem(key_pem_path)
        elif cert_pem or key_pem:
            if not (cert_pem and key_pem):
                raise ValueError("Both cert_pem and key_pem must be provided")
            self.cert_pem = cert_pem
            self.key_pem = key_pem
        else:
            raise ValueError("Must provide cert_pem and key_pem (or their _path variants)")
        
        # Load cert chain and CRL (optional, needed for bootstrap)
        self.cert_chain_pem = None
        self.crl_pem = None
        
        if cert_chain_pem_path or crl_pem_path:
            if cert_chain_pem_path:
                self.cert_chain_pem = load_pem(cert_chain_pem_path)
            if crl_pem_path:
                self.crl_pem = load_pem(crl_pem_path)
        elif cert_chain_pem or crl_pem:
            self.cert_chain_pem = cert_chain_pem
            self.crl_pem = crl_pem
        
        self.cert_id = compute_cert_id(self.cert_pem)
    
    def sign(self, payload: dict) -> str:
        """Sign a payload dict using this signer's credentials."""
        return sign_payload(payload, self.key_pem, self.cert_id)
    
    def sign_json(self, payload_json: str) -> str:
        """Sign an arbitrary JSON string using this signer's credentials."""
        return sign(payload_json, self.key_pem, self.cert_id)
    
    def build_app_cert_install(self, sequence_id: str) -> str:
        """Build app_cert_install bootstrap message using this signer's chain and CRL.
        
        Requires cert_chain_pem and crl_pem to have been provided during initialization.
        """
        if not self.cert_chain_pem or not self.crl_pem:
            raise ValueError("cert_chain_pem and crl_pem required for app_cert_install (not provided during init)")
        return build_app_cert_install(sequence_id, self.cert_chain_pem, self.crl_pem)
    
    def get_cert_id(self) -> str:
        """Get the cert_id for this signer's certificate."""
        return self.cert_id