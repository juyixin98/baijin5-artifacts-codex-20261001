//! Cross-checks of the IEJoin core against the INDEPENDENT nested-loop
//! reference over the four inequality directions, all-equal values,
//! empty sides and NULL handling. Result multisets must be identical
//! and duplicate multiplicity preserved.
//!
//! Expectations are hand-computed literals in `fixtures`; the reference
//! join is an additional cross-check written as brute force with no
//! shared algorithm code.

use iejoin::batch::multiset;
use iejoin::fixtures as fx;
use iejoin::operator::comparator::Comparator;
use iejoin::operator::iejoin::PreparedJoin;
use iejoin::operator::reference;
use iejoin::resource::Budget;

fn pair_ids(
    pairs: &[iejoin::operator::iejoin::MatchPair],
    sc: &fx::Scenario,
) -> Vec<(String, String)> {
    let mut v: Vec<_> = pairs
        .iter()
        .map(|p| {
            (
                sc.left.row_ids[p.left_row as usize].clone(),
                sc.right.row_ids[p.right_row as usize].clone(),
            )
        })
        .collect();
    v.sort();
    v
}

fn assert_matches_hand_and_reference(sc: &fx::Scenario, expected: &[(&str, &str)]) {
    let prep = PreparedJoin::prepare(&sc.plan, &sc.left, &sc.right).unwrap();
    let out = prep.run_all(Budget::default().max_output_pairs).unwrap();

    // 1) Exact hand-computed multiset.
    let got = pair_ids(&out.pairs, sc);
    let mut want: Vec<(String, String)> = expected
        .iter()
        .map(|(l, r)| ((*l).to_owned(), (*r).to_owned()))
        .collect();
    want.sort();
    assert_eq!(
        got, want,
        "scenario {}: IEJoin output differs from hand-computed expectation",
        sc.name
    );

    // 2) Independent nested-loop reference multiset.
    let reflow = reference::execute(&sc.plan, &sc.left, &sc.right).unwrap();
    let mut ref_ids: Vec<(String, String)> = reflow
        .pairs
        .iter()
        .map(|p| (p.left_id.clone(), p.right_id.clone()))
        .collect();
    ref_ids.sort();
    assert_eq!(
        got, ref_ids,
        "scenario {}: IEJoin output differs from nested-loop reference",
        sc.name
    );

    // 3) Multiset (with multiplicity), explicitly, via BTreeMap.
    let ie_ms = multiset(
        &out.pairs
            .iter()
            .map(|p| iejoin::batch::OutputPair {
                left_id: sc.left.row_ids[p.left_row as usize].clone(),
                right_id: sc.right.row_ids[p.right_row as usize].clone(),
            })
            .collect::<Vec<_>>(),
    );
    let ref_ms = multiset(&reflow.pairs);
    assert_eq!(ie_ms, ref_ms, "scenario {}: multiset mismatch", sc.name);
}

#[test]
fn all_four_directions_match_hand_answers_and_reference() {
    assert_matches_hand_and_reference(
        &fx::by_name("grid_lt").unwrap(),
        &fx::grid_expected(Comparator::Lt),
    );
    assert_matches_hand_and_reference(
        &fx::by_name("grid_le").unwrap(),
        &fx::grid_expected(Comparator::Le),
    );
    assert_matches_hand_and_reference(
        &fx::by_name("grid_gt").unwrap(),
        &fx::grid_expected(Comparator::Gt),
    );
    assert_matches_hand_and_reference(
        &fx::by_name("grid_ge").unwrap(),
        &fx::grid_expected(Comparator::Ge),
    );
}

#[test]
fn all_equal_strict_is_empty_lenient_is_full_cartesian() {
    // Strict: equal values never satisfy < ; result empty.
    assert_matches_hand_and_reference(
        &fx::by_name("equal_strict").unwrap(),
        &fx::all_equal_expected(true),
    );
    // Non-strict: every duplicate pair appears exactly once.
    assert_matches_hand_and_reference(
        &fx::by_name("equal_lenient").unwrap(),
        &fx::all_equal_expected(false),
    );
}

#[test]
fn duplicate_rows_preserve_identity_and_multiplicity() {
    let sc = fx::by_name("equal_lenient").unwrap();
    let prep = PreparedJoin::prepare(&sc.plan, &sc.left, &sc.right).unwrap();
    let out = prep.run_all(Budget::default().max_output_pairs).unwrap();
    let ms = multiset(
        &out.pairs
            .iter()
            .map(|p| iejoin::batch::OutputPair {
                left_id: sc.left.row_ids[p.left_row as usize].clone(),
                right_id: sc.right.row_ids[p.right_row as usize].clone(),
            })
            .collect::<Vec<_>>(),
    );
    // Each of 3 distinct left identities x 2 right identities once.
    assert_eq!(ms.len(), 6, "six distinct identity pairs");
    for &count in ms.values() {
        assert_eq!(count, 1, "no identity pair duplicated or lost");
    }
    assert_eq!(out.pairs.len(), 6);
}

#[test]
fn empty_sides_are_empty() {
    assert_matches_hand_and_reference(&fx::by_name("empty_left").unwrap(), &[]);
    assert_matches_hand_and_reference(&fx::by_name("empty_right").unwrap(), &[]);
}

#[test]
fn nulls_never_match() {
    assert_matches_hand_and_reference(&fx::by_name("nulls").unwrap(), &fx::null_expected());
}

#[test]
fn every_named_scenario_agrees_with_reference() {
    for name in fx::all_named() {
        let sc = fx::by_name(name).unwrap();
        let prep = PreparedJoin::prepare(&sc.plan, &sc.left, &sc.right).unwrap();
        let out = prep.run_all(Budget::default().max_output_pairs).unwrap();
        let reflow = reference::execute(&sc.plan, &sc.left, &sc.right).unwrap();
        assert_eq!(
            out.pairs.len(),
            reflow.pairs.len(),
            "scenario {name} length"
        );
    }
}
