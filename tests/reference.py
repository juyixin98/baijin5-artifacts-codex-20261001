"""Independent reference implementation used ONLY by tests.

This is a deliberately naive, pure-Python transposed-DF2T cascade. It shares
no code with ``app.dsp.filter`` (which delegates to scipy.signal.lfilter), so
agreement between the two is evidence of correctness, not of a shared bug.
"""

from __future__ import annotations


def reference_sos_filter(
    sections: list[list[float]],
    samples: list[list[float]],
    state: list[list[list[float]]] | None = None,
) -> tuple[list[list[float]], list[list[list[float]]]]:
    """Filter ``samples`` (channels x samples) through a normalized SOS cascade.

    ``sections`` rows are [b0, b1, b2, a0, a1, a2] with a0 == 1.
    Returns (output, final_state); state layout is [section][channel][2].
    """
    n_sections = len(sections)
    n_channels = len(samples)
    n_samples = len(samples[0]) if samples else 0
    if state is None:
        state = [[[0.0, 0.0] for _ in range(n_channels)] for _ in range(n_sections)]

    out = [[0.0] * n_samples for _ in range(n_channels)]
    for c in range(n_channels):
        for n in range(n_samples):
            v = samples[c][n]
            for s in range(n_sections):
                b0, b1, b2, _a0, a1, a2 = sections[s]
                z0, z1 = state[s][c]
                y = b0 * v + z0
                state[s][c][0] = b1 * v - a1 * y + z1
                state[s][c][1] = b2 * v - a2 * y
                v = y
            out[c][n] = v
    return out, state


def reference_whole(sections, samples):
    """Convenience: zero-state whole-signal run, output only."""
    out, _ = reference_sos_filter(sections, samples)
    return out
