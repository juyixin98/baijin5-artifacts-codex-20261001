#!/usr/bin/env python3
"""Probe: verify that scipy.ndimage native modes match the documented
boundary semantics used by the engine (mirror / constant / periodic), and
that the odd-centering trick makes anchor placement exact.

Prints PASS/FAIL lines; exits non-zero if any mapping disagrees. This guards
the reference path (kernel.direct_reference) against scipy version drift.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
from scipy import ndimage

failures = []


def check(name: str, got: np.ndarray, expected: np.ndarray) -> None:
    ok = np.allclose(got, expected, atol=1e-12)
    print(f"{'PASS' if ok else 'FAIL'} {name}")
    if not ok:
        print(f"  got:      {got.ravel()[:12]}")
        print(f"  expected: {expected.ravel()[:12]}")
        failures.append(name)


def main() -> int:
    x = np.array([10.0, 20.0, 30.0, 40.0])
    k = np.array([1.0, 1.0, 1.0])  # centered sum-3

    # hand-computed expectations under the documented semantics
    mirror_expected = np.array([
        10 + 20 + 20,   # i=0: f(-1)=1 -> 20, 10, 20
        10 + 20 + 30,
        20 + 30 + 40,
        30 + 40 + 30.0,  # i=3: f(4)=2 -> 30
    ])
    periodic_expected = np.array([
        40 + 10 + 20.0,
        10 + 20 + 30,
        20 + 30 + 40,
        30 + 40 + 10.0,
    ])
    constant_expected = np.array([
        0 + 10 + 20.0,
        10 + 20 + 30,
        20 + 30 + 40,
        30 + 40 + 0.0,
    ])

    check("scipy mode 'mirror' == whole-sample symmetric",
          ndimage.convolve1d(x, k, mode="mirror"), mirror_expected)
    check("scipy mode 'wrap' == modulo periodic",
          ndimage.convolve1d(x, k, mode="wrap"), periodic_expected)
    check("scipy mode 'constant' == zero fill",
          ndimage.convolve1d(x, k, mode="constant", cval=0.0), constant_expected)

    # odd-centering: even kernel [1,2,3,4] anchor 1 must behave like the
    # zero-padded odd kernel [0,1,2,3,4] with center anchor 2.
    img = np.zeros(9)
    img[4] = 1.0
    even_k = np.array([1.0, 2.0, 3.0, 4.0])
    odd_k = np.array([0.0, 1.0, 2.0, 3.0, 4.0])  # anchor 2 == old anchor 1 + pad_left 1
    got = ndimage.convolve1d(img, odd_k, mode="constant")
    expected = np.zeros(9)
    expected[3:7] = even_k  # convolution: kernel verbatim, anchor on impulse
    check("odd-centered even kernel places anchor exactly", got, expected)

    if failures:
        print(f"\n{len(failures)} probe(s) FAILED")
        return 1
    print("\nall probes passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
