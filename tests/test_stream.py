"""Stream-level boundary semantics: block alignment, final partial block,
flush-once, output-length accounting, and failure categories."""
from __future__ import annotations

import numpy as np
import pytest

from app.errors import (
    BlockSizeMismatchError,
    EmptyBlockError,
    InputAfterFinalBlockError,
    InvalidSamplesError,
    SessionAlreadyFlushedError,
    StateBudgetExceededError,
)
from app.fixtures import make_exponential_ir, make_random_signal
from app.stream import StreamSession
from tests.conftest import run_stream
from tests.reference import direct_convolve_np


def make_session(block_size=128, ir_length=300, **kwargs) -> StreamSession:
    return StreamSession(
        session_id="s1",
        sample_rate=48_000,
        block_size=block_size,
        ir=make_exponential_ir(ir_length, seed=61),
        **kwargs,
    )


def test_full_length_invariant_total_output_equals_input_plus_ir_minus_one():
    ir = make_exponential_ir(300, seed=62)
    x = make_random_signal(1000, seed=63)
    y = run_stream(x, ir, block_size=128)
    assert len(y) == len(x) + len(ir) - 1
    np.testing.assert_allclose(y, direct_convolve_np(x, ir), atol=1e-9)


def test_flush_without_final_block_still_drains_tail():
    session = make_session()
    session.push_block(np.ones(128))
    tail = session.flush()
    assert tail.shape == (session.convolver.current_ir_length - 1,)


def test_wrong_block_size_rejected_with_category():
    session = make_session()
    with pytest.raises(BlockSizeMismatchError) as excinfo:
        session.push_block(np.zeros(100))
    assert excinfo.value.code == "BLOCK_SIZE_MISMATCH"


def test_oversized_block_rejected_even_when_final():
    session = make_session()
    with pytest.raises(BlockSizeMismatchError):
        session.push_block(np.zeros(200), final=True)


def test_empty_block_rejected():
    session = make_session()
    with pytest.raises(EmptyBlockError):
        session.push_block(np.zeros(0))


def test_non_finite_samples_rejected():
    session = make_session()
    with pytest.raises(InvalidSamplesError):
        session.push_block(np.full(128, np.inf))


def test_input_after_final_block_rejected():
    session = make_session()
    session.push_block(np.zeros(64), final=True)
    with pytest.raises(InputAfterFinalBlockError) as excinfo:
        session.push_block(np.zeros(128))
    assert excinfo.value.code == "INPUT_AFTER_FINAL_BLOCK"


def test_double_flush_rejected():
    session = make_session()
    session.push_block(np.zeros(128))
    session.flush()
    with pytest.raises(SessionAlreadyFlushedError) as excinfo:
        session.flush()
    assert excinfo.value.code == "SESSION_ALREADY_FLUSHED"


def test_input_after_flush_rejected():
    session = make_session()
    session.push_block(np.zeros(128))
    session.flush()
    with pytest.raises(SessionAlreadyFlushedError):
        session.push_block(np.zeros(128))


def test_state_budget_counts_spectrum_history_and_is_enforced():
    # IR of 3000 samples at block 128 -> 24 partitions -> ~24*129*16*2 bytes
    # of spectrum history alone; a 10 KiB budget must reject it.
    with pytest.raises(StateBudgetExceededError) as excinfo:
        make_session(block_size=128, ir_length=3000, max_state_bytes=10_000)
    assert excinfo.value.code == "STATE_BUDGET_EXCEEDED"
    assert excinfo.value.detail["projected_bytes"] > 10_000


def test_state_report_exposes_key_state():
    session = make_session()
    session.push_block(np.ones(128))
    report = session.state_report()
    assert report["blocks_processed"] == 1
    assert report["total_input_samples"] == 128
    assert report["total_output_samples"] == 128
    assert report["flushed"] is False
    assert report["state_bytes"]["total"] > 0
