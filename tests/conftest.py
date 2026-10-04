"""Shared fixtures and helpers for the trustlab test suite.

All certificate material is generated locally per session; nothing talks
to any production service or network beyond 127.0.0.1.
"""
from __future__ import annotations

import ssl
import time
from types import SimpleNamespace

import pytest

from trustlab import clientlib
from trustlab.certs import (IssuedCert, make_ca, make_expired_leaf, make_leaf)
from trustlab.service import ServiceConfig, TrustLabService


@pytest.fixture(scope="session")
def fixtures():
    """Local synthetic PKI: one server CA, two client CAs (old/new)."""
    ca_server = make_ca("server-ca")
    ca_old = make_ca("client-ca-old")
    ca_new = make_ca("client-ca-new")
    server = make_leaf(ca_server, "localhost", eku="server",
                       san_dns=("localhost",))
    return SimpleNamespace(
        ca_server=ca_server,
        ca_old=ca_old,
        ca_new=ca_new,
        server=server,
        client_a=make_leaf(ca_old, "client-a", eku="client"),
        client_b=make_leaf(ca_new, "client-b", eku="client"),
        client_expired=make_expired_leaf(ca_old, "client-expired",
                                         eku="client"),
        client_wrong_eku=make_leaf(ca_old, "client-wrong-eku", eku="server"),
    )


def write_pair(tmp_path, issued: IssuedCert, name: str) -> tuple[str, str]:
    cert_path = tmp_path / f"{name}.crt.pem"
    key_path = tmp_path / f"{name}.key.pem"
    cert_path.write_text(issued.cert_pem)
    key_path.write_text(issued.key_pem)
    return str(cert_path), str(key_path)


@pytest.fixture
def service(tmp_path, fixtures):
    """A running TrustLabService trusting only the OLD client CA (v1)."""
    svc = TrustLabService(
        ServiceConfig(data_dir=tmp_path / "svc"),
        server_cert_pem=fixtures.server.cert_pem,
        server_key_pem=fixtures.server.key_pem,
        initial_roots_pem=[fixtures.ca_old.cert_pem],
    )
    svc.start()
    yield svc
    # Dump the audit trail so a failed test can be replayed from the log.
    rows = svc.store.list_audit(run_id=svc.run_id)
    print(f"\n=== audit trail run_id={svc.run_id} ({len(rows)} rows) ===")
    for row in rows:
        print(f"[{row['id']:>3}] {row['ts']} {row['category']:<18} "
              f"{row['event']:<26} {row['reasoning']}")
    svc.stop()


def connect_client(service, fixtures, tmp_path, issued: IssuedCert | None,
                   name: str = "client") -> clientlib.ClientSession:
    cert_path = key_path = None
    if issued is not None:
        cert_path, key_path = write_pair(tmp_path, issued, name)
    return clientlib.connect(
        host=service.host,
        port=service.port,
        cert_path=cert_path,
        key_path=key_path,
        server_ca_pem=fixtures.ca_server.cert_pem,
    )


def wait_failures(service, count: int, timeout: float = 5.0) -> list[dict]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        failures = service.store.list_handshake_failures(service.run_id)
        if len(failures) >= count:
            return failures
        time.sleep(0.05)
    raise AssertionError(
        f"expected {count} handshake failure(s), got {len(failures)}"
    )


def wait_audit(service, *, category: str, event: str,
               timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = service.store.list_audit(run_id=service.run_id,
                                        category=category)
        for row in rows:
            if row["event"] == event:
                return row
        time.sleep(0.05)
    raise AssertionError(f"no audit row category={category} event={event}")


HANDSHAKE_ERRORS = (ssl.SSLError, ConnectionResetError, BrokenPipeError,
                    OSError, clientlib.HandshakeRejected)
