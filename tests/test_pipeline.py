"""End-to-end pipeline behaviour on the synthetic fixtures."""

import dataclasses

import pytest

from app.errors import FailureCategory, PhasingError
from app.models import PhaseRequest
from app.parsing import parse_request
from app.phasing.pipeline import run_phasing

from conftest import load_fixture, request_from_fixture


def _run(name, settings):
    parsed = parse_request(request_from_fixture(name), settings)
    return run_phasing(parsed, settings)


def test_clean_two_site_phase(settings):
    result = _run("clean_two_site.json", settings)
    assert result["status"] == "OK"
    assert result["uncertainties"] == []
    assert result["summary"]["num_blocks"] == 1
    block = result["blocks"][0]
    assert block["variant_ids"] == ["v1", "v2"]
    # Canonical orientation: H1 carries the reference allele at site 1.
    assert block["haplotypes"] == {"H1": ["A", "C"], "H2": ["G", "T"]}
    assert block["mec_score"] == 0.0
    assert block["num_optimal_solutions"] == 1
    assert block["ambiguous"] is False
    per_site = {s["variant_id"]: s for s in block["evidence"]["per_site"]}
    assert per_site["v1"]["ref_support_weight"] == 60.0
    assert per_site["v1"]["alt_support_weight"] == 60.0
    assert per_site["v1"]["correction_count"] == 0


def test_error_read_corrected_with_fixed_cost(settings):
    result = _run("error_read.json", settings)
    assert result["status"] == "OK"
    block = result["blocks"][0]
    # One q10 observation contradicts the winning phase: MEC == its quality.
    assert block["mec_score"] == 10.0
    assert block["haplotypes"] == {"H1": ["A", "C"], "H2": ["G", "T"]}
    assert block["evidence"]["corrected_observations"] == 1
    per_site = {s["variant_id"]: s for s in block["evidence"]["per_site"]}
    assert per_site["v2"]["correction_weight"] == 10.0
    assert per_site["v1"]["correction_count"] == 0
    # The erroneous fragment is equally close to both haplotypes: the tie is
    # surfaced, not hidden.
    assert block["evidence"]["tied_assignments"] == 1
    assert any("equally close" in u for u in result["uncertainties"])


def test_conflicting_support_yields_multiple_optima(settings):
    result = _run("ambiguous.json", settings)
    assert result["status"] == "AMBIGUOUS"
    block = result["blocks"][0]
    # Hand-computed: both (A,C)/(G,T) and (A,T)/(G,C) cost 30.
    assert block["mec_score"] == 30.0
    assert block["num_optimal_solutions"] == 2
    assert block["ambiguous"] is True
    assert block["haplotypes"] == {"H1": ["A", "C"], "H2": ["G", "T"]}
    assert block["alternative_solutions"] == [
        {"haplotype_1": ["A", "T"], "haplotype_2": ["G", "C"], "mec_score": 30.0}
    ]
    assert any("not uniquely determined" in u for u in result["uncertainties"])


def test_disconnected_blocks_phased_independently(settings):
    result = _run("disconnected.json", settings)
    assert result["status"] == "OK"
    assert result["summary"]["num_blocks"] == 2
    first, second = result["blocks"]
    assert first["variant_ids"] == ["v1", "v2"]
    assert second["variant_ids"] == ["v3", "v4"]
    assert first["haplotypes"] == {"H1": ["A", "C"], "H2": ["G", "T"]}
    assert second["haplotypes"] == {"H1": ["G", "T"], "H2": ["A", "C"]}
    # No cross-block phase is ever asserted.
    for block in result["blocks"]:
        assert "no phase relation to other blocks" in block["phase_note"]


def test_unknown_allele_excluded_but_reported(settings):
    result = _run("unknown_allele.json", settings)
    assert result["status"] == "OK"
    assert result["summary"]["unknown_allele_observations"] == 1
    assert any("neither ref nor alt" in u for u in result["uncertainties"])
    block = result["blocks"][0]
    assert block["haplotypes"] == {"H1": ["A", "C"], "H2": ["G", "T"]}
    assert block["mec_score"] == 0.0


def test_no_observations_fails_with_category(settings):
    req = PhaseRequest(
        variants=[{"id": "v1", "pos": 1, "ref": "A", "alt": "G"}],
        reads=[],
    )
    parsed = parse_request(req, settings)
    with pytest.raises(PhasingError) as excinfo:
        run_phasing(parsed, settings)
    assert excinfo.value.category is FailureCategory.NO_OBSERVATIONS


def test_oversized_block_fails_with_category(settings):
    oversized = PhaseRequest(
        variants=[
            {"id": f"v{i}", "pos": i + 1, "ref": "A", "alt": "G"} for i in range(4)
        ],
        reads=[
            {"read_id": "r1", "variant_id": f"v{i}", "allele": "A", "quality": 30}
            for i in range(4)
        ],
    )
    small_settings = dataclasses.replace(settings, max_enum_sites=3)
    parsed = parse_request(oversized, small_settings)
    with pytest.raises(PhasingError) as excinfo:
        run_phasing(parsed, small_settings)
    assert excinfo.value.category is FailureCategory.BLOCK_TOO_LARGE


def test_phase_flip_is_canonicalized(settings):
    """Swapping ref/alt labels must yield the same canonical bit pattern."""
    original = load_fixture("clean_two_site.json")
    swapped = {
        "sample": "synth-clean-swapped",
        "variants": [
            {**v, "ref": v["alt"], "alt": v["ref"]} for v in original["variants"]
        ],
        "reads": [
            {
                **r,
                "allele": next(
                    v["alt"] if r["allele"] == v["ref"] else v["ref"]
                    for v in original["variants"]
                    if v["id"] == r["variant_id"]
                ),
            }
            for r in original["reads"]
        ],
    }
    result_a = _run("clean_two_site.json", settings)
    parsed_b = parse_request(PhaseRequest(**swapped), settings)
    result_b = run_phasing(parsed_b, settings)

    block_a = result_a["blocks"][0]
    block_b = result_b["blocks"][0]
    # Same canonical orientation in both labelings: H1 starts with the ref allele.
    assert block_a["haplotypes"]["H1"] == ["A", "C"]
    assert block_b["haplotypes"]["H1"] == ["G", "T"]  # swapped refs
    assert block_a["mec_score"] == block_b["mec_score"]
    assert result_a["status"] == result_b["status"] == "OK"


def test_uncovered_variant_listed_as_uncertain(settings):
    req = PhaseRequest(
        variants=[
            {"id": "v1", "pos": 1, "ref": "A", "alt": "G"},
            {"id": "v2", "pos": 2, "ref": "C", "alt": "T"},
        ],
        reads=[{"read_id": "r1", "variant_id": "v1", "allele": "A", "quality": 30}],
    )
    parsed = parse_request(req, settings)
    result = run_phasing(parsed, settings)
    assert result["summary"]["num_blocks"] == 2
    assert any("v2" in u and "no covering reads" in u for u in result["uncertainties"])
