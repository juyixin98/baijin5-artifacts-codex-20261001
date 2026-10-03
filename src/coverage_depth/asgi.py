"""ASGI entry point: `uvicorn coverage_depth.asgi:app`.

The provenance database path can be overridden with COVERAGE_DB.
"""

import os

from .api import create_app

app = create_app(os.environ.get("COVERAGE_DB", "coverage_provenance.db"))
