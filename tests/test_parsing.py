"""Parsing and validation rules: unknown alleles, quality->cost, input errors."""

import pytest

from app.errors import FailureCategory, PhasingError
from app.models import PhaseRequest
from app.parsing import clamp_quality, parse_request

from conftest import load_fixture, request_from_fixture


def _request(variants, reads):
    return PhaseRequest(variants=variants, reads=reads)


def test_unknown_allele_excluded_and_counted(settings):
    parsed = parse_request(request_from_fixture("unknown_allele.json"), settings)
    assert parsed.unknown_allele_observations == 1
    # r3 keeps only its v2 observation; the "N" call at v1 is dropped.
    r3 = next(f for f in parsed.fragments if f.read_id == "r3")
    assert len(r3.observations) == 1
    assert r3.observations[0].site == 1
    assert r3.observations[0].bit == 1
    assert r3.observations[0].weight == 40.0


def test_quality_cost_rule_is_fixed(settings):
    # cost == phred quality, clamped to [0, max_quality]
    assert clamp_quality(30, settings) == (30.0, False)
    assert clamp_quality(0, settings) == (0.0, False)
    assert clamp_quality(99, settings) == (float(settings.max_quality), False)
    # missing quality -> configured default, flagged as defaulted
    assert clamp_quality(None, settings) == (float(settings.default_quality), True)


def test_default_quality_applied_and_counted(settings):
    req = _request(
        variants=[{"id": "v1", "pos": 1, "ref": "A", "alt": "G"}],
        reads=[{"read_id": "r1", "variant_id": "v1", "allele": "A"}],
    )
    parsed = parse_request(req, settings)
    assert parsed.defaulted_quality_observations == 1
    assert parsed.fragments[0].observations[0].weight == float(settings.default_quality)


def test_identical_ref_alt_rejected(settings):
    req = _request(
        variants=[{"id": "v1", "pos": 1, "ref": "A", "alt": "a"}],
        reads=[],
    )
    with pytest.raises(PhasingError) as excinfo:
        parse_request(req, settings)
    assert excinfo.value.category is FailureCategory.INVALID_INPUT


def test_non_dna_allele_rejected(settings):
    req = _request(
        variants=[{"id": "v1", "pos": 1, "ref": "A", "alt": "X"}],
        reads=[],
    )
    with pytest.raises(PhasingError) as excinfo:
        parse_request(req, settings)
    assert excinfo.value.category is FailureCategory.INVALID_INPUT


def test_read_referencing_unknown_variant_rejected(settings):
    req = _request(
        variants=[{"id": "v1", "pos": 1, "ref": "A", "alt": "G"}],
        reads=[{"read_id": "r1", "variant_id": "nope", "allele": "A"}],
    )
    with pytest.raises(PhasingError) as excinfo:
        parse_request(req, settings)
    assert excinfo.value.category is FailureCategory.INVALID_INPUT


def test_duplicate_read_variant_pair_rejected(settings):
    req = _request(
        variants=[{"id": "v1", "pos": 1, "ref": "A", "alt": "G"}],
        reads=[
            {"read_id": "r1", "variant_id": "v1", "allele": "A"},
            {"read_id": "r1", "variant_id": "v1", "allele": "G"},
        ],
    )
    with pytest.raises(PhasingError) as excinfo:
        parse_request(req, settings)
    assert excinfo.value.category is FailureCategory.INVALID_INPUT


def test_duplicate_variant_id_rejected(settings):
    req = _request(
        variants=[
            {"id": "v1", "pos": 1, "ref": "A", "alt": "G"},
            {"id": "v1", "pos": 2, "ref": "C", "alt": "T"},
        ],
        reads=[],
    )
    with pytest.raises(PhasingError) as excinfo:
        parse_request(req, settings)
    assert excinfo.value.category is FailureCategory.INVALID_INPUT


def test_fixture_clean_parses_to_four_fragments(settings):
    parsed = parse_request(request_from_fixture("clean_two_site.json"), settings)
    assert len(parsed.variants) == 2
    assert len(parsed.fragments) == 4
    assert parsed.unknown_allele_observations == 0
