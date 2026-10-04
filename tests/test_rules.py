"""Tests for cleavage rules: hand-computed cuts, blocks, terminal enzymes."""

from __future__ import annotations

import pytest

from app.domain.rules import BUILTIN_ENZYMES, Enzyme
from app.errors import InvalidRuleError


def enzyme(name: str) -> Enzyme:
    table = {e.name: e for e in BUILTIN_ENZYMES}
    return table[name]


def decisions(seq: str, enz: Enzyme) -> list[tuple[int, str, str, str]]:
    return [
        (d.bond, d.p1, d.p1_prime, d.decision)
        for d in (enz.evaluate_bond(seq, b) for b in range(1, len(seq)))
    ]


def test_trypsin_basic_cut_after_k():
    # AAK|AAA : bond 3 (K|A) must be CUT; all others NO_MATCH.
    got = decisions("AAKAAA", enzyme("trypsin_syn"))
    assert got == [
        (1, "A", "A", "NO_MATCH"),
        (2, "A", "K", "NO_MATCH"),
        (3, "K", "A", "CUT"),
        (4, "A", "A", "NO_MATCH"),
        (5, "A", "A", "NO_MATCH"),
    ]


def test_trypsin_proline_blocking_context():
    # K|P at bond 3 is BLOCKED with matched_rule not_before.
    d = enzyme("trypsin_syn").evaluate_bond("AAKPAA", 3)
    assert d.decision == "BLOCKED"
    assert d.matched_rule == "not_before"
    assert d.p1 == "K" and d.p1_prime == "P"


def test_consecutive_cut_sites_all_cut_and_no_empty_pieces():
    # KKK|A -> bonds 1,2,3 all CUT.
    got = [row[3] for row in decisions("KKKA", enzyme("trypsin_syn"))]
    assert got == ["CUT", "CUT", "CUT"]


def test_trypsin_cuts_both_k_and_r():
    seq = "AKARPA"
    # bonds: A|K no, K|A cut2, A|R no, R|P BLOCKED4, P|A no
    got = decisions(seq, enzyme("trypsin_syn"))
    assert [d for d in got if d[3] == "CUT"] == [(2, "K", "A", "CUT")]
    assert [d for d in got if d[3] == "BLOCKED"] == [(4, "R", "P", "BLOCKED")]


def test_no_proline_rule_enzyme_cuts_kp():
    d = enzyme("trypsin_no_proline_rule").evaluate_bond("AAKPAA", 3)
    assert d.decision == "CUT"


def test_terminal_enzymes_cut_only_at_boundary_bond():
    seq = "ACDEF"
    nterm = [d[3] for d in decisions(seq, enzyme("nterm_cut_syn"))]
    cterm = [d[3] for d in decisions(seq, enzyme("cterm_cut_syn"))]
    assert nterm == ["CUT", "NO_MATCH", "NO_MATCH", "NO_MATCH"]
    assert cterm == ["NO_MATCH", "NO_MATCH", "NO_MATCH", "CUT"]


def test_single_residue_has_no_bonds():
    assert decisions("K", enzyme("trypsin_syn")) == []


def test_enzyme_requires_a_rule():
    with pytest.raises(InvalidRuleError) as exc:
        Enzyme(name="broken")
    assert exc.value.code == "INVALID_RULE"


def test_enzyme_rejects_mixed_terminal_and_cut_after():
    with pytest.raises(InvalidRuleError):
        Enzyme(name="broken", cut_after=frozenset("K"), require_nterm=True)


def test_from_dict_parses_context_fields():
    enz = Enzyme.from_dict(
        {"name": "custom", "cut_after": "KR", "not_before": ["P"]}
    )
    assert enz.cut_after == frozenset("KR")
    assert enz.not_before == frozenset("P")
