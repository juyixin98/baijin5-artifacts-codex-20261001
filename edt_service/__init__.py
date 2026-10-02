"""Exact Euclidean distance transform (EDT) service for binary rasters.

Modules
-------
- ``contracts``  : image/grid data contract and request/response schemas.
- ``kernel``     : separable lower-envelope (parabola) exact EDT numeric kernel.
- ``reference``  : independent O(N*S) brute-force oracle used by tests.
- ``tiling``     : exact tiled execution for very large rasters.
- ``api``        : FastAPI validation/execution interface.
- ``config``     : runtime configuration.
- ``errors``     : failure taxonomy shared by all layers.
"""

__version__ = "1.0.0"
