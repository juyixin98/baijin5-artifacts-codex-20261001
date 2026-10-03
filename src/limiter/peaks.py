"""Peak detectors.

Sample-peak mode (default): per-sample max of |x| across channels; the
threshold is a hard structural guarantee on the emitted sample peak.

True-peak mode: the buffer is oversampled with a polyphase FIR and the
peak is measured on the reconstructed waveform. The threshold then
constrains the *reconstructed* (inter-sample) peak up to the tolerance
documented in README/docs (filter ripple + base-rate gain quantization).
"""

from __future__ import annotations

import numpy as np
from scipy.signal import resample_poly

#: Samples of history kept before the live buffer, and of future context
#: required after the last emitted sample, so the polyphase filter sees a
#: continuous signal instead of artificial zeros at buffer edges.
TRUE_PEAK_GUARD_SAMPLES = 64


class SamplePeakDetector:
    """per-sample max |x| across channels (channel-linked)."""

    guard_samples = 0

    def peaks(self, buf: np.ndarray) -> np.ndarray:
        return np.max(np.abs(buf), axis=1)


class TruePeakDetector:
    """Oversampled (reconstructed) peak, max across channels."""

    guard_samples = TRUE_PEAK_GUARD_SAMPLES

    def __init__(self, factor: int = 4):
        if factor not in (2, 4, 8):
            raise ValueError("oversample factor must be one of 2, 4, 8")
        self.factor = factor

    def peaks(self, buf: np.ndarray) -> np.ndarray:
        n = buf.shape[0]
        if n == 0:
            return np.empty(0, dtype=np.float64)
        up = resample_poly(buf, self.factor, 1, axis=0)
        # resample_poly compensates filter delay: up[i*F:(i+1)*F] belongs to
        # base sample i. Take the max of each base sample's oversampled cell.
        cells = np.abs(up[: n * self.factor]).reshape(n, self.factor, buf.shape[1])
        return np.max(cells, axis=(1, 2))


def make_detector(true_peak: bool, oversample_factor: int = 4):
    return (
        TruePeakDetector(oversample_factor) if true_peak else SamplePeakDetector()
    )
