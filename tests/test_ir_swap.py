"""IR swap policies: restart exactness, crossfade transparency under a
silent guard interval, steady-state convergence after crossfade, and budget
enforcement during swaps."""
from __future__ import annotations

import numpy as np
import pytest

from app.convolution import SwapStrategy
from app.errors import StateBudgetExceededError
from app.fixtures import make_exponential_ir, make_impulse, make_random_signal
from app.stream import StreamSession
from tests.reference import direct_convolve_np

B = 128


def make_session(ir, strategy=SwapStrategy.RESTART, crossfade_blocks=4, **kw):
    return StreamSession(
        session_id="swap",
        sample_rate=48_000,
        block_size=B,
        ir=ir,
        swap_strategy=strategy,
        crossfade_blocks=crossfade_blocks,
        **kw,
    )


def feed(session, x):
    """Push ``x`` in full blocks (len must be a multiple of B).  Never marks
    a block final: these tests keep the stream open across swaps and flush
    explicitly at the end."""
    assert len(x) % B == 0
    outs = []
    for start in range(0, len(x), B):
        outs.append(session.push_block(x[start : start + B]))
    return np.concatenate(outs)


def test_restart_discards_old_tail_and_applies_new_ir():
    ir_old = make_exponential_ir(500, seed=71)
    ir_new = make_exponential_ir(200, seed=72)
    session = make_session(ir_old)

    x1 = make_random_signal(4 * B, seed=73)
    feed(session, x1)
    session.swap_ir(ir_new, SwapStrategy.RESTART)

    # After restart the convolver is cold: silence in -> silence out.
    y_silence = feed(session, np.zeros(2 * B))
    np.testing.assert_allclose(y_silence, 0.0, atol=1e-12)

    # And an impulse now reproduces exactly the new IR.
    y = feed(session, make_impulse(2 * B))
    tail = session.flush()
    full = np.concatenate([y, tail])
    np.testing.assert_allclose(full[: len(ir_new)], ir_new, atol=1e-9)


def test_restart_output_matches_fresh_convolution_from_swap_point():
    ir_old = make_exponential_ir(300, seed=75)
    ir_new = make_exponential_ir(300, seed=76)
    session = make_session(ir_old)
    feed(session, make_random_signal(3 * B, seed=77))
    session.swap_ir(ir_new, SwapStrategy.RESTART)
    x2 = make_random_signal(5 * B, seed=78)
    y2 = np.concatenate([feed(session, x2), session.flush()])
    np.testing.assert_allclose(y2, direct_convolve_np(x2, ir_new), atol=1e-9)


def test_crossfade_with_silent_guard_is_transparent():
    """With identical IRs and >= ir_length of silence around the swap, the
    crossfade mixes two convolvers whose outputs are identical, so the mix
    must equal uninterrupted convolution sample-for-sample."""
    ir = make_exponential_ir(256, seed=81)  # fits exactly in 2 partitions
    x = np.concatenate(
        [make_random_signal(4 * B, seed=82), np.zeros(8 * B), make_random_signal(4 * B, seed=83)]
    )
    # Reference: no swap at all.
    ref_session = make_session(ir)
    ref = np.concatenate([feed(ref_session, x), ref_session.flush()])

    session = make_session(ir, strategy=SwapStrategy.CROSSFADE, crossfade_blocks=4)
    y1 = feed(session, x[: 6 * B])
    session.swap_ir(ir, SwapStrategy.CROSSFADE)  # swap during the silent guard
    y2 = feed(session, x[6 * B :])
    out = np.concatenate([y1, y2, session.flush()])
    np.testing.assert_allclose(out, ref, atol=1e-9)


def test_crossfade_converges_to_new_ir_steady_state():
    ir_old = make_exponential_ir(256, seed=85)
    ir_new = make_exponential_ir(256, seed=86)
    session = make_session(ir_old, strategy=SwapStrategy.CROSSFADE, crossfade_blocks=4)
    feed(session, make_random_signal(4 * B, seed=87))
    session.swap_ir(ir_new, SwapStrategy.CROSSFADE)
    # Let the fade finish and the new FDL fill, then probe with an impulse.
    feed(session, np.zeros(8 * B))
    y = feed(session, make_impulse(4 * B))
    full = np.concatenate([y, session.flush()])
    np.testing.assert_allclose(full[: len(ir_new)], ir_new, atol=1e-9)


def test_crossfade_block_contract_holds_during_fade():
    ir_old = make_exponential_ir(512, seed=91)
    ir_new = make_exponential_ir(512, seed=92)
    session = make_session(ir_old, strategy=SwapStrategy.CROSSFADE, crossfade_blocks=3)
    feed(session, make_random_signal(2 * B, seed=93))
    session.swap_ir(ir_new)
    assert session.convolver.fade_remaining_blocks == 3
    for _ in range(3):
        out = session.push_block(make_random_signal(B, seed=94))
        assert out.shape == (B,)  # block in = block out, even mid-fade
    assert session.convolver.fade_remaining_blocks == 0


def test_swap_budget_enforced_during_crossfade():
    # Budget fits one convolver generation but not the two that coexist
    # during a crossfade.
    ir_small = make_exponential_ir(300, seed=95)
    single = StreamSession(
        session_id="probe", sample_rate=48_000, block_size=B, ir=ir_small
    ).convolver.state_bytes()["total"]
    session = make_session(ir_small, max_state_bytes=int(single * 1.5))
    session.push_block(np.zeros(B))
    with pytest.raises(StateBudgetExceededError):
        session.swap_ir(make_exponential_ir(300, seed=96), SwapStrategy.CROSSFADE)
    # Restart only needs one generation: accepted under the same budget.
    session.swap_ir(make_exponential_ir(300, seed=96), SwapStrategy.RESTART)
