"""Client identity extraction.

Identity is derived ONLY from the peer certificate of an already
established TLS session (post-handshake, chain verified by OpenSSL with
CERT_REQUIRED). Nothing the client says at the application layer can
influence the identity the server acts on.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.x509.oid import ExtensionOID, NameOID

from .certs import cert_validity_utc

CLIENT_AUTH_OID = "1.3.6.1.5.5.7.3.2"


@dataclass(frozen=True)
class Identity:
    common_name: str
    organization: str
    san_dns: tuple[str, ...]
    issuer_common_name: str
    serial_hex: str
    sha256_fingerprint: str
    not_before: str
    not_after: str
    extended_key_usages: tuple[str, ...]

    def to_dict(self) -> dict:
        data = asdict(self)
        data["san_dns"] = list(self.san_dns)
        data["extended_key_usages"] = list(self.extended_key_usages)
        return data


def identity_from_verified_der(der_bytes: bytes) -> Identity:
    """Build an Identity from a verified peer certificate (DER).

    Caller contract: ``der_bytes`` MUST come from
    ``SSLSocket.getpeercert(binary_form=True)`` on a session whose
    handshake completed with ``verify_mode=CERT_REQUIRED``. Never call
    this on unverified, client-supplied bytes for authentication
    decisions.
    """
    cert = x509.load_der_x509_certificate(der_bytes)

    def _attr(name: x509.Name, oid) -> str:
        attrs = name.get_attributes_for_oid(oid)
        return attrs[0].value if attrs else ""

    try:
        san = cert.extensions.get_extension_for_oid(
            ExtensionOID.SUBJECT_ALTERNATIVE_NAME
        ).value
        san_dns = tuple(san.get_values_for_type(x509.DNSName))
    except x509.ExtensionNotFound:
        san_dns = ()

    try:
        eku = cert.extensions.get_extension_for_oid(
            ExtensionOID.EXTENDED_KEY_USAGE
        ).value
        ekus = tuple(oid.dotted_string for oid in eku)
    except x509.ExtensionNotFound:
        ekus = ()

    fp = cert.fingerprint(hashes.SHA256()).hex()
    not_before, not_after = cert_validity_utc(cert)
    return Identity(
        common_name=_attr(cert.subject, NameOID.COMMON_NAME),
        organization=_attr(cert.subject, NameOID.ORGANIZATION_NAME),
        san_dns=san_dns,
        issuer_common_name=_attr(cert.issuer, NameOID.COMMON_NAME),
        serial_hex=format(cert.serial_number, "x"),
        sha256_fingerprint=":".join(fp[i:i + 2] for i in range(0, len(fp), 2)),
        not_before=not_before.isoformat(),
        not_after=not_after.isoformat(),
        extended_key_usages=ekus,
    )
