"""Rollback semantics: a rollback emits a NEW version carrying older
content; version numbers are never reused."""
from __future__ import annotations

import pytest

from trustlab.errors import StateConflict, InputError

from .conftest import connect_client


def test_rollback_emits_new_version_number(service, fixtures, tmp_path):
    # Move to new-root-only (v2), then roll back to v1's content.
    service.bundles.create([fixtures.ca_new.cert_pem], kind="manual")
    rolled = service.bundles.rollback(to_version=1)

    assert rolled.version == 3  # NOT 1: old numbers are never reused
    assert rolled.kind == "rollback"
    assert rolled.root_fingerprints == service.bundles.history()[0] \
        .root_fingerprints

    # After rollback, old-root clients authenticate again, under v3.
    sess = connect_client(service, fixtures, tmp_path, fixtures.client_a, "a")
    assert sess.bundle_version == 3
    assert sess.identity["common_name"] == "client-a"

    # History keeps all three versions; v1 content and v3 version differ.
    history = service.bundles.history()
    assert [b.version for b in history] == [1, 2, 3]
    assert history[0].status == "retired"
    assert history[2].status == "active"


def test_version_reuse_is_a_state_conflict(service, fixtures):
    with pytest.raises(StateConflict) as excinfo:
        service.bundles.create([fixtures.ca_new.cert_pem], explicit_version=1)
    assert excinfo.value.category.value == "STATE_CONFLICT"

    # Skipping ahead is also rejected: versions must be exactly max+1.
    with pytest.raises(StateConflict):
        service.bundles.create([fixtures.ca_new.cert_pem],
                               explicit_version=99)


def test_rollback_to_unknown_version_is_input_error(service):
    with pytest.raises(InputError):
        service.bundles.rollback(to_version=999)
