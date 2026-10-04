"""Exact Euclidean distance transform numerical kernel.

Implements the separable *lower envelope of parabolas* algorithm of
Felzenszwalb & Huttenlocher ("Distance Transforms of Sampled Functions",
2012) for the squared Euclidean distance, extended with:

* anisotropic, per-axis pixel spacing (physical coordinates);
* a stable, documented tie rule: when two or more source pixels are exactly
  (to within a relative tolerance of 1e-12) equidistant, the source with the
  lexicographically smallest coordinate ``(row, column)`` is returned;
* explicit handling of the two degenerate cases: no source pixels at all
  (distance ``+inf``, nearest source ``-1``) and every pixel a source
  (distance 0, every pixel maps to itself).

The kernel is O(n) per scan line and O(H*W) overall; it never falls back to
Manhattan/chessboard approximations.

Tie tolerance rationale: geometry is expressed in physical units with
arbitrary spacings, so an absolute epsilon in metres is meaningless. We use
``rtol = 1e-12`` together with an absolute floor scaled by the squared cell
diagonal ``sy**2 + sx**2``. Distances that differ by less than one part in
1e12 are geometrically indistinguishable at double precision; this is
reported as an uncertainty by the API layer.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

RTOL = 1e-12

NO_SOURCE_LABEL = np.int64(-1)


@dataclass(frozen=True)
class EDTResult:
    # float64 (H, W); +inf where no source exists anywhere in the image.
    distances: np.ndarray
    # int64 (H, W); nearest source coordinate, -1 when no source exists.
    nearest_y: np.ndarray
    nearest_x: np.ndarray
    # bool (H, W); True where >=2 sources are equidistant within RTOL and
    # the lexicographic tie rule had to be applied.
    ties: np.ndarray
    spacing_y: float
    spacing_x: float
    has_sources: bool

    @property
    def shape(self) -> tuple[int, int]:
        return self.distances.shape

    def nearest_flat(self) -> np.ndarray:
        """Flat source index ``y * width + x``; -1 where no source exists."""
        h, w = self.distances.shape
        flat = np.full(self.distances.shape, -1, dtype=np.int64)
        ok = self.nearest_y >= 0
        flat[ok] = self.nearest_y[ok] * w + self.nearest_x[ok]
        return flat


def validate_inputs(mask: np.ndarray, spacing_y: float, spacing_x: float) -> None:
    if not isinstance(mask, np.ndarray):
        raise TypeError("mask must be a numpy.ndarray")
    if mask.ndim != 2:
        raise ValueError(f"mask must be 2-D, got {mask.ndim}-D")
    if mask.shape[0] == 0 or mask.shape[1] == 0:
        raise ValueError("mask must be non-empty on both axes")
    for name, value in (("spacing_y", spacing_y), ("spacing_x", spacing_x)):
        if not np.isfinite(value):
            raise ValueError(f"{name} must be finite, got {value!r}")
        if value <= 0.0:
            raise ValueError(f"{name} must be strictly positive, got {value!r}")


def _distances_tie(a: float, b: float, scale: float) -> bool:
    """True when squared distances a,b are indistinguishable at RTOL."""
    diff = abs(a - b)
    return diff <= max(RTOL * max(abs(a), abs(b), 1.0), RTOL * scale)


def _edt_1d(
    f: np.ndarray,
    labels: np.ndarray,
    in_ties: np.ndarray,
    spacing: float,
    atol_scale: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Lower-envelope EDT along one scan line.

    Parameters
    ----------
    f:
        Squared distance values at each center (``+inf`` = no candidate).
    labels:
        Opaque source label carried with each center (``-1`` for no
        candidate). The caller guarantees labels are ordered so that a
        smaller integer is the lexicographically smaller source coordinate.
    in_ties:
        Per-center flag: True if the candidate's own distance is already a
        tie from a previous separable pass (propagated to the output).
    spacing:
        Physical distance between adjacent samples on this axis.
    atol_scale:
        Squared cell-diagonal scale for the tie absolute floor.
    """
    n = f.shape[0]
    finite_centers = np.nonzero(np.isfinite(f))[0]
    out_d = np.full(n, np.inf, dtype=np.float64)
    out_l = np.full(n, NO_SOURCE_LABEL, dtype=np.int64)
    out_t = np.zeros(n, dtype=bool)
    if finite_centers.size == 0:
        return out_d, out_l, out_t

    a2 = spacing * spacing
    # Envelope state: v[k] = center of k-th parabola, z[k]/z[k+1] its range.
    v = np.empty(n, dtype=np.int64)
    z = np.empty(n + 1, dtype=np.float64)
    k = 0
    v[0] = finite_centers[0]
    z[0] = -np.inf
    z[1] = np.inf

    for q in finite_centers[1:]:
        # Intersection (in index units) between parabolas at v[k] and q.
        vk = v[k]
        s = (f[q] + a2 * q * q - (f[vk] + a2 * vk * vk)) / (2.0 * a2 * (q - vk))
        while k > 0 and s <= z[k]:
            k -= 1
            vk = v[k]
            s = (f[q] + a2 * q * q - (f[vk] + a2 * vk * vk)) / (
                2.0 * a2 * (q - vk)
            )
        k += 1
        v[k] = q
        z[k] = s
        z[k + 1] = np.inf

    last = k
    # Evaluation: advance with a STRICT comparison so that, when two
    # parabolas intersect exactly on an integer sample, we keep the earlier
    # (smaller center / smaller source label) parabola active and only peek
    # at the next one for an explicit tie comparison.
    #
    # Intersections are computed in float64 from values of magnitude up to
    # ~2*a2*n^2, so their absolute rounding error in index units can reach
    # ~2*eps*n^2 (worst case adjacent centers); size the detection window
    # accordingly, capped at 0.25 index so genuinely distinct segments are
    # never merged blindly (the numeric distance comparison below still
    # decides).
    eps_t = min(0.25, 4.0 * np.finfo(np.float64).eps * max(1, n) ** 2)
    seg = 0
    for i in range(n):
        while seg < last and z[seg + 1] < i - eps_t:
            seg += 1
        center = v[seg]
        best_d = f[center] + a2 * (i - center) ** 2
        best_l = labels[center]
        # A tie inherited from the previous pass counts only while this
        # candidate remains the winner.
        best_t = bool(in_ties[center])

        if seg < last and abs(z[seg + 1] - i) <= eps_t:
            # Boundary sits on this sample: the next parabola is equally close.
            c2 = v[seg + 1]
            d2 = f[c2] + a2 * (i - c2) ** 2
            if _distances_tie(d2, best_d, atol_scale):
                best_t = True
                if labels[c2] < best_l:
                    best_d = d2
                    best_l = labels[c2]
            elif d2 < best_d:
                best_d = d2
                best_l = labels[c2]
                best_t = bool(in_ties[c2])

        out_d[i] = best_d
        out_l[i] = best_l
        out_t[i] = best_t
    return out_d, out_l, out_t


def edt(
    mask: np.ndarray,
    spacing_y: float = 1.0,
    spacing_x: float = 1.0,
) -> EDTResult:
    """Exact Euclidean distance transform of a binary source mask.

    Parameters
    ----------
    mask:
        2-D boolean/array; truthy pixels are *source* pixels (distance 0).
    spacing_y, spacing_x:
        Physical pixel pitch along rows/columns; must be strictly positive
        and finite. Need not be equal (non-square pixels are supported).

    Returns
    -------
    EDTResult
    """
    validate_inputs(mask, spacing_y, spacing_x)
    m = np.asarray(mask, dtype=bool)
    h, w = m.shape
    sy = float(spacing_y)
    sx = float(spacing_x)
    atol_scale = sy * sy + sx * sx

    # f holds squared distances; labels hold source identity.
    f = np.where(m, 0.0, np.inf).astype(np.float64)
    cols = np.broadcast_to(np.arange(w, dtype=np.int64), (h, w))
    labels = np.where(m, cols, NO_SOURCE_LABEL).astype(np.int64)
    ties = np.zeros((h, w), dtype=bool)

    # ---- Pass 1: along columns within each row (physical x) --------------
    for y in range(h):
        d_row, l_row, t_row = _edt_1d(f[y], labels[y], ties[y], sx, atol_scale)
        f[y] = d_row
        labels[y] = l_row
        ties[y] = t_row

    # Encode (row, col) lexicographic order into one integer before the
    # vertical pass: a candidate's source lies in the parabola center row and
    # its label is the source column found in pass 1.
    rows = np.arange(h, dtype=np.int64)[:, None]
    finite_after_pass1 = np.isfinite(f)
    labels = np.where(
        finite_after_pass1, rows * np.int64(w) + labels, NO_SOURCE_LABEL
    )

    # ---- Pass 2: along rows within each column (physical y) --------------
    for x in range(w):
        d_col, l_col, t_col = _edt_1d(
            f[:, x], labels[:, x], ties[:, x], sy, atol_scale
        )
        f[:, x] = d_col
        labels[:, x] = l_col
        ties[:, x] = t_col

    has_sources = bool(m.any())
    nearest_y = np.full((h, w), -1, dtype=np.int64)
    nearest_x = np.full((h, w), -1, dtype=np.int64)
    if has_sources:
        ok = labels >= 0
        nearest_y[ok] = labels[ok] // w
        nearest_x[ok] = labels[ok] % w
    else:
        ties.fill(False)

    distances = np.sqrt(f, out=f)
    return EDTResult(
        distances=distances,
        nearest_y=nearest_y,
        nearest_x=nearest_x,
        ties=ties,
        spacing_y=sy,
        spacing_x=sx,
        has_sources=has_sources,
    )
