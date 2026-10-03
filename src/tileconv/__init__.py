"""tileconv — tiled convolution and separable filtering for very large images.

Layout:
- contract:   image/kernel data contracts, digests, anchor semantics
- padding:    boundary index resolution (mirror / constant / periodic)
- kernel:     numerical kernels (valid correlation, separable passes, scipy reference)
- tiling:     tile grid decomposition with halo-aware read windows
- job:        checkpointed tiled job engine (interrupt / resume, digest binding)
- storage:    memmap-backed local image store
- validation: comparison of tiled output against the direct reference
- api:        FastAPI service layer
"""

__version__ = "0.1.0"

from .contract import BoundaryMode, ImageSpec, KernelSpec, default_anchor  # noqa: F401
