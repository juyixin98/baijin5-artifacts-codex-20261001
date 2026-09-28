"""Run the HTTP service: ``python -m sparse_embeddings``."""

from __future__ import annotations

import argparse

import uvicorn

from sparse_embeddings import __version__


def main() -> None:
    parser = argparse.ArgumentParser(description="Sparse embedding optimizer service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args()
    uvicorn.run(
        "sparse_embeddings.api.app:app",
        host=args.host,
        port=args.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
