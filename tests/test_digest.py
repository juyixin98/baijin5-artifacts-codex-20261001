"""Unit tests: cleavage sites, blocking, missed cleavages, provenance.

Every case asserts concrete, hand-derived boundaries and bond indices - not
mere "the function ran".
"""

from __future__ import annotations

import pytest

from app.domain.constants import ENZYMES, MODIFICATIONS, EnzymeRule
from app.services.digest import digest, evaluate_sites
from app.services.parse import parse_sequence

pytestmark = pytest.mark.unit


def _bounds(result) -> list[tuple[int, int, str, int]]:
    return [
        (f.start, f.end, f.sequence, f.missed_cleavages) for f in result.fragments
    ]


def test_trypsin_basic_boundaries() -> None:
    parsed = parse_sequence("AAKAAAKAA", max_length=100)
    result = digest(parsed, ENZYMES["trypsin"], missed_cleavages=0)
    assert _bounds(result) == [
        (0, 3, "AAK", 0),
        (3, 7, "AAAK", 0),
        (7, 9, "AA", 0),
    ]


def test_trypsin_missed_cleavages_enumerate_every_span_once() -> None:
    parsed = parse_sequence("AAKAAAKAA", max_length=100)
    result = digest(parsed, ENZYMES["trypsin"], missed_cleavages=1)
    assert _bounds(result) == [
        (0, 3, "AAK", 0),
        (0, 7, "AAKAAAK", 1),
        (3, 7, "AAAK", 0),
        (3, 9, "AAAKAA", 1),
        (7, 9, "AA", 0),
    ]


def test_missed_cleavages_above_site_count_caps_at_whole_protein() -> None:
    parsed = parse_sequence("AKA", max_length=100)
    result = digest(parsed, ENZYMES["trypsin"], missed_cleavages=5)
    # 2 fully-digested segments => at most one 1-missed span; no phantom forms
    assert _bounds(result) == [
        (0, 2, "AK", 0),
        (0, 3, "AKA", 1),
        (2, 3, "A", 0),
    ]


def test_consecutive_cleavage_sites() -> None:
    parsed = parse_sequence("AKKA", max_length=100)
    result = digest(parsed, ENZYMES["trypsin"], missed_cleavages=0)
    assert _bounds(result) == [
        (0, 2, "AK", 0),
        (2, 3, "K", 0),
        (3, 4, "A", 0),
    ]
    assert [s.bond for s in result.cleavage_sites] == [2, 3]


def test_trypsin_blocked_by_proline_is_recorded_as_blocked() -> None:
    parsed = parse_sequence("AKPAA", max_length=100)
    result = digest(parsed, ENZYMES["trypsin"], missed_cleavages=0)
    assert len(result.blocked_sites) == 1
    blocked = result.blocked_sites[0]
    assert blocked.bond == 2
    assert blocked.blocking_residue == "P"
    assert blocked.blocking_residue_position == 3
    assert _bounds(result) == [(0, 5, "AKPAA", 0)]
    assert not result.cleavage_sites


def test_proline_block_does_not_block_other_bonds() -> None:
    parsed = parse_sequence("AKPAK", max_length=100)
    result = digest(parsed, ENZYMES["trypsin"], missed_cleavages=0)
    # bond 2 (K-P) blocked; bond 5 is C-anchor; bond... sequence: A K P A K
    assert [s.bond for s in result.cleavage_sites] == []
    assert [s.bond for s in result.blocked_sites] == [2]


def test_asp_n_nterminal_d_emits_explicit_empty_n_segment() -> None:
    parsed = parse_sequence("DAAD", max_length=100)
    result = digest(parsed, ENZYMES["asp_n"], missed_cleavages=0)
    empty = result.fragments[0]
    assert empty.empty and empty.start == 0 and empty.end == 0
    assert empty.n_terminal and not empty.c_terminal
    assert empty.mass.nominal == 0.0
    assert _bounds(result) == [
        (0, 0, "", 0),
        (0, 3, "DAA", 0),
        (3, 4, "D", 0),
    ]


def test_cterminal_rule_residue_emits_explicit_empty_c_segment() -> None:
    parsed = parse_sequence("AK", max_length=100)
    result = digest(parsed, ENZYMES["trypsin"], missed_cleavages=0)
    empty = result.fragments[-1]
    assert empty.empty and empty.start == 2 and empty.end == 2
    assert empty.c_terminal and not empty.n_terminal


def test_identical_sequences_keep_distinct_positions() -> None:
    parsed = parse_sequence("AMAM", max_length=100)
    result = digest(parsed, ENZYMES["cnbr"], missed_cleavages=0)
    am_fragments = [f for f in result.fragments if f.sequence == "AM"]
    assert len(am_fragments) == 2
    assert am_fragments[0].start == 0 and am_fragments[0].n_terminal
    assert am_fragments[1].start == 2 and am_fragments[1].c_terminal
    # Source identities differ even though strings match.
    assert am_fragments[0].identity() != am_fragments[1].identity()


def test_fragment_positions_are_absolute_parent_coordinates() -> None:
    parsed = parse_sequence("AAAKAAAK", max_length=100)
    result = digest(parsed, ENZYMES["trypsin"], missed_cleavages=1)
    merged = next(f for f in result.fragments if f.sequence == "AAAKAAAK")
    assert (merged.start, merged.end) == (0, 8)
    assert merged.internal_cleavage_bonds == (4,)
    assert merged.missed_cleavages == 1


def test_no_cleavage_sites_returns_whole_terminal_fragment() -> None:
    parsed = parse_sequence("AAAA", max_length=100)
    result = digest(parsed, ENZYMES["trypsin"], missed_cleavages=0)
    only = result.fragments[0]
    assert only.sequence == "AAAA"
    assert only.n_terminal and only.c_terminal
    assert only.cleavage_before is None and only.cleavage_after is None


def test_custom_rule_with_blocking_context() -> None:
    rule = EnzymeRule(
        key="custom",
        name="after-A-block-P",
        cleave_after=frozenset({"A"}),
        cterm_block=frozenset({"P"}),
    )
    parsed = parse_sequence("APAA", max_length=100)
    result = digest(parsed, rule, missed_cleavages=0)
    # bond1 A-P blocked; bond3 A-A cuts
    assert [s.bond for s in result.blocked_sites] == [1]
    assert [s.bond for s in result.cleavage_sites] == [3]


def test_evaluate_sites_reports_rule_residue_position() -> None:
    eligible, blocked = evaluate_sites(ENZYMES["trypsin"], "AKP")
    assert len(blocked) == 1
    assert blocked[0].terminal_residue == "K"
    assert blocked[0].terminal_residue_position == 2


def test_pepsin_before_rule_nside_proline_block() -> None:
    # cleave before F/L/W/Y; a preceding P blocks. "PF": before-F bond has P
    # on its N side -> blocked, no cut.
    parsed = parse_sequence("PF", max_length=100)
    result = digest(parsed, ENZYMES["pepsin"], missed_cleavages=0)
    assert [s.bond for s in result.blocked_sites] == [1]
    assert not result.cleavage_sites
    assert result.fragments[0].sequence == "PF"

    # Without the blocking P, pepsin cuts before F: "AF" -> bond 1 cuts.
    parsed2 = parse_sequence("AF", max_length=100)
    result2 = digest(parsed2, ENZYMES["pepsin"], missed_cleavages=0)
    assert [s.bond for s in result2.cleavage_sites] == [1]
    assert [f.sequence for f in result2.fragments if not f.empty] == ["A", "F"]


def test_fixed_modification_applies_to_every_target() -> None:
    parsed = parse_sequence("CC", max_length=100)
    carb = MODIFICATIONS["carbamidomethyl_c"]
    result = digest(parsed, ENZYMES["cnbr"], missed_cleavages=0, fixed_mods=[carb])
    forms = result.fragments[0].modification_forms
    # Fixed only => exactly one form with two C placements.
    assert len(forms) == 1
    assert len(forms[0].placements) == 2
    assert forms[0].delta == pytest.approx(2 * carb.delta, abs=1e-6)


def test_variable_modification_enumerates_subsets() -> None:
    parsed = parse_sequence("MM", max_length=100)
    ox = MODIFICATIONS["oxidation_m"]
    result = digest(
        parsed, ENZYMES["chymotrypsin"], missed_cleavages=0, variable_mods=[ox]
    )
    forms = result.fragments[0].modification_forms
    # 2 methionines => 2^2 = 4 forms: none, M1, M2, both
    assert len(forms) == 4
    deltas = sorted(round(f.delta, 6) for f in forms)
    assert deltas == sorted(
        [0.0, round(ox.delta, 6), round(ox.delta, 6), round(2 * ox.delta, 6)]
    )
    # Placements carry absolute parent positions.
    both = max(forms, key=lambda f: f.delta)
    assert sorted(p[0] for p in both.placements) == [1, 2]
