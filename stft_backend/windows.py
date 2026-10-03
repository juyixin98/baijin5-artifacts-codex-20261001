"""Window function resolution.

Windows are DFT-even (``fftbins=True``, the periodic convention), matching
both ``scipy.signal.stft`` and the OLA normalization used by
``algorithms``: a window and its inverse twin are the *same* array, so
"window function, center padding and final trimming are unified".
"""

from __future__ import annotations

from typing import Union

import numpy as np
from scipy.signal import get_window

from .errors import ErrorCode, StftError

WindowSpec = Union[str, list[float]]


def resolve_window(spec: WindowSpec, nperseg: int) -> np.ndarray:
    """Resolve a window name or explicit samples to a length-``nperseg`` array.

    Named windows are generated DFT-even. An explicit array must be real,
    finite and have exactly ``nperseg`` samples.
    """

    if isinstance(spec, str):
        try:
            window = np.asarray(get_window(spec, nperseg, fftbins=True), dtype=np.float64)
        except (ValueError, KeyError) as exc:  # pragma: no cover - scipy text varies
            raise StftError(
                ErrorCode.UNSUPPORTED_WINDOW,
                f"Unsupported or invalid window name {spec!r}.",
                stage="window",
                details={"window": spec},
            ) from exc
    else:
        try:
            window = np.asarray(spec, dtype=np.float64)
        except (TypeError, ValueError) as exc:
            raise StftError(
                ErrorCode.INVALID_PARAMETER,
                "Explicit window samples must be a list of real numbers.",
                stage="window",
            ) from exc
        if window.ndim != 1:
            raise StftError(
                ErrorCode.INVALID_PARAMETER,
                "Explicit window must be one-dimensional.",
                stage="window",
                details={"shape": list(window.shape)},
            )
        if window.shape[0] != nperseg:
            raise StftError(
                ErrorCode.WINDOW_LENGTH_MISMATCH,
                f"Explicit window has {window.shape[0]} samples; "
                f"must equal nperseg={nperseg}.",
                stage="window",
                details={"window_length": int(window.shape[0]), "nperseg": int(nperseg)},
            )
        if not np.all(np.isfinite(window)):
            raise StftError(
                ErrorCode.INVALID_PARAMETER,
                "Explicit window contains non-finite samples.",
                stage="window",
            )
    return window
