"""Runnable service entry point.

Examples:
    python -m trustlab --data-dir /tmp/trustlab --generate-fixtures
    python -m trustlab --data-dir /tmp/trustlab \
        --server-cert srv.crt.pem --server-key srv.key.pem \
        --roots root1.pem root2.pem
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .certs import make_ca, make_leaf
from .control import create_app
from .service import ServiceConfig, TrustLabService


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trustlab")
    parser.add_argument("--data-dir", default="./trustlab-data")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8643,
                        help="mTLS data-plane port")
    parser.add_argument("--control-port", type=int, default=8600,
                        help="HTTP control-plane port")
    parser.add_argument("--max-connections", type=int, default=64)
    parser.add_argument("--server-cert")
    parser.add_argument("--server-key")
    parser.add_argument("--roots", nargs="*", default=None,
                        help="PEM files of initially trusted client roots")
    parser.add_argument("--generate-fixtures", action="store_true",
                        help="generate a local server CA + one client root "
                             "CA into <data-dir>/fixtures and use them")
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir)
    fixtures_dir = data_dir / "fixtures"

    if args.generate_fixtures:
        fixtures_dir.mkdir(parents=True, exist_ok=True)
        server_ca = make_ca("server-ca")
        server = make_leaf(server_ca, "localhost", eku="server",
                           san_dns=("localhost",))
        client_root = make_ca("client-root-a")
        (fixtures_dir / "server-ca.pem").write_text(server_ca.cert_pem)
        (fixtures_dir / "server.crt.pem").write_text(server.cert_pem)
        (fixtures_dir / "server.key.pem").write_text(server.key_pem)
        (fixtures_dir / "client-root-a.pem").write_text(client_root.cert_pem)
        (fixtures_dir / "client-root-a.key.pem").write_text(client_root.key_pem)
        args.server_cert = str(fixtures_dir / "server.crt.pem")
        args.server_key = str(fixtures_dir / "server.key.pem")
        args.roots = [str(fixtures_dir / "client-root-a.pem")]
        print(f"fixtures written to {fixtures_dir}", file=sys.stderr)

    if not (args.server_cert and args.server_key and args.roots):
        parser.error("provide --server-cert/--server-key/--roots, "
                     "or use --generate-fixtures")

    service = TrustLabService(
        ServiceConfig(data_dir=data_dir, host=args.host, port=args.port,
                      max_connections=args.max_connections),
        server_cert_pem=Path(args.server_cert).read_text(),
        server_key_pem=Path(args.server_key).read_text(),
        initial_roots_pem=[Path(p).read_text() for p in args.roots],
    )
    service.start()
    print(f"run_id={service.run_id}", file=sys.stderr)
    print(f"mTLS data plane on {service.host}:{service.port}", file=sys.stderr)
    print(f"control plane on http://{args.host}:{args.control_port}",
          file=sys.stderr)

    import uvicorn

    app = create_app(service)
    try:
        uvicorn.run(app, host=args.host, port=args.control_port,
                    log_level="warning")
    finally:
        service.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
