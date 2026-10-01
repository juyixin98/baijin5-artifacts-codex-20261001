//! Deterministic randomized differential testing: generate connected chain
//! joins with duplicate rows and random projections, stitch engine pages back
//! together, and compare the full multiset to the independent naive oracle.
//!
//! The expected answers come exclusively from the naive oracle plus
//! multiset reconstruction invariants — never from the engine under test.
mod common;

use common::*;
use leapfrog_triejoin::domain::{Datum, LogicalType, NullPolicy};

/// Tiny deterministic xorshift64 PRNG (no dev-dependency needed).
struct Rng(u64);

impl Rng {
    fn next_u64(&mut self) -> u64 {
        let mut x = self.0;
        x ^= x << 13;
        x ^= x >> 7;
        x ^= x << 17;
        self.0 = x;
        x
    }
    fn below(&mut self, n: u64) -> usize {
        (self.next_u64() % n) as usize
    }
}

/// Build a connected chain: relation i has columns (v_i, v_{i+1}), so the
/// join graph is always one connected component. Small domain + duplicates
/// exercise multiplicity heavily.
fn chain_relations(rng: &mut Rng, rel_count: usize) -> Vec<leapfrog_triejoin::RelationInput> {
    let vars: Vec<String> = (0..=rel_count).map(|i| format!("v{i}")).collect();
    (0..rel_count)
        .map(|i| {
            let cols = vec![
                (&vars[i][..], LogicalType::Int64),
                (&vars[i + 1][..], LogicalType::Int64),
            ];
            let n_rows = 1 + rng.below(8);
            let rows = (0..n_rows)
                .map(|_| {
                    // domain 0..3 forces heavy overlap and duplicate tuples
                    ints(&[rng.below(4) as i64, rng.below(4) as i64])
                })
                .collect();
            rel(&format!("r{i}"), &cols, rows)
        })
        .collect()
}

fn stitch(case: &common::Case, page: u64) -> Vec<(Vec<Datum>, u128)> {
    let mut all = Vec::new();
    let mut cursor = None;
    loop {
        let out = engine_rows(case, page, cursor);
        for r in out.rows {
            all.push((r.values, r.multiplicity));
        }
        match out.next_cursor {
            Some(next) => cursor = Some(next),
            None => break,
        }
    }
    all
}

#[test]
fn randomized_chains_match_oracle_across_projections_and_pages() {
    let mut rng = Rng(0x1234_5678_9abc_def0);
    let mut checked = 0usize;

    for _ in 0..150 {
        let rel_count = 2 + rng.below(3); // 2..=4 relations
        let relations = chain_relations(&mut rng, rel_count);
        let mut request = req(relations);
        request.null_policy = NullPolicy::DropJoinRows; // generated data has no NULL anyway

        // Optionally project a non-prefix subset of the global variables.
        let all_vars: Vec<String> = (0..=rel_count).map(|i| format!("v{i}")).collect();
        if rng.below(2) == 0 {
            let keep = 1 + rng.below(all_vars.len() as u64);
            let mut sel: Vec<String> = all_vars.clone();
            // deterministic partial shuffle-then-truncate
            for i in (1..sel.len()).rev() {
                let j = rng.below((i + 1) as u64);
                sel.swap(i, j);
            }
            sel.truncate(keep);
            sel.sort();
            request.select = Some(sel);
        }

        let case = match std::panic::catch_unwind(|| compile_case(request.clone())) {
            Ok(c) => c,
            Err(_) => continue, // a relation could be empty post build; skip
        };

        let oracle = oracle_rows(&case);
        let page = 1 + rng.below(5) as u64; // tiny pages stress resumption
        let got = stitch(&case, page);

        assert_eq!(
            got.len(),
            oracle.len(),
            "row count mismatch for request {:?}",
            request
        );
        for (g, want) in got.iter().zip(oracle.iter()) {
            assert_eq!(g.0, want.values, "value mismatch on {:?}", request);
            assert_eq!(
                g.1, want.multiplicity,
                "multiplicity mismatch on {:?}",
                request
            );
        }

        // Reconstruction invariant: stitched rows are strictly sorted & unique.
        for w in got.windows(2) {
            assert!(w[0].0 < w[1].0, "pages must stitch in sorted order, no dup");
        }
        checked += 1;
    }

    assert!(
        checked >= 100,
        "expected many generated cases, got {checked}"
    );
}
