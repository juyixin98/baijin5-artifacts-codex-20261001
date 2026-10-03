"""Stream state manager: lifecycle and exact streamed reconstruction."""
from __future__ import annotations

import numpy as np
import pytest

from app.config import Settings
from app.lpc.pipeline import analyze_frame
from app.stream import StreamManager
from tests.conftest import load_fixture

SETTINGS = Settings()


def test_manager_lifecycle():
    manager = StreamManager()
    session = manager.create(order=10, window="hann")
    assert manager.get(session.stream_id) is session
    assert len(manager) == 1
    manager.delete(session.stream_id)
    assert len(manager) == 0
    with pytest.raises(KeyError):
        manager.get(session.stream_id)
    with pytest.raises(KeyError):
        manager.delete("does-not-exist")


def test_streamed_roundtrip_reconstructs_full_signal():
    x = np.asarray(load_fixture("ar_process.json")["samples"])
    manager = StreamManager()
    session = manager.create(order=10, window="hann")

    frame_len = 320
    frames = [x[i : i + frame_len] for i in range(0, len(x), frame_len)]
    reconstructed = []
    for frame in frames:
        outcome = analyze_frame(
            frame,
            session.order,
            session.window,
            SETTINGS,
            analysis_filter=session.analysis_filter,
        )
        reconstructed.append(
            session.synthesis_filter.process(
                outcome.result.residual, outcome.result.coefficients
            )
        )
        session.frames_processed += 1

    x_hat = np.concatenate(reconstructed)
    rel_err = np.linalg.norm(x_hat - x) / np.linalg.norm(x)
    assert rel_err <= SETTINGS.reconstruction_tol
    assert session.frames_processed == len(frames)
    snap = session.snapshot()
    assert len(snap["analysis_state"]) == session.order
    assert len(snap["synthesis_state"]) == session.order
