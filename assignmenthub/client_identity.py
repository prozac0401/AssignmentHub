"""Authenticate client IPs across the private Caddy -> Streamlit -> API hops.

The launcher shares a fresh secret through child environments only. Caddy
overwrites the gateway headers using its socket peer; Streamlit verifies those
headers and signs a short-lived, credential-bound login proof. Ordinary forwarded
headers are never an authority, including on direct loopback API connections.
"""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
import hmac
import ipaddress
import json
import time


SECRET_ENV = "AH_INTERNAL_CLIENT_SECRET"
CLIENT_IP_HEADER = "X-AH-Client-IP"
GATEWAY_KEY_HEADER = "X-AH-Gateway-Key"
UI_TIME_HEADER = "X-AH-UI-Time"
UI_SIGNATURE_HEADER = "X-AH-UI-Signature"
PROOF_TTL_SECONDS = 60


def _ip(value: str) -> str | None:
    try:
        address = ipaddress.ip_address(value)
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        return str(address)
    except ValueError:
        return None


def gateway_client_ip(headers: Mapping[str, str], secret: str) -> str | None:
    """Only Caddy's overwritten, authenticated header is accepted by the UI."""
    supplied = headers.get(GATEWAY_KEY_HEADER, "")
    if not secret or not hmac.compare_digest(supplied.encode(), secret.encode()):
        return None
    return _ip(headers.get(CLIENT_IP_HEADER, ""))


def _signature(secret: str, address: str, stamp: str, user_id: str, password: str) -> str:
    message = json.dumps(["ah-login-v1", address, stamp, user_id, password],
                         ensure_ascii=True, separators=(",", ":")).encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def ui_login_headers(headers: Mapping[str, str], secret: str, user_id: str,
                     password: str, *, now: float | None = None) -> dict[str, str]:
    address = gateway_client_ip(headers, secret)
    if address is None:
        return {}
    stamp = str(int(time.time() if now is None else now))
    return {CLIENT_IP_HEADER: address, UI_TIME_HEADER: stamp,
            UI_SIGNATURE_HEADER: _signature(secret, address, stamp, user_id, password)}


def login_client_ip(headers: Mapping[str, str], peer: str, secret: str, user_id: str,
                    password: str, *, now: float | None = None) -> str:
    """Resolve a trusted hop, otherwise retain the real transport peer."""
    transport_ip = _ip(peer)
    if not transport_ip or not ipaddress.ip_address(transport_ip).is_loopback or not secret:
        return transport_ip or peer
    gateway_ip = gateway_client_ip(headers, secret)
    if gateway_ip is not None:
        return gateway_ip
    address = _ip(headers.get(CLIENT_IP_HEADER, ""))
    stamp = headers.get(UI_TIME_HEADER, "")
    if address is None or not stamp.isascii() or not stamp.isdecimal() or len(stamp) > 12:
        return transport_ip
    age = (time.time() if now is None else now) - int(stamp)
    if not -5 <= age <= PROOF_TTL_SECONDS:
        return transport_ip
    supplied = headers.get(UI_SIGNATURE_HEADER, "")
    expected = _signature(secret, address, stamp, user_id, password)
    return address if hmac.compare_digest(supplied.encode(), expected.encode()) else transport_ip
