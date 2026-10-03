"""Independent topology probes built on scipy.ndimage.

These helpers are deliberately NOT implemented via the thinning kernel: they
are the independent yardstick tests and the /validate endpoint use to check
the kernel's topology-preservation claims (components, holes, endpoints).
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage

# Compatible adjacency, matching the kernel contract:
# foreground 8-connected, background 4-connected.
STRUCTURE_FG = np.ones((3, 3), dtype=int)
STRUCTURE_BG = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=int)

_NEIGHBOR_KERNEL = np.ones((3, 3), dtype=int)
_NEIGHBOR_KERNEL[1, 1] = 0


def count_foreground_components(mask: np.ndarray) -> int:
    """Number of 8-connected foreground components."""
    _, n = ndimage.label((mask != 0).astype(np.uint8), structure=STRUCTURE_FG)
    return int(n)


def count_holes(mask: np.ndarray) -> int:
    """Number of background 4-connected components not touching the border."""
    fg = (mask != 0).astype(np.uint8)
    bg = 1 - fg
    labels, _ = ndimage.label(bg, structure=STRUCTURE_BG)
    border_labels = set(np.unique(np.concatenate([
        labels[0, :], labels[-1, :], labels[:, 0], labels[:, -1]
    ])).tolist())
    border_labels.discard(0)
    all_labels = set(np.unique(labels).tolist()) - {0}
    return len(all_labels - border_labels)


def neighbor_count(skel: np.ndarray) -> np.ndarray:
    """8-neighborhood foreground count for every pixel."""
    s = (skel != 0).astype(int)
    return ndimage.convolve(s, _NEIGHBOR_KERNEL, mode="constant", cval=0)


def endpoint_pixels(skel: np.ndarray) -> np.ndarray:
    """Foreground pixels with exactly one 8-neighbor."""
    s = (skel != 0).astype(np.uint8)
    return (s == 1) & (neighbor_count(s) == 1)


def junction_pixels(skel: np.ndarray) -> np.ndarray:
    """Foreground pixels with three or more 8-neighbors."""
    s = (skel != 0).astype(np.uint8)
    return (s == 1) & (neighbor_count(s) >= 3)


def has_2x2_block(skel: np.ndarray) -> bool:
    """True if any 2x2 all-foreground block remains (skeleton not fully thin)."""
    s = (skel != 0).astype(np.uint8)
    acc = s[:-1, :-1] + s[1:, :-1] + s[:-1, 1:] + s[1:, 1:]
    return bool((acc == 4).any())
