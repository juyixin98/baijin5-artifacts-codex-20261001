"""Geodesic dilation reconstruction service.

Morphological reconstruction by dilation of a marker image under a mask,
exposed as a FastAPI service with a validated image contract, pluggable
numerical kernels (naive reference / FIFO queue / tiled sweeps) and
structured diagnostics.
"""

__version__ = "0.1.0"
