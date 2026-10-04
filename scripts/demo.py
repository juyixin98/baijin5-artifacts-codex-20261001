#!/usr/bin/env python3
"""Local end-to-end demo of the trust-bundle rotation test platform.

Runs the real service (mTLS data plane + FastAPI control plane over
uvicorn) on 127.0.0.1 with locally generated fixtures, then walks through:

  1. baseline authentication under the initial trust bundle
  2. rejection of a client from an unknown root (UNTRUSTED_ROOT)
  3. overlapping rotation (old+new roots), then finalization
  4. legacy connection retention after the old root is removed
  5. expired and wrong-purpose client certificates
  6. rollback that emits a NEW version number
  7. hard cutover and the no-common-trust-period explanation
  8. independent verification cross-checks (PyCryptodome / openssl CLI)

Nothing here contacts any production service. Exit code is non-zero if
any step produces an unexpected outcome.
"""
from __future__ import annotations

import shutil
import ssl
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
import uvicorn

from trustlab import clientlib
from trustlab.certs import make_ca, make_expired_leaf, make_leaf
from trustlab.control import create_app
from trustlab.errors import FailureClass
from trustlab.service import ServiceConfig, TrustLabService
from trustlab.verify_ref import (openssl_verify,
                                 pycryptodome_verify_leaf_signature)

CONTROL_PORT = 18600
CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))


def connect_expect_fail(service, fixtures, tmp, issued, name) -> None:
    try:
        clientlib.connect(host=service.host, port=service.port,
                          cert_path=str(tmp / f"{name}.crt.pem"),
                          key_path=str(tmp / f"{name}.key.pem"),
                          server_ca_pem=fixtures["ca_server"].cert_pem)
    except (ssl.SSLError, OSError, clientlib.HandshakeRejected):
        return
    raise AssertionError(f"{name}: connection unexpectedly succeeded")


def write_pair(tmp: Path, issued, name: str) -> None:
    (tmp / f"{name}.crt.pem").write_text(issued.cert_pem)
    (tmp / f"{name}.key.pem").write_text(issued.key_pem)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="trustlab-demo-"))
    print(f"demo workspace: {tmp}")

    # --- local synthetic PKI -------------------------------------------------
    ca_server = make_ca("server-ca")
    ca_old = make_ca("client-ca-old")
    ca_new = make_ca("client-ca-new")
    ca_third = make_ca("client-ca-third")  # never overlaps with ca_old
    server = make_leaf(ca_server, "localhost", eku="server",
                       san_dns=("localhost",))
    client_a = make_leaf(ca_old, "client-a", eku="client")
    client_b = make_leaf(ca_new, "client-b", eku="client")
    client_expired = make_expired_leaf(ca_old, "client-expired")
    client_wrong_eku = make_leaf(ca_old, "client-wrong-eku", eku="server")
    fixtures = {"ca_server": ca_server}
    for name, issued in (("a", client_a), ("b", client_b),
                         ("expired", client_expired),
                         ("wrongeku", client_wrong_eku)):
        write_pair(tmp, issued, name)

    service = TrustLabService(
        ServiceConfig(data_dir=tmp / "svc"),
        server_cert_pem=server.cert_pem,
        server_key_pem=server.key_pem,
        initial_roots_pem=[ca_old.cert_pem],
    )
    service.start()
    print(f"run_id: {service.run_id}")
    print(f"mTLS data plane: {service.host}:{service.port}")

    app = create_app(service)
    server_cfg = uvicorn.Config(app, host="127.0.0.1", port=CONTROL_PORT,
                                log_level="error")
    control = uvicorn.Server(server_cfg)
    thread = threading.Thread(target=control.run, daemon=True)
    thread.start()
    for _ in range(100):
        if control.started:
            break
        time.sleep(0.05)
    api = httpx.Client(base_url=f"http://127.0.0.1:{CONTROL_PORT}", timeout=5)
    print(f"control plane:  http://127.0.0.1:{CONTROL_PORT}\n")

    def connect(issued_name: str) -> clientlib.ClientSession:
        return clientlib.connect(
            host=service.host, port=service.port,
            cert_path=str(tmp / f"{issued_name}.crt.pem"),
            key_path=str(tmp / f"{issued_name}.key.pem"),
            server_ca_pem=ca_server.cert_pem,
        )

    def failures() -> list[dict]:
        return api.get("/failures").json()["failures"]

    try:
        print("1. baseline: client-a authenticates under bundle v1")
        sess_a = connect("a")
        check("client-a accepted under v1", sess_a.bundle_version == 1)
        check("identity from verified cert",
              sess_a.identity["common_name"] == "client-a"
              and sess_a.identity["issuer_common_name"] == "client-ca-old")

        print("2. client-b (unknown root) is rejected")
        connect_expect_fail(service, fixtures, tmp, client_b, "b")
        check("client-b rejected as UNTRUSTED_ROOT",
              failures()[-1]["failure_class"]
              == FailureClass.UNTRUSTED_ROOT.value)

        print("3. overlapping rotation: begin (v2 = old+new), end (v3 = new)")
        r = api.post("/rotation/begin",
                     json={"new_roots_pem": [ca_new.cert_pem]})
        check("rotation begin -> v2 overlap", r.status_code == 201
              and r.json()["bundle"]["kind"] == "rotation_overlap")
        sess_b = connect("b")
        check("client-b accepted during overlap", sess_b.bundle_version == 2)
        r = api.post("/rotation/end",
                     json={"new_roots_pem": [ca_new.cert_pem]})
        check("rotation end -> v3 final", r.json()["bundle"]["version"] == 3)

        print("4. legacy connection retention after old root removal")
        check("client-a's v1 session still answers ping",
              sess_a.ping()["type"] == "pong")
        conns = api.get("/connections").json()["connections"]
        legacy = next(c for c in conns if c["id"] == sess_a.connection_id)
        check("connection marked legacy, not revoked",
              legacy["legacy"] and not legacy["revoked"])
        connect_expect_fail(service, fixtures, tmp, client_a, "a")
        check("NEW client-a connection rejected after root removal",
              failures()[-1]["failure_class"]
              == FailureClass.UNTRUSTED_ROOT.value)
        api.post(f"/connections/{sess_a.connection_id}/revoke")
        check("explicit revocation recorded",
              api.get("/connections").json()["connections"][0]["revoked"]
              or any(c["revoked"] for c in
                     api.get("/connections").json()["connections"]))

        print("5. rollback emits a NEW version number")
        r = api.post("/rollback", json={"to_version": 1})
        bundle = r.json()["bundle"]
        check("rollback to v1 content emitted as v4",
              bundle["version"] == 4 and bundle["kind"] == "rollback")
        sess_a2 = connect("a")
        check("client-a works again under rolled-back bundle",
              sess_a2.bundle_version == 4)
        sess_a2.close()

        print("6. expired and wrong-purpose certificates (old root trusted)")
        connect_expect_fail(service, fixtures, tmp, client_expired, "expired")
        connect_expect_fail(service, fixtures, tmp, client_wrong_eku,
                            "wrongeku")
        classes = {f["failure_class"] for f in failures()}
        check("CERT_EXPIRED recorded",
              FailureClass.CERT_EXPIRED.value in classes)
        check("WRONG_PURPOSE recorded",
              FailureClass.WRONG_PURPOSE.value in classes)

        print("7. hard cutover -> explainable no-common-trust failure")
        # ca_third has never coexisted with ca_old in any bundle version.
        api.post("/bundles", json={"roots_pem": [ca_third.cert_pem],
                                   "note": "hard cutover, no overlap"})
        connect_expect_fail(service, fixtures, tmp, client_a, "a")
        expl = api.post("/explain", json={
            "chain_pem": [client_a.cert_pem, ca_old.cert_pem],
        }).json()["explanation"]
        check("explanation: no common trust period",
              expl["common_trust_period"] is False,
              expl["reasoning"][-1][:100])

        print("8. independent verification cross-checks")
        rep = pycryptodome_verify_leaf_signature(client_b.cert_der,
                                                 ca_new.cert_der)
        check("PyCryptodome verifies client-b signature", rep.ok, rep.detail)
        if shutil.which("openssl"):
            ok_rep = openssl_verify(client_b.cert_pem, ca_new.cert_pem)
            bad_rep = openssl_verify(client_expired.cert_pem, ca_old.cert_pem)
            check("openssl CLI agrees: client-b valid under new root",
                  ok_rep.ok)
            check("openssl CLI agrees: expired cert rejected",
                  not bad_rep.ok, bad_rep.detail.splitlines()[-1][:80])
        else:
            print("  [SKIP] openssl binary not found")

        print(f"\nrun_id={service.run_id}")
        audit = api.get("/audit").json()["audit"]
        print(f"audit rows: {len(audit)} "
              f"(categories: {sorted({r['category'] for r in audit})})")
        print(f"handshake failures: {len(failures())} "
              f"(classes: {sorted({f['failure_class'] for f in failures()})})")
        print(f"replay: sqlite3 {tmp}/svc/trustlab.db "
              f"\"SELECT * FROM audit WHERE run_id='{service.run_id}'\"")
    finally:
        api.close()
        control.should_exit = True
        thread.join(timeout=5)
        service.stop()
        sess_a.close()
        sess_b.close()

    failed = [c for c in CHECKS if not c[1]]
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
