"""Numerical kernels for geodesic reconstruction by dilation.

All kernels compute R_mask(marker): iterate the geodesic dilation
    f(x) = min(dilate(x, B), mask)
from x0 = marker (already constrained: marker <= mask) to the fixpoint.

Fixed boundary rule: the neighborhood is 4- or 8-connectivity on the
finite grid; out-of-image positions contribute the dtype minimum
(i.e. no wrap-around, no replication — border pixels simply have fewer
neighbors).  Every kernel in this package implements exactly this rule,
so their fixpoints coincide.
"""

from .queue_impl import KernelStats, reconstruct_queue
from .reference import reconstruct_reference

__all__ = ["KernelStats", "reconstruct_queue", "reconstruct_reference"]
