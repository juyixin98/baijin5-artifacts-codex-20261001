"""Synthetic certificate fixtures built on the `cryptography` package.

Everything here is local test material: no production CAs, no real
identities, no network access. Certificates are RSA-2048 / SHA-256 so the
independent verifier (verify_ref) can re-check signatures with
PyCryptodome's own RSA implementation.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

UTC = dt.timezone.utc


@dataclass
class IssuedCert:
    cert: x509.Certificate
    key: rsa.RSAPrivateKey

    @property
    def cert_pem(self) -> str:
        return self.cert.public_bytes(serialization.Encoding.PEM).decode("ascii")

    @property
    def key_pem(self) -> str:
        return self.key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode("ascii")

    @property
    def cert_der(self) -> bytes:
        return self.cert.public_bytes(serialization.Encoding.DER)


def _new_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def fingerprint_sha256(cert: x509.Certificate) -> str:
    fp = cert.fingerprint(hashes.SHA256()).hex()
    return ":".join(fp[i:i + 2] for i in range(0, len(fp), 2))


def _name(common_name: str) -> x509.Name:
    return x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "trustlab-fixtures"),
    ])


def make_ca(common_name: str, *, days: int = 365,
            not_before: dt.datetime | None = None) -> IssuedCert:
    key = _new_key()
    not_before = not_before or (dt.datetime.now(UTC) - dt.timedelta(minutes=1))
    cert = (
        x509.CertificateBuilder()
        .subject_name(_name(common_name))
        .issuer_name(_name(common_name))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_before + dt.timedelta(days=days))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False, content_commitment=False,
                key_encipherment=False, data_encipherment=False,
                key_agreement=False, key_cert_sign=True, crl_sign=True,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    return IssuedCert(cert, key)


EKU_PROFILES = {
    "client": [ExtendedKeyUsageOID.CLIENT_AUTH],
    "server": [ExtendedKeyUsageOID.SERVER_AUTH],
    "both": [ExtendedKeyUsageOID.CLIENT_AUTH, ExtendedKeyUsageOID.SERVER_AUTH],
    "none": [],  # no EKU extension at all
}


def make_leaf(issuer: IssuedCert, common_name: str, *, eku: str = "client",
              days: int = 30, not_before: dt.datetime | None = None,
              not_after: dt.datetime | None = None,
              san_dns: tuple[str, ...] = ()) -> IssuedCert:
    if eku not in EKU_PROFILES:
        raise ValueError(f"unknown eku profile {eku!r}")
    key = _new_key()
    not_before = not_before or (dt.datetime.now(UTC) - dt.timedelta(minutes=1))
    not_after = not_after or (not_before + dt.timedelta(days=days))
    builder = (
        x509.CertificateBuilder()
        .subject_name(_name(common_name))
        .issuer_name(issuer.cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, content_commitment=False,
                key_encipherment=True, data_encipherment=False,
                key_agreement=False, key_cert_sign=False, crl_sign=False,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(
                issuer.key.public_key()
            ),
            critical=False,
        )
    )
    oids = EKU_PROFILES[eku]
    if oids:
        builder = builder.add_extension(x509.ExtendedKeyUsage(oids), critical=True)
    if san_dns:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName(n) for n in san_dns]),
            critical=False,
        )
    cert = builder.sign(issuer.key, hashes.SHA256())
    return IssuedCert(cert, key)


def make_expired_leaf(issuer: IssuedCert, common_name: str, *,
                      eku: str = "client") -> IssuedCert:
    now = dt.datetime.now(UTC)
    return make_leaf(
        issuer, common_name, eku=eku,
        not_before=now - dt.timedelta(days=10),
        not_after=now - dt.timedelta(days=1),
    )


def load_pem_certificate(pem: str) -> x509.Certificate:
    return x509.load_pem_x509_certificate(pem.encode("utf-8"))


def cert_validity_utc(cert: x509.Certificate) -> tuple[dt.datetime, dt.datetime]:
    """(not_before, not_after) as aware UTC datetimes.

    cryptography>=42 exposes not_valid_before_utc / not_valid_after_utc;
    on 41.x fall back to localizing the naive values (which are UTC).
    """
    try:
        return cert.not_valid_before_utc, cert.not_valid_after_utc
    except AttributeError:
        return (cert.not_valid_before.replace(tzinfo=UTC),
                cert.not_valid_after.replace(tzinfo=UTC))


def cert_summary(cert: x509.Certificate) -> dict:
    def _cn(name: x509.Name) -> str:
        attrs = name.get_attributes_for_oid(NameOID.COMMON_NAME)
        return attrs[0].value if attrs else ""

    not_before, not_after = cert_validity_utc(cert)
    return {
        "common_name": _cn(cert.subject),
        "issuer_common_name": _cn(cert.issuer),
        "serial_hex": format(cert.serial_number, "x"),
        "sha256_fingerprint": fingerprint_sha256(cert),
        "not_before": not_before.isoformat(),
        "not_after": not_after.isoformat(),
    }
