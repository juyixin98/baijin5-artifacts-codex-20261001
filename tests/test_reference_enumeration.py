"""Reference validation: enumerate true haplotype pairs and check recovery.

The reference answers here are the *enumerated ground-truth haplotype
pairs* themselves. Reads are synthesized by an independent generator living
in this test file (not by the phasing implementation), so the core cannot
pass by agreeing with itself.

Guarantee used: when every adjacent site pair is observed from both
haplotypes, the true pair is the unique MEC-0 solution (a recombinant
candidate would need a switch-point pair matching neither haplotype, which
is impossible for complementary haplotypes).
"""

import itertools

import pytest

from app.models import PhaseRequest
from app.parsing import parse_request
from app.phasing.pipeline import run_phasing

BASES = ["A", "C", "G", "T"]


def _variants(n):
    return [
        {"id": f"v{i}", "pos": 10 * (i + 1), "ref": "A", "alt": "G"}
        for i in range(n)
    ]


def _allele(bit):
    return "A" if bit == 0 else "G"


def synthesize_reads(truth_h1, quality=30, reps=2):
    """Independent read generator: for each adjacent site pair, emit `reps`
    fragments from each of the two true haplotypes. No phasing code used."""
    n = len(truth_h1)
    truth_h2 = [1 - b for b in truth_h1]
    reads = []
    counter = itertools.count(1)
    for i in range(n - 1):
        for hap in (truth_h1, truth_h2):
            for _ in range(reps):
                read_id = f"read{next(counter)}"
                for j in (i, i + 1):
                    reads.append({
                        "read_id": read_id,
                        "variant_id": f"v{j}",
                        "allele": _allele(hap[j]),
                        "quality": quality,
                    })
    return reads


def _bits_of(hap_alleles):
    return [0 if a == "A" else 1 for a in hap_alleles]


@pytest.mark.parametrize("n", [2, 3, 4])
def test_all_enumerated_haplotype_pairs_recovered(settings, n):
    # Canonical truths: h1[0] == 0, h2 = complement -> 2**(n-1) pairs.
    for code in range(2 ** (n - 1)):
        truth = [0] + [(code >> k) & 1 for k in range(n - 2, -1, -1)]
        request = PhaseRequest(
            variants=_variants(n),
            reads=synthesize_reads(truth),
        )
        parsed = parse_request(request, settings)
        result = run_phasing(parsed, settings)

        assert result["status"] == "OK", f"truth {truth}: {result['uncertainties']}"
        assert result["summary"]["num_blocks"] == 1
        block = result["blocks"][0]
        assert block["mec_score"] == 0.0
        assert block["num_optimal_solutions"] == 1
        recovered = _bits_of(block["haplotypes"]["H1"])
        assert recovered == truth, f"truth {truth} -> recovered {recovered}"
        recovered_h2 = _bits_of(block["haplotypes"]["H2"])
        assert recovered_h2 == [1 - b for b in truth]


def test_low_quality_error_read_does_not_break_recovery(settings):
    truth = [0, 1, 0]
    reads = synthesize_reads(truth, quality=30, reps=2)
    # One erroneous q5 fragment: claims (1,1) over sites v0,v1 where the
    # truth supports (0,1) or (1,0). Cost 5 < 30, so MEC corrects it.
    reads.append({"read_id": "err1", "variant_id": "v0", "allele": "G", "quality": 5})
    reads.append({"read_id": "err1", "variant_id": "v1", "allele": "G", "quality": 5})
    request = PhaseRequest(variants=_variants(3), reads=reads)
    parsed = parse_request(request, settings)
    result = run_phasing(parsed, settings)

    block = result["blocks"][0]
    assert _bits_of(block["haplotypes"]["H1"]) == truth
    assert block["mec_score"] == 5.0
    assert block["evidence"]["corrected_observations"] == 1


def test_unlinked_tail_sites_form_second_block(settings):
    truth = [0, 1, 0]
    reads = synthesize_reads(truth)
    # A separate pair of sites, covered only among themselves.
    variants = _variants(3) + [
        {"id": "w0", "pos": 900, "ref": "C", "alt": "T"},
        {"id": "w1", "pos": 910, "ref": "C", "alt": "T"},
    ]
    for read_id, alleles in (("x1", ("C", "C")), ("x2", ("T", "T"))):
        for variant_id, allele in zip(("w0", "w1"), alleles):
            reads.append({
                "read_id": read_id, "variant_id": variant_id,
                "allele": allele, "quality": 30,
            })
    request = PhaseRequest(variants=variants, reads=reads)
    parsed = parse_request(request, settings)
    result = run_phasing(parsed, settings)

    assert result["summary"]["num_blocks"] == 2
    main, tail = result["blocks"]
    assert _bits_of(main["haplotypes"]["H1"]) == truth
    assert tail["variant_ids"] == ["w0", "w1"]
    assert tail["haplotypes"] == {"H1": ["C", "C"], "H2": ["T", "T"]}
