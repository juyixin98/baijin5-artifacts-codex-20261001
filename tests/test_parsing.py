"""Validation tests: every rejection must carry the right failure category."""

import pytest

from app.errors import DatasetError, FailureCategory
from app.models import PhaseRequest
from app.parsing import normalize

MAX_Q = 60


def make_request(**overrides) -> PhaseRequest:
    payload = {
        "sample_id": "t",
        "sites": [
            {"id": "s1", "chrom": "c1", "position": 10, "ref": "A", "alt": "G"},
            {"id": "s2", "chrom": "c1", "position": 20, "ref": "C", "alt": "T"},
        ],
        "reads": [
            {"id": "r1", "calls": [
                {"site": "s1", "allele": "ref", "qual": 30},
                {"site": "s2", "allele": "ref", "qual": 30},
            ]},
        ],
    }
    payload.update(overrides)
    return PhaseRequest(**payload)


def expect_error(request, category):
    with pytest.raises(DatasetError) as excinfo:
        normalize(request, max_quality=MAX_Q)
    assert excinfo.value.category is category
    return excinfo.value


def test_valid_dataset_normalizes():
    ds = normalize(make_request(), max_quality=MAX_Q)
    assert ds.n_sites == 2
    assert ds.n_reads == 1
    assert ds.alleles.tolist() == [[0, 0]]
    assert ds.quals.tolist() == [[30, 30]]
    assert ds.observed.tolist() == [[True, True]]


def test_unknown_reference_allele_rejected():
    req = make_request(sites=[
        {"id": "s1", "chrom": "c1", "position": 10, "ref": "N", "alt": "G"},
        {"id": "s2", "chrom": "c1", "position": 20, "ref": "C", "alt": "T"},
    ])
    err = expect_error(req, FailureCategory.INVALID_REFERENCE_ALLELE)
    assert err.context["site_id"] == "s1"


def test_non_base_reference_allele_rejected():
    req = make_request(sites=[
        {"id": "s1", "chrom": "c1", "position": 10, "ref": "AA", "alt": "G"},
        {"id": "s2", "chrom": "c1", "position": 20, "ref": "C", "alt": "T"},
    ])
    expect_error(req, FailureCategory.INVALID_REFERENCE_ALLELE)


def test_alt_equal_to_ref_rejected():
    req = make_request(sites=[
        {"id": "s1", "chrom": "c1", "position": 10, "ref": "A", "alt": "A"},
        {"id": "s2", "chrom": "c1", "position": 20, "ref": "C", "alt": "T"},
    ])
    expect_error(req, FailureCategory.INVALID_ALT_ALLELE)


def test_alt_not_a_base_rejected():
    req = make_request(sites=[
        {"id": "s1", "chrom": "c1", "position": 10, "ref": "A", "alt": "X"},
        {"id": "s2", "chrom": "c1", "position": 20, "ref": "C", "alt": "T"},
    ])
    expect_error(req, FailureCategory.INVALID_ALT_ALLELE)


def test_duplicate_site_id_rejected():
    req = make_request(sites=[
        {"id": "s1", "chrom": "c1", "position": 10, "ref": "A", "alt": "G"},
        {"id": "s1", "chrom": "c1", "position": 20, "ref": "C", "alt": "T"},
    ])
    expect_error(req, FailureCategory.DUPLICATE_SITE_ID)


def test_read_referencing_unknown_site_rejected():
    req = make_request(reads=[
        {"id": "r1", "calls": [{"site": "sX", "allele": "ref", "qual": 30}]},
    ])
    err = expect_error(req, FailureCategory.READ_REFERENCES_UNKNOWN_SITE)
    assert err.context["site_id"] == "sX"


def test_quality_above_max_rejected():
    req = make_request(reads=[
        {"id": "r1", "calls": [{"site": "s1", "allele": "ref", "qual": 61}]},
    ])
    expect_error(req, FailureCategory.QUALITY_OUT_OF_RANGE)


def test_negative_quality_rejected():
    req = make_request(reads=[
        {"id": "r1", "calls": [{"site": "s1", "allele": "ref", "qual": -1}]},
    ])
    expect_error(req, FailureCategory.QUALITY_OUT_OF_RANGE)


def test_duplicate_read_id_rejected():
    req = make_request(reads=[
        {"id": "r1", "calls": [{"site": "s1", "allele": "ref", "qual": 30}]},
        {"id": "r1", "calls": [{"site": "s1", "allele": "alt", "qual": 30}]},
    ])
    expect_error(req, FailureCategory.DUPLICATE_READ_ID)


def test_duplicate_call_in_read_rejected():
    req = make_request(reads=[
        {"id": "r1", "calls": [
            {"site": "s1", "allele": "ref", "qual": 30},
            {"site": "s1", "allele": "alt", "qual": 30},
        ]},
    ])
    expect_error(req, FailureCategory.DUPLICATE_CALL_IN_READ)


def test_empty_dataset_rejected():
    # Bypass pydantic's min_length to exercise the domain-level guard.
    req = PhaseRequest.model_construct(sample_id="t", sites=[], reads=[])
    expect_error(req, FailureCategory.EMPTY_DATASET)


def test_unknown_allele_calls_are_ignored_but_counted():
    req = make_request(reads=[
        {"id": "r1", "calls": [
            {"site": "s1", "allele": "ref", "qual": 30},
            {"site": "s2", "allele": "unknown", "qual": 0},
        ]},
    ])
    ds = normalize(req, max_quality=MAX_Q)
    assert ds.n_unknown_calls == 1
    assert ds.alleles.tolist() == [[0, -1]]
    assert ds.observed.tolist() == [[True, False]]
