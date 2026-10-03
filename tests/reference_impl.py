"""Independent textbook reference implementation of LMS/NLMS.

Written in pure Python (no NumPy) directly from the textbook equations, so
that cross-checking the production core against it is a genuine
implementation-vs-implementation comparison, not the core grading itself.

Conventions match app.algorithms.lms: buffer holds the last L reference
samples (oldest first, newest last); error uses pre-update weights;
update happens after the error is computed, unless the sample is frozen.
"""

from __future__ import annotations


def lms_reference(
    weights: list[float],
    buffer: list[float],
    mu: float,
    xs: list[float],
    ds: list[float],
    freezes: list[bool] | None = None,
) -> tuple[list[float], list[float], list[float], list[float]]:
    """Returns (final_weights, final_buffer, outputs, errors)."""
    w = list(weights)
    buf = list(buffer)
    ys: list[float] = []
    es: list[float] = []
    for i, (x, d) in enumerate(zip(xs, ds)):
        buf = buf[1:] + [x]
        y = sum(wi * xi for wi, xi in zip(w, buf))
        e = d - y
        if freezes is None or not freezes[i]:
            w = [wi + mu * e * xi for wi, xi in zip(w, buf)]
        ys.append(y)
        es.append(e)
    return w, buf, ys, es


def nlms_reference(
    weights: list[float],
    buffer: list[float],
    mu: float,
    epsilon: float,
    xs: list[float],
    ds: list[float],
    freezes: list[bool] | None = None,
) -> tuple[list[float], list[float], list[float], list[float]]:
    """Returns (final_weights, final_buffer, outputs, errors)."""
    w = list(weights)
    buf = list(buffer)
    ys: list[float] = []
    es: list[float] = []
    for i, (x, d) in enumerate(zip(xs, ds)):
        buf = buf[1:] + [x]
        y = sum(wi * xi for wi, xi in zip(w, buf))
        e = d - y
        if freezes is None or not freezes[i]:
            denom = epsilon + sum(xi * xi for xi in buf)
            w = [wi + (mu / denom) * e * xi for wi, xi in zip(w, buf)]
        ys.append(y)
        es.append(e)
    return w, buf, ys, es
