//! Candidate-access accounting and randomized differential testing.
//!
//! The access-count test asserts the exact, hand-derived number of
//! candidate probes on a highly selective fixture, proving the sorted
//! plan inspects far fewer candidates than the nested-loop reference.
//!
//! The differential tests generate many small random inputs (including
//! duplicates and NULLs) over all four directions and assert multiset
//! equality against the independent reference. A fixed seed makes them
//! reproducible; each case prints its seed on failure for replay.

use iejoin::batch::{multiset, Batch, Column, OutputPair};
use iejoin::fixtures as fx;
use iejoin::operator::comparator::Comparator;
use iejoin::operator::iejoin::PreparedJoin;
use iejoin::operator::plan::{JoinPlan, Predicate};
use iejoin::operator::reference;
use iejoin::resource::Budget;

fn ids_of(pairs: &[iejoin::operator::iejoin::MatchPair], l: &Batch, r: &Batch) -> Vec<OutputPair> {
    pairs
        .iter()
        .map(|p| OutputPair {
            left_id: l.row_ids[p.left_row as usize].clone(),
            right_id: r.row_ids[p.right_row as usize].clone(),
        })
        .collect()
}

#[test]
fn selective_fixture_has_exact_candidate_access_count() {
    let sc = fx::by_name("selective").unwrap();
    let prep = PreparedJoin::prepare(&sc.plan, &sc.left, &sc.right).unwrap();
    let out = prep.run_all(Budget::default().max_output_pairs).unwrap();

    // Exact hand-derived instrumentation.
    assert_eq!(
        out.counters.candidate_accesses,
        fx::SELECTIVE_EXPECTED_CANDIDATE_ACCESSES,
        "candidate probes must equal hand-derived count"
    );
    assert_eq!(out.counters.gate1_steps, fx::SELECTIVE_EXPECTED_GATE_STEPS);
    assert_eq!(out.counters.pairs_emitted, fx::SELECTIVE_EXPECTED_PAIRS);
    assert_eq!(out.pairs.len() as u64, fx::SELECTIVE_EXPECTED_PAIRS);

    // Concrete result ids, not just a count.
    let got = ids_of(&out.pairs, &sc.left, &sc.right);
    let mut got_ids: Vec<_> = got
        .iter()
        .map(|p| (p.left_id.clone(), p.right_id.clone()))
        .collect();
    got_ids.sort();
    let mut want: Vec<_> = fx::selective_expected()
        .iter()
        .map(|(l, r)| ((*l).to_owned(), (*r).to_owned()))
        .collect();
    want.sort();
    assert_eq!(got_ids, want);

    // Work comparison: IEJoin probes << nested-loop p1 evaluations.
    let nl = reference::execute(&sc.plan, &sc.left, &sc.right).unwrap();
    assert_eq!(nl.stats.p1_evaluations, fx::SELECTIVE_NL_P1_EVALUATIONS);
    assert!(
        out.counters.candidate_accesses < nl.stats.p1_evaluations,
        "IEJoin {} probes should be far below nested-loop {} p1 evaluations",
        out.counters.candidate_accesses,
        nl.stats.p1_evaluations
    );
}

// ---- deterministic mini PRNG -------------------------------------------------

struct Rng(u64);
impl Rng {
    fn next_u64(&mut self) -> u64 {
        // xorshift64*
        let mut x = self.0;
        x ^= x >> 12;
        x ^= x << 25;
        x ^= x >> 27;
        self.0 = x;
        x.wrapping_mul(0x2545_F491_4F6C_DD1D)
    }
    fn below(&mut self, n: u64) -> usize {
        (self.next_u64() % n) as usize
    }
    fn nullable_key(&mut self, span: i64, null_pct: u64) -> Option<i64> {
        if self.below(100) < null_pct as usize {
            None
        } else {
            Some(self.below((span * 2 + 1) as u64) as i64 - span)
        }
    }
}

fn random_batch(rng: &mut Rng, n: usize, span: i64, null_pct: u64, prefix: &str) -> Batch {
    let x: Vec<Option<i64>> = (0..n).map(|_| rng.nullable_key(span, null_pct)).collect();
    let y: Vec<Option<i64>> = (0..n).map(|_| rng.nullable_key(span, null_pct)).collect();
    Batch::new(vec![Column::new("x", x), Column::new("y", y)])
        .unwrap()
        .with_row_ids((0..n).map(|i| format!("{prefix}{i}")).collect())
}

fn plan_for(op1: Comparator, op2: Comparator) -> JoinPlan {
    JoinPlan::new(Predicate::new("x", op1, "x"), Predicate::new("y", op2, "y"))
}

fn assert_case_matches_reference(
    seed: u64,
    op1: Comparator,
    op2: Comparator,
    l: &Batch,
    r: &Batch,
) {
    let plan = plan_for(op1, op2);
    let prep = PreparedJoin::prepare(&plan, l, r).unwrap();
    let out = prep.run_all(Budget::default().max_output_pairs).unwrap();
    let nl = reference::execute(&plan, l, r).unwrap();

    let ie_ms = multiset(&ids_of(&out.pairs, l, r));
    let ref_ms = multiset(&nl.pairs);
    assert_eq!(
        ie_ms, ref_ms,
        "differential case diverged: seed={seed} op1={op1:?} op2={op2:?} left_rows={} right_rows={}",
        l.row_count(),
        r.row_count()
    );
    assert_eq!(
        out.pairs.len(),
        nl.pairs.len(),
        "seed={seed} op1={op1:?} op2={op2:?} pair count"
    );
}

#[test]
fn randomized_differential_all_directions_with_nulls_and_dupes() {
    // Fixed seed: reproducible. Change SEED to explore; it is logged in
    // every failure message.
    const SEED: u64 = 0x1E5A_10A5;
    let mut rng = Rng(SEED);
    let dirs = [
        Comparator::Lt,
        Comparator::Le,
        Comparator::Gt,
        Comparator::Ge,
    ];

    let mut cases = 0;
    for _ in 0..400 {
        // Predicate directions are chosen independently, so mixed-order
        // plans (one ascending, one descending) are covered.
        let op1 = dirs[rng.below(4)];
        let op2 = dirs[rng.below(4)];
        let ln = rng.below(12); // 0..11, includes empty
        let rn = rng.below(12);
        // Small key span forces duplicates and equal groups; some NULLs.
        let span = (rng.below(4) + 1) as i64;
        let null_pct = rng.below(40) as u64;
        let l = random_batch(&mut rng, ln, span, null_pct, "l");
        let r = random_batch(&mut rng, rn, span, null_pct, "r");
        assert_case_matches_reference(SEED, op1, op2, &l, &r);
        cases += 1;
    }
    assert!(cases >= 400);
}

#[test]
fn randomized_larger_case_still_exact() {
    // A few denser cases to exercise bigger bitmaps and groups,
    // including a strictly mixed-direction plan.
    let mut rng = Rng(0x1234_5678);
    let combos = [
        (Comparator::Lt, Comparator::Lt),
        (Comparator::Le, Comparator::Gt),
        (Comparator::Gt, Comparator::Le),
        (Comparator::Ge, Comparator::Gt),
    ];
    for &(op1, op2) in &combos {
        let l = random_batch(&mut rng, 200, 50, 10, "l");
        let r = random_batch(&mut rng, 60, 50, 10, "r");
        assert_case_matches_reference(0x1234_5678, op1, op2, &l, &r);
    }
}

#[test]
fn all_equal_random_constant_keys() {
    // Every key identical: strict empty, lenient full product, incl NULL.
    for op1 in [
        Comparator::Lt,
        Comparator::Le,
        Comparator::Gt,
        Comparator::Ge,
    ] {
        for op2 in [
            Comparator::Lt,
            Comparator::Le,
            Comparator::Gt,
            Comparator::Ge,
        ] {
            let make = |n: usize, p: &str| {
                Batch::new(vec![
                    Column::new("x", vec![Some(7); n]),
                    Column::new("y", vec![Some(7); n]),
                ])
                .unwrap()
                .with_row_ids((0..n).map(|i| format!("{p}{i}")).collect())
            };
            let (l, r) = (make(5, "l"), make(3, "r"));
            assert_case_matches_reference(777, op1, op2, &l, &r);
        }
    }
}
