"""Geodesic dilation reconstruction service.

Modules:
    config      -- deployment settings (file + env overrides)
    errors      -- error taxonomy with stable categories
    contracts   -- image data contract (coercion + validation of marker/mask)
    kernel      -- numerical kernels (sync iteration, FIFO queue)
    tiles       -- chunked (tiled) job execution
    validation  -- post-hoc property checks (fixed point, idempotency, monotonicity)
    diagnostics -- request-scoped logging with masked (aggregate-only) image stats
    service     -- FastAPI HTTP interface
"""

__version__ = "0.1.0"
