"""Phasing-core tests.

Expected values in the fixture-driven tests are hand-computed (see
docs/expected_results.md for the arithmetic), not produced by the
implementation under test.  The randomized test cross-checks the NumPy
core against an independent pure-Python brute-force MEC oracle written
for this test file only.
"""

import itertools
import random

import pytest

from app.errors import FailureCategory, ProcessingError
from app.models import PhaseRequest
from app.parsing import normalize
from app.phasing import (
    canonical_orientation,
    find_blocks,
    phase,
    result_to_dict,
)

from conftest import load_fixture

MAX_Q = 60
MAX_ENUM = 20


def run_fixture(name: str) -> dict:
    request = PhaseRequest(**load_fixture(name))
    dataset = normalize(request, max_quality=MAX_Q)
    return result_to_dict(phase(dataset, max_enum_sites=MAX_ENUM))


# --------------------------------------------------------------------------
# basic_clean: 3 sites, perfect reads, true haplotypes A-C-G / G-T-A
# --------------------------------------------------------------------------

def test_basic_clean_recovers_reference_haplotypes():
    result = run_fixture("basic_clean")
    assert result["n_blocks"] == 1
    block = result["blocks"][0]
    assert block["site_ids"] == ["s1", "s2", "s3"]
    # Hand-computed: the unique MEC=0 solution is 000/111, i.e. A-C-G / G-T-A.
    assert block["haplotype1"] == ["A", "C", "G"]
    assert block["haplotype2"] == ["G", "T", "A"]
    assert block["mec"] == 0
    assert block["ambiguous"] is False
    assert block["n_optima"] == 1
    assert block["supporting_reads"] == 6
    assert block["conflicting_reads"] == 0
    assert result["total_mec"] == 0
    assert result["uncertainties"] == []


# --------------------------------------------------------------------------
# error_reads: one read with a single miscalled base (s1, qual 25)
# --------------------------------------------------------------------------

def test_error_read_yields_mec_equal_to_error_qual():
    result = run_fixture("error_reads")
    assert result["n_blocks"] == 1
    block = result["blocks"][0]
    # Hand-computed: the error read costs min(25, 30+30) = 25 against the
    # true phase; every other candidate phase costs >= 30.
    assert block["mec"] == 25
    assert block["haplotype1"] == ["A", "C", "G"]
    assert block["haplotype2"] == ["G", "T", "A"]
    assert block["ambiguous"] is False
    assert block["supporting_reads"] == 4
    assert block["conflicting_reads"] == 1

    err = next(a for a in block["read_assignments"] if a["read_id"] == "r_err")
    assert err["assigned_haplotype"] == 1
    assert err["correction_cost"] == 25
    assert err["corrections"] == [
        {"site_id": "s1", "read_allele": "G", "phased_allele": "A", "qual": 25}
    ]


# --------------------------------------------------------------------------
# ambiguous: equal evidence for coupling (A-C/G-T) and repulsion (A-T/G-C)
# --------------------------------------------------------------------------

def test_ambiguous_block_reports_all_optima():
    result = run_fixture("ambiguous")
    assert result["n_blocks"] == 1
    block = result["blocks"][0]
    # Hand-computed: both 00 and 01 (first bit pinned) give MEC = 60.
    assert block["mec"] == 60
    assert block["ambiguous"] is True
    assert block["n_optima"] == 2
    assert block["haplotype1"] == ["A", "C"]
    assert block["haplotype2"] == ["G", "T"]
    assert block["alternative_optima"] == [
        {"haplotype1": ["A", "T"], "haplotype2": ["G", "C"]}
    ]
    assert any("2 MEC-optimal phases" in u for u in result["uncertainties"])


# --------------------------------------------------------------------------
# disconnected: three blocks; no cross-block phase may be claimed
# --------------------------------------------------------------------------

def test_disconnected_blocks_phased_independently():
    result = run_fixture("disconnected")
    assert result["n_blocks"] == 3

    b0, b1, b2 = result["blocks"]
    assert b0["site_ids"] == ["s1", "s2"]
    assert b0["haplotype1"] == ["A", "C"]
    assert b0["haplotype2"] == ["G", "T"]
    assert b0["mec"] == 0

    # Block 1's reads support G-A / A-C equally flipped; canonical
    # orientation pins the first site to its ref allele.
    assert b1["site_ids"] == ["s3", "s4"]
    assert b1["haplotype1"] == ["G", "T"]
    assert b1["haplotype2"] == ["A", "C"]
    assert b1["mec"] == 0

    assert b2["site_ids"] == ["s5"]
    assert b2["is_singleton"] is True

    # Each block lists only its own sites: no fabricated cross-block phase.
    for block in result["blocks"]:
        assert set(block["site_ids"]) in (
            {"s1", "s2"}, {"s3", "s4"}, {"s5"},
        )
    assert any("cannot be phased" in u for u in result["uncertainties"])
    assert result["total_mec"] == 0


# --------------------------------------------------------------------------
# Flip equivalence
# --------------------------------------------------------------------------

def test_canonical_orientation_collapses_global_flip():
    assert canonical_orientation((1, 0, 1)) == (0, 1, 0)
    assert canonical_orientation((0, 1, 1)) == (0, 1, 1)
    assert canonical_orientation((1, 1, 1)) == (0, 0, 0)
    assert canonical_orientation((0,)) == (0,)


def test_flipped_read_evidence_yields_same_canonical_phase():
    # Reads that support h=(alt, alt) on two sites must come back with the
    # first site on the ref allele after canonicalization.
    payload = {
        "sample_id": "flip-test",
        "sites": [
            {"id": "s1", "chrom": "c1", "position": 1, "ref": "A", "alt": "G"},
            {"id": "s2", "chrom": "c1", "position": 2, "ref": "C", "alt": "T"},
        ],
        "reads": [
            {"id": "r1", "calls": [
                {"site": "s1", "allele": "alt", "qual": 30},
                {"site": "s2", "allele": "alt", "qual": 30},
            ]},
            {"id": "r2", "calls": [
                {"site": "s1", "allele": "ref", "qual": 30},
                {"site": "s2", "allele": "ref", "qual": 30},
            ]},
        ],
    }
    dataset = normalize(PhaseRequest(**payload), max_quality=MAX_Q)
    result = result_to_dict(phase(dataset, max_enum_sites=MAX_ENUM))
    block = result["blocks"][0]
    assert block["ambiguous"] is False
    assert block["haplotype1"] == ["A", "C"]  # canonical: first site = ref
    assert block["haplotype2"] == ["G", "T"]


# --------------------------------------------------------------------------
# Processing failures
# --------------------------------------------------------------------------

def test_block_above_enumeration_limit_is_refused():
    sites = [
        {"id": f"s{i}", "chrom": "c1", "position": i, "ref": "A", "alt": "G"}
        for i in range(3)
    ]
    reads = [
        {"id": "r1", "calls": [
            {"site": f"s{i}", "allele": "ref", "qual": 30} for i in range(3)
        ]},
    ]
    dataset = normalize(
        PhaseRequest(sample_id="t", sites=sites, reads=reads), max_quality=MAX_Q
    )
    with pytest.raises(ProcessingError) as excinfo:
        phase(dataset, max_enum_sites=2)
    assert excinfo.value.category is FailureCategory.BLOCK_TOO_LARGE


def test_no_informative_reads_is_refused():
    payload = {
        "sample_id": "t",
        "sites": [
            {"id": "s1", "chrom": "c1", "position": 1, "ref": "A", "alt": "G"},
        ],
        "reads": [
            {"id": "r1", "calls": [{"site": "s1", "allele": "unknown", "qual": 0}]},
        ],
    }
    dataset = normalize(PhaseRequest(**payload), max_quality=MAX_Q)
    with pytest.raises(ProcessingError) as excinfo:
        phase(dataset, max_enum_sites=MAX_ENUM)
    assert excinfo.value.category is FailureCategory.NO_INFORMATIVE_READS


def test_find_blocks_co_coverage():
    import numpy as np

    observed = np.array([
        [True, True, False, False],
        [False, False, True, False],
        [False, False, False, False],
    ])
    assert find_blocks(observed) == [[0, 1], [2], [3]]


# --------------------------------------------------------------------------
# Independent oracle: pure-Python brute force over all 2^k orientations
# --------------------------------------------------------------------------

def _oracle_mec(reads, site_list):
    """Return (min_mec, set of canonical optimal bit vectors) for one block.

    `reads` is a list of dicts {site_index: (allele_bit, qual)}; `site_list`
    the block's site indices.  Written independently of app.phasing
    (itertools brute force, no NumPy).
    """
    k = len(site_list)

    def canonical(bits):
        return bits if bits[0] == 0 else tuple(1 - b for b in bits)

    best = None
    optima = set()
    for h1 in itertools.product((0, 1), repeat=k):
        total = 0
        for read in reads:
            cost1 = sum(q for j, (a, q) in read.items()
                        if j in site_list and a != h1[site_list.index(j)])
            cost2 = sum(q for j, (a, q) in read.items()
                        if j in site_list and a == h1[site_list.index(j)])
            total += min(cost1, cost2)
        if best is None or total < best:
            best, optima = total, {canonical(h1)}
        elif total == best:
            optima.add(canonical(h1))
    return best, optima


def _oracle_blocks(reads, k):
    """Connected components of the co-coverage graph (independent BFS)."""
    adjacency = [set() for _ in range(k)]
    for read in reads:
        sites = sorted(read)
        for a, b in itertools.combinations(sites, 2):
            adjacency[a].add(b)
            adjacency[b].add(a)
    blocks = []
    unseen = set(range(k))
    while unseen:
        seed = min(unseen)
        component, stack = [], [seed]
        unseen.discard(seed)
        while stack:
            node = stack.pop()
            component.append(node)
            for nb in adjacency[node]:
                if nb in unseen:
                    unseen.discard(nb)
                    stack.append(nb)
        blocks.append(sorted(component))
    return sorted(blocks, key=min)


def test_core_matches_independent_oracle_on_random_instances():
    rng = random.Random(20261004)
    for trial in range(30):
        k = rng.randint(2, 5)
        n_reads = rng.randint(1, 8)
        sites = [
            {"id": f"s{j}", "chrom": "c1", "position": j, "ref": "A", "alt": "G"}
            for j in range(k)
        ]
        reads_payload = []
        oracle_reads = []
        for i in range(n_reads):
            calls = []
            oracle_read = {}
            for j in range(k):
                if rng.random() < 0.8:
                    bit = rng.randint(0, 1)
                    qual = rng.randint(10, 40)
                    calls.append({
                        "site": f"s{j}",
                        "allele": "ref" if bit == 0 else "alt",
                        "qual": qual,
                    })
                    oracle_read[j] = (bit, qual)
            if not calls:  # keep every read informative
                calls.append({"site": "s0", "allele": "ref", "qual": 30})
                oracle_read[0] = (0, 30)
            reads_payload.append({"id": f"r{i}", "calls": calls})
            oracle_reads.append(oracle_read)

        dataset = normalize(
            PhaseRequest(sample_id=f"t{trial}", sites=sites, reads=reads_payload),
            max_quality=MAX_Q,
        )
        result = phase(dataset, max_enum_sites=MAX_ENUM)

        oracle_blocks = _oracle_blocks(oracle_reads, k)
        assert len(result.blocks) == len(oracle_blocks), f"trial {trial}"
        for block, oracle_sites in zip(result.blocks, oracle_blocks):
            assert block.site_ids == [f"s{j}" for j in oracle_sites], f"trial {trial}"
            expected_mec, expected_optima = _oracle_mec(oracle_reads, oracle_sites)
            assert block.mec == expected_mec, f"trial {trial}"
            assert block.n_optima == len(expected_optima), f"trial {trial}"
            # Representative haplotype must be one of the oracle's optima.
            rep_bits = tuple(0 if b == "A" else 1 for b in block.haplotype1)
            assert rep_bits in expected_optima, f"trial {trial}"
            assert block.ambiguous == (len(expected_optima) > 1)
