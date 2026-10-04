"""Cross-validation against SciPy's EDT (independent third implementation).

SciPy and our kernel may legitimately disagree on *which* source wins an
exact tie (SciPy has no documented stable tie rule), so here distances must
agree and the source our kernel names must itself realize the reported
distance.
"""
from __future__ import annotations

import math

import numpy as np
import pytest
from scipy import ndimage

from app.kernel import edt


@pytest.mark.parametrize("seed", [0, 7, 99])
@pytest.mark.parametrize("shape", [(20, 13), (31, 31), (1, 40), (40, 1), (17, 3)])
@pytest.mark.parametrize("spacing", [(1.0, 1.0), (0.4, 2.5), (2.0, 0.3)])
def test_distances_match_scipy(seed, shape, spacing):
    rng = np.random.default_rng(seed)
    mask = rng.random(shape) < 0.25
    sy, sx = spacing
    res = edt(mask, sy, sx)

    sd, (sy_idx, sx_idx) = ndimage.distance_transform_edt(
        ~mask, sampling=(sy, sx), return_indices=True
    )

    if not mask.any():
        assert np.isinf(res.distances).all()
        return

    assert np.allclose(res.distances, sd, rtol=1e-12, atol=1e-10), (
        np.abs(res.distances - sd).max()
    )

    # The named source must actually sit at the reported distance.
    yy, xx = np.indices(shape)
    dy = (yy - res.nearest_y) * sy
    dx = (xx - res.nearest_x) * sx
    recomputed = np.hypot(dy, dx)
    assert np.allclose(recomputed, res.distances, rtol=1e-12, atol=1e-10)
    # ...and it really is a source pixel.
    assert mask[res.nearest_y, res.nearest_x].all()
