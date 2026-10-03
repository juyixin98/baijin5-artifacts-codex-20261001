"""Tests for the stream session state machine."""

from __future__ import annotations

import numpy as np
import pytest

from app.errors import InputError, ResourceExhaustedError, StateConflictError
from app.state.store import SessionState, SessionStore

from .fixtures import KNOWN_FIR, make_response, white_excitation


def test_happy_path_open_then_finalize():
    store = SessionStore()
    session = store.create()
    assert session.state is SessionState.OPEN
    x = white_excitation(500, seed=41)
    y = make_response(x, KNOWN_FIR)
    store.append_chunk(session.session_id, x[:250], y[:250])
    store.append_chunk(session.session_id, x[250:], y[250:])
    fx, fy = store.finalize(session.session_id)
    np.testing.assert_array_equal(fx, x)
    np.testing.assert_array_equal(fy, y)
    assert store.get(session.session_id).state is SessionState.FINALIZED


def test_append_after_finalize_conflicts():
    store = SessionStore()
    session = store.create()
    store.append_chunk(session.session_id, [1.0, 2.0], [1.0, 2.0])
    store.finalize(session.session_id)
    with pytest.raises(StateConflictError, match="finalized"):
        store.append_chunk(session.session_id, [3.0], [3.0])


def test_double_finalize_conflicts():
    store = SessionStore()
    session = store.create()
    store.append_chunk(session.session_id, [1.0], [1.0])
    store.finalize(session.session_id)
    with pytest.raises(StateConflictError, match="already"):
        store.finalize(session.session_id)


def test_finalize_without_data_conflicts():
    store = SessionStore()
    session = store.create()
    with pytest.raises(StateConflictError, match="no"):
        store.finalize(session.session_id)


def test_unknown_session_conflicts():
    store = SessionStore()
    with pytest.raises(StateConflictError, match="no such"):
        store.get("does-not-exist")
    with pytest.raises(StateConflictError, match="no such"):
        store.append_chunk("does-not-exist", [1.0], [1.0])
    with pytest.raises(StateConflictError, match="no such"):
        store.finalize("does-not-exist")


def test_chunk_length_mismatch_is_input_error():
    store = SessionStore()
    session = store.create()
    with pytest.raises(InputError, match="length"):
        store.append_chunk(session.session_id, [1.0, 2.0], [1.0])


def test_accumulated_samples_bounded(small_config):
    store = SessionStore(small_config)
    session = store.create()
    store.append_chunk(
        session.session_id,
        np.zeros(small_config.max_samples),
        np.zeros(small_config.max_samples),
    )
    with pytest.raises(ResourceExhaustedError, match="maximum"):
        store.append_chunk(session.session_id, [1.0], [1.0])
