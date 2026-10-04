"""Run the service:  python -m sae  (requires SAE_MASTER_KEY in env)."""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "sae.api:create_app",
        factory=True,
        host=os.environ.get("SAE_HOST", "127.0.0.1"),
        port=int(os.environ.get("SAE_PORT", "8000")),
    )


if __name__ == "__main__":
    main()
