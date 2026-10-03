"""Exact-MEC solver against hand-computed reference answers.

Every expected value below was derived by hand from the weighted-MEC
definition, not produced by the implementation under test.
"""

from app.domain import Fragment, Observation
from app.phasing.matrix import build_matrix
from app.phasing.mec import solve_mec


def _fragment(read_id, obs):
    """obs: list of (site, bit, quality)."""
    return Fragment(
        read_id=read_id,
        observations=[Observation(site=s, bit=b, weight=float(q)) for s, b, q in obs],
    )


def _solve(fragments, num_sites):
    matrix = build_matrix(fragments, list(range(num_sites)))
    return solve_mec(matrix)


def test_clean_two_site_unique_optimum():
    # Fragments: 2x (0,0)@q30, 2x (1,1)@q30.
    # Hand check: h1=(0,0) gives MEC 0; h1=(0,1) gives each fragment one
    # mismatch -> 4 * 30 = 120. Unique optimum (0,0)/(1,1).
    fragments = [
        _fragment("r1", [(0, 0, 30), (1, 0, 30)]),
        _fragment("r2", [(0, 0, 30), (1, 0, 30)]),
        _fragment("r3", [(0, 1, 30), (1, 1, 30)]),
        _fragment("r4", [(0, 1, 30), (1, 1, 30)]),
    ]
    result = _solve(fragments, 2)
    assert result.best_score == 0.0
    assert len(result.optimal_haplotypes) == 1
    assert result.optimal_haplotypes[0].tolist() == [0, 0]


def test_error_read_costs_exactly_its_quality():
    # One q10 fragment disagrees with the majority phase at one site.
    # Hand check: h1=(0,0): clean fragments cost 0; the error fragment is
    # distance 10 from both (0,0) and (1,1) -> MEC 10. Any other candidate
    # misclassifies >= 1 clean fragment -> >= 30. Unique optimum, MEC 10.
    fragments = [
        _fragment("r1", [(0, 0, 30), (1, 0, 30)]),
        _fragment("r2", [(0, 0, 30), (1, 0, 30)]),
        _fragment("r3", [(0, 1, 30), (1, 1, 30)]),
        _fragment("r4", [(0, 1, 30), (1, 1, 30)]),
        _fragment("r5", [(0, 0, 10), (1, 1, 10)]),
    ]
    result = _solve(fragments, 2)
    assert result.best_score == 10.0
    assert len(result.optimal_haplotypes) == 1
    assert result.optimal_haplotypes[0].tolist() == [0, 0]
    # The error fragment is equally close to both haplotypes -> reported tie.
    assert result.tied_fragments == [4]
    assert result.correction_costs[4] == 10.0


def test_symmetric_evidence_yields_two_optima():
    # Fragments (0,0)@q30 and (0,1)@q30.
    # Hand check: h1=(0,0): min(0,60) + min(30,30) = 30.
    #             h1=(0,1): min(30,30) + min(0,60) = 30.  Tie -> ambiguous.
    fragments = [
        _fragment("r1", [(0, 0, 30), (1, 0, 30)]),
        _fragment("r2", [(0, 0, 30), (1, 1, 30)]),
    ]
    result = _solve(fragments, 2)
    assert result.best_score == 30.0
    assert sorted(h.tolist() for h in result.optimal_haplotypes) == [[0, 0], [0, 1]]


def test_three_site_hand_computed():
    # Truth H1=(0,1,0), H2=(1,0,1); every fragment consistent with H1.
    # f4 spans all three sites, so only (0,1,0) and its complement can reach
    # MEC 0, and canonicalization (h1[0]==0) keeps only (0,1,0).
    fragments = [
        _fragment("f1", [(0, 0, 30), (1, 1, 30)]),
        _fragment("f2", [(1, 1, 30), (2, 0, 30)]),
        _fragment("f3", [(0, 0, 30), (2, 0, 30)]),
        _fragment("f4", [(0, 0, 30), (1, 1, 30), (2, 0, 30)]),
    ]
    result = _solve(fragments, 3)
    assert result.best_score == 0.0
    assert len(result.optimal_haplotypes) == 1
    assert result.optimal_haplotypes[0].tolist() == [0, 1, 0]


def test_phase_flip_is_canonicalized():
    # Same physical evidence, bit labels swapped: the solver must return the
    # same canonical representative (h1[0] == 0) either way.
    fragments_a = [_fragment("r1", [(0, 0, 30), (1, 0, 30)])]
    fragments_b = [_fragment("r1", [(0, 1, 30), (1, 1, 30)])]
    result_a = _solve(fragments_a, 2)
    result_b = _solve(fragments_b, 2)
    assert result_a.optimal_haplotypes[0].tolist() == [0, 0]
    assert result_b.optimal_haplotypes[0].tolist() == [0, 0]
    assert result_a.best_score == result_b.best_score == 0.0


def test_quality_weights_change_the_winner():
    # Crossing evidence: f1=(0,0)@q40, f2=(1,1)@q10, f3=(0,1)@q30.
    # Hand check: h1=(0,0): min(0,80) + min(20,0) + min(30,30) = 30.
    #             h1=(0,1): min(40,40) + min(10,10) + min(0,60) = 50.
    # Winner (0,0) with MEC 30: the cheap q10 fragment gets corrected.
    fragments = [
        _fragment("f1", [(0, 0, 40), (1, 0, 40)]),
        _fragment("f2", [(0, 1, 10), (1, 1, 10)]),
        _fragment("f3", [(0, 0, 30), (1, 1, 30)]),
    ]
    result = _solve(fragments, 2)
    assert result.best_score == 30.0
    assert result.optimal_haplotypes[0].tolist() == [0, 0]
