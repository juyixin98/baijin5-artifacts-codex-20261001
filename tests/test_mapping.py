"""Core mapping: hand-computed points, boundaries, introns, fragmentation.

T1_PLUS (syn1, +): exons E0[100,130) E1[160,180) E2[210,240), mature 80.
    tx: g = 100 + p in E0; p=30 -> g=160 ... p=49 -> g=179; p=50 -> g=210.
T2_MINUS (syn2, -): exons E0[500,530) E1[560,585) E2[620,640), mature 75.
    tx order: E2 then E1 then E0, bases decreasing.
    p=0 -> g=639; p=19 -> g=620; p=20 -> g=584; p=44 -> g=560;
    p=45 -> g=529; p=74 -> g=500.
T3_ADJACENT (syn1, +): E0[40,50) E1[50,65), back-to-back.
"""

from __future__ import annotations

import numpy as np
import pytest

from txmap.errors import (
    CoordinateOutOfRangeError,
    IntronicPositionError,
    InvalidIntervalError,
    RegionNotMappableError,
)

pytestmark = pytest.mark.unit


# --------------------------------------------------------------- plus strand

def test_plus_hand_computed_points(m1):
    cases = {
        0: (100, 0),    # first base of transcript / E0
        29: (129, 0),   # last base of E0 (half-open: 130 excluded)
        30: (160, 1),   # first base of E1: intron skipped, not spliced over
        49: (179, 1),   # last base of E1
        50: (210, 2),   # first base of E2
        79: (239, 2),   # last mature base
    }
    for tx_pos, (g, exon) in cases.items():
        m = m1.tx_to_genomic_point(tx_pos)
        assert (m.genomic_position, m.exon_index) == (g, exon), tx_pos
        back = m1.genomic_to_tx_point(g)
        assert back.tx_position == tx_pos


def test_plus_exon_end_exclusive_on_genomic_side(m1):
    # 130, 180, 210 are intronic (first intron base / junction), NOT exonic.
    for g in (130, 131, 159, 180, 209):
        with pytest.raises(IntronicPositionError) as exc:
            m1.genomic_to_tx_point(g)
        assert exc.value.code == "intronic_position"
        assert "hard-snapped" in exc.value.detail
        assert exc.value.key_state["genomic_position"] == g


def test_plus_intronic_flanks_reported(m1):
    with pytest.raises(IntronicPositionError) as exc:
        m1.genomic_to_tx_point(145)  # middle of intron [130,160)
    state = exc.value.key_state
    assert state["prev_exon"] == [100, 130]
    assert state["next_exon"] == [160, 180]


def test_plus_interval_spanning_intron_splits_in_order(m1):
    # tx [25, 55) = 30 bases: tail E0(25..29)=5, all E1=20, head E2(50..54)=5
    result = m1.tx_to_genomic_interval(25, 55)
    assert result.length == 30
    assert result.fragment_count == 3
    assert result.mapped_length == 30  # length conserved, intron not inserted

    f = result.fragments
    assert (f[0].tx_start, f[0].tx_end, f[0].genomic_start, f[0].genomic_end) == (
        25, 30, 125, 130
    )
    assert (f[1].tx_start, f[1].tx_end, f[1].genomic_start, f[1].genomic_end) == (
        30, 50, 160, 180
    )
    assert (f[2].tx_start, f[2].tx_end, f[2].genomic_start, f[2].genomic_end) == (
        50, 55, 210, 215
    )
    # fragments are transcript-ascending; genomic_order reflects reference rank
    assert [fr.genomic_order for fr in f] == [0, 1, 2]
    # half-open lengths match on both sides of every fragment
    for fr in f:
        assert fr.length == fr.genomic_end - fr.genomic_start

    # round trip of the whole spliced region
    merged_back = [
        (fr.genomic_start, fr.genomic_end) for fr in f
    ]
    # reconstruct tx interval piecewise via genomic_to_tx on each exon piece
    tx_pieces = [m1.genomic_to_tx_interval(*g) for g in merged_back]
    flat = [(p.tx_start, p.tx_end) for p in tx_pieces]
    assert flat == [(25, 30), (30, 50), (50, 55)]


def test_plus_interval_inside_single_exon(m1):
    result = m1.tx_to_genomic_interval(32, 40)
    assert result.fragment_count == 1
    fr = result.fragments[0]
    assert (fr.genomic_start, fr.genomic_end) == (162, 170)
    assert fr.length == 8


def test_plus_exon_boundary_aligned_interval(m1):
    # exactly [29, 31) crosses the E0|intron|E1 junction at exon edges
    result = m1.tx_to_genomic_interval(29, 31)
    assert result.fragment_count == 2
    assert [
        (fr.tx_start, fr.tx_end, fr.genomic_start, fr.genomic_end)
        for fr in result.fragments
    ] == [(29, 30, 129, 130), (30, 31, 160, 161)]


def test_plus_genomic_interval_overlapping_intron_rejected(m1):
    # [125,165) covers 5 exon + 30 intron + 5 exon: must not hard-map as block
    with pytest.raises(RegionNotMappableError) as exc:
        m1.genomic_to_tx_interval(125, 165)
    assert exc.value.code == "region_not_mappable"
    state = exc.value.key_state
    assert state["requested"] == 40
    assert state["covered"] == 10
    assert state["intronic_gaps"] == [[130, 160]]


def test_plus_genomic_interval_purely_intronic_rejected(m1):
    with pytest.raises(RegionNotMappableError):
        m1.genomic_to_tx_interval(185, 200)


def test_plus_genomic_interval_single_exon_roundtrip(m1):
    result = m1.genomic_to_tx_interval(160, 180)
    assert result.tx_start == 30 and result.tx_end == 50
    assert result.fragment_count == 1


# -------------------------------------------------------------- minus strand

def test_minus_hand_computed_points(m2):
    cases = {
        0: (639, 2),
        19: (620, 2),
        20: (584, 1),
        44: (560, 1),
        45: (529, 0),
        74: (500, 0),
    }
    for tx_pos, (g, exon) in cases.items():
        m = m2.tx_to_genomic_point(tx_pos)
        assert (m.genomic_position, m.exon_index) == (g, exon), tx_pos
        assert m1_roundtrip(m2, tx_pos, g)


def m1_roundtrip(mapper, tx_pos, g):
    back = mapper.genomic_to_tx_point(g)
    return back.tx_position == tx_pos


def test_minus_intronic_not_snapped(m2):
    # intron [585,620): base 600 is 16 bases away from both sides of E2
    with pytest.raises(IntronicPositionError):
        m2.genomic_to_tx_point(600)
    # intron [530,560)
    with pytest.raises(IntronicPositionError):
        m2.genomic_to_tx_point(545)


def test_minus_interval_splits_with_descending_genome_ascending_tx(m2):
    # tx [15, 50): tail of E2 (p15..19 -> g624..620) = 5,
    # all E1 (p20..44 -> g584..560) = 25, head of E0 (p45..49 -> g529..525) = 5
    result = m2.tx_to_genomic_interval(15, 50)
    assert result.length == 35
    assert result.fragment_count == 3
    assert result.mapped_length == 35
    f = result.fragments
    assert [(x.tx_start, x.tx_end, x.genomic_start, x.genomic_end) for x in f] == [
        (15, 20, 620, 625),
        (20, 45, 560, 585),
        (45, 50, 525, 530),
    ]
    # genomic intervals descend as tx ascends on minus strand
    assert [f[0].genomic_start, f[1].genomic_start, f[2].genomic_start] == [620, 560, 525]
    assert [x.genomic_order for x in f] == [2, 1, 0]
    for fr in f:
        assert fr.length == fr.genomic_end - fr.genomic_start


def test_minus_genomic_to_tx_interval_inverts(m2):
    # genomic [525,530)+[560,585)+[620,625) is not one interval; map the
    # highest exonic piece alone and check tx orientation (p45..49 <-> g525..530)
    result = m2.genomic_to_tx_interval(525, 530)
    assert (result.tx_start, result.tx_end) == (45, 50)
    # the high-genomic exon piece maps to LOW tx positions
    result_high = m2.genomic_to_tx_interval(620, 625)
    assert (result_high.tx_start, result_high.tx_end) == (15, 20)
    # full E1 maps to p20..44
    assert (
        m2.genomic_to_tx_interval(560, 585).tx_start,
        m2.genomic_to_tx_interval(560, 585).tx_end,
    ) == (20, 45)


def test_minus_genomic_interval_over_intron_rejected(m2):
    with pytest.raises(RegionNotMappableError) as exc:
        m2.genomic_to_tx_interval(580, 625)  # E1 tail + intron + E2 tail
    gaps = exc.value.key_state["intronic_gaps"]
    assert gaps == [[585, 620]]


# ----------------------------------------------------------- adjacent exons

def test_adjacent_exons_no_gap(t3):
    assert t3.length == 25  # 10 + 15 with a zero-base gap


def test_exon_value_object_half_open_contains(t1):
    e0, e1, _ = t1.exons
    assert e0.contains_position(100) and e0.contains_position(129)
    assert not e0.contains_position(130)  # end is exclusive
    assert not e0.contains_position(99)
    assert e0.length == 30 and e1.length == 20
    assert t1.genomic_span() == (100, 240)


def test_error_to_dict_serializes_code_and_state(m1):
    with pytest.raises(IntronicPositionError) as exc:
        m1.genomic_to_tx_point(145)
    d = exc.value.to_dict()
    assert d["code"] == "intronic_position"
    assert d["key_state"]["genomic_position"] == 145


def test_adjacent_boundary_is_exonic_on_both_sides(m3):
    assert m3.tx_to_genomic_point(9).genomic_position == 49
    assert m3.tx_to_genomic_point(10).genomic_position == 50
    # g50 must be exonic (E1), not intronic
    assert m3.genomic_to_tx_point(50).tx_position == 10
    whole = m3.tx_to_genomic_interval(0, 25)
    assert whole.fragment_count == 2
    assert whole.mapped_length == 25


# --------------------------------------------------------------- range errors

def test_nonexistent_tx_position_categories(m1):
    with pytest.raises(CoordinateOutOfRangeError) as exc:
        m1.tx_to_genomic_point(80)  # mature length is 80, half-open end
    assert exc.value.code == "coordinate_out_of_range"
    assert exc.value.key_state["mature_length"] == 80

    with pytest.raises(InvalidIntervalError):
        m1.tx_to_genomic_point(-1)


def test_nonexistent_genomic_positions(m1):
    # outside the transcript span entirely
    with pytest.raises(CoordinateOutOfRangeError):
        m1.genomic_to_tx_point(0)
    with pytest.raises(CoordinateOutOfRangeError):
        m1.genomic_to_tx_point(240)  # half-open end
    with pytest.raises(InvalidIntervalError):
        m1.genomic_to_tx_point(-5)


def test_invalid_interval_shapes(m1, m2):
    with pytest.raises(InvalidIntervalError, match="start <= end"):
        m1.tx_to_genomic_interval(10, 5)
    with pytest.raises(InvalidIntervalError, match="zero-length"):
        m1.tx_to_genomic_interval(10, 10)
    with pytest.raises(CoordinateOutOfRangeError):
        m1.tx_to_genomic_interval(70, 90)
    with pytest.raises(InvalidIntervalError):
        m2.genomic_to_tx_interval(1.5, 3)  # type: ignore[arg-type]


def test_negative_vs_below_domain_categories(m1):
    # negative coordinate on either system -> malformed (invalid)
    with pytest.raises(InvalidIntervalError, match=">= 0"):
        m1.genomic_to_tx_interval(-10, 5)
    # non-negative but below this transcript's span -> out of range
    with pytest.raises(CoordinateOutOfRangeError, match="lower bound"):
        m1.genomic_to_tx_interval(0, 10)  # T1 span starts at 100


# ----------------------------------------------------- exhaustive oracle cross-check

def test_every_base_matches_independent_oracle_plus(m1, t1):
    from oracle import build_point_maps

    exons = [(e.start, e.end) for e in t1.exons]
    tx2g, g2tx = build_point_maps(exons, "+")

    g_batch, s_batch = m1.tx_points_to_genomic(list(range(t1.length)))
    t_batch, st_batch = m1.genomic_points_to_tx(
        [g for g in range(t1.tx_start, t1.tx_end)]
    )
    for p, g in enumerate(tx2g):
        assert int(g_batch[p]) == g
        assert s_batch[p] == "mapped"
        point = m1.tx_to_genomic_point(p)
        assert point.genomic_position == g

    j = 0
    for g in range(t1.tx_start, t1.tx_end):
        if g in g2tx:
            assert int(t_batch[j]) == g2tx[g]
            assert st_batch[j] == "mapped"
        else:
            assert st_batch[j] == "intronic_position"
        j += 1


def test_every_base_matches_independent_oracle_minus(m2, t2):
    from oracle import build_point_maps

    exons = [(e.start, e.end) for e in t2.exons]
    tx2g, g2tx = build_point_maps(exons, "-")

    g_batch, s_batch = m2.tx_points_to_genomic(list(range(t2.length)))
    for p, g in enumerate(tx2g):
        assert int(g_batch[p]) == g, p
        assert m2.tx_to_genomic_point(p).genomic_position == g
        assert m2.genomic_to_tx_point(g).tx_position == p

    t_batch, st_batch = m2.genomic_points_to_tx(
        [g for g in range(t2.tx_start, t2.tx_end)]
    )
    j = 0
    for g in range(t2.tx_start, t2.tx_end):
        if g in g2tx:
            assert int(t_batch[j]) == g2tx[g]
        else:
            assert st_batch[j] == "intronic_position"
        j += 1


def test_batch_status_categories(m1):
    g, status = m1.tx_points_to_genomic(np.array([-1, 0, 79, 80, 1000]))
    assert list(status) == [
        "invalid", "mapped", "mapped", "coordinate_out_of_range",
        "coordinate_out_of_range",
    ]
    assert int(g[1]) == 100 and int(g[2]) == 239


def test_oracle_site_classification_matches_batch(m1, t1):
    from oracle import classify_genomic

    exons = [(e.start, e.end) for e in t1.exons]
    positions = list(range(t1.tx_start, t1.tx_end))
    _, status = m1.genomic_points_to_tx(positions)
    for g, st in zip(positions, status):
        kind = classify_genomic(exons, g)
        assert st == ("mapped" if kind == "exon" else "intronic_position")


def test_length_conservation_all_exonic_intervals(m1, m2):
    # every fully-exonic tx interval preserves total length after split,
    # and each genomic piece maps back into exactly the original tx range
    for mapper in (m1, m2):
        L = mapper.mature_length
        for s in range(0, L, 7):
            for e in range(s + 1, L + 1, 11):
                out = mapper.tx_to_genomic_interval(s, e)
                assert out.mapped_length == e - s
                covered: list[int] = []
                for fr in out.fragments:
                    back = mapper.genomic_to_tx_interval(
                        fr.genomic_start, fr.genomic_end
                    )
                    assert back.fragment_count == 1
                    bf = back.fragments[0]
                    covered.extend(range(bf.tx_start, bf.tx_end))
                assert covered == list(range(s, e))
