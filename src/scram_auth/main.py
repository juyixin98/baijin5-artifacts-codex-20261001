"""Local uvicorn entry point: ``python -m scram_auth.main`` or ``./scripts/run_server.sh``.

Binds to the loopback address from config only. Override the config path with
the SCRAM_CONFIG environment variable.
"""
from __future__ import annotations

import uvicorn

from .app import create_app
from .config import load_config


def main() -> None:
    config = load_config()
    app = create_app(config)
    uvicorn.run(
        app,
        host=config.server.host,
        port=config.server.port,
        log_level="info",
        # Loopback test service: no proxy headers, no forwarded-for trust.
        proxy_headers=False,
        forwarded_allow_ips=None,
    )


if __name__ == "__main__":
    main()
