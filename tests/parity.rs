//! Step-by-step parity between the production engine and the independent
//! reference model (`tests/common/model.rs`), driven by a deterministic
//! pseudo-random trace that mixes reads, writes, resizes and scripted
//! write-back failures. After EVERY step the full observable state is
//! compared: outcome, p, all four lists, dirty set, stats and store.

mod common;

use arc_cache::diag::Op;
use arc_cache::engine::Request;

use common::{flaky_script, pair, Step, XorShift};

fn run_trace(capacity: usize, steps: usize, seed: u64) {
    let (mut engine, mut model, store) = pair(capacity, 8, flaky_script());
    let mut rng = XorShift::new(seed);

    for i in 0..steps {
        let step = match rng.below(100) {
            0..=54 => Step::Read(rng.below(8)),
            55..=84 => {
                let page = rng.below(8);
                let len = 1 + rng.below(8) as usize;
                let data = vec![(rng.next() % 251) as u8; len];
                Step::Write(page, data)
            }
            _ => Step::Resize(rng.below(6) as usize),
        };

        // Apply to the engine.
        let (eng_outcome, eng_err) = match &step {
            Step::Read(page) => {
                let r = engine.submit(Request {
                    request_id: None,
                    op: Op::Read,
                    page: *page,
                    data: None,
                });
                match r.result {
                    Ok(ok) => (Some(ok.outcome), None),
                    Err(e) => (None, Some(e.category())),
                }
            }
            Step::Write(page, data) => {
                let r = engine.submit(Request {
                    request_id: None,
                    op: Op::Write,
                    page: *page,
                    data: Some(data.clone()),
                });
                match r.result {
                    Ok(ok) => (Some(ok.outcome), None),
                    Err(e) => (None, Some(e.category())),
                }
            }
            Step::Resize(new_c) => match engine.resize(*new_c) {
                Ok(()) => (None, None),
                Err(e) => (None, Some(e.category())),
            },
        };

        // Apply to the independent model.
        let (mod_outcome, mod_err) = match &step {
            Step::Read(page) => match model.read(*page) {
                Ok((_, outcome)) => (Some(outcome), None),
                Err(e) => (None, Some(e)),
            },
            Step::Write(page, data) => match model.write(*page, data.clone()) {
                Ok(outcome) => (Some(outcome), None),
                Err(e) => (None, Some(e)),
            },
            Step::Resize(new_c) => match model.resize(*new_c) {
                Ok(()) => (None, None),
                Err(e) => (None, Some(e)),
            },
        };

        assert_eq!(
            eng_outcome, mod_outcome,
            "step {i} ({step:?}): outcome diverged"
        );
        assert_eq!(eng_err, mod_err, "step {i} ({step:?}): error category diverged");

        // Full state comparison.
        let cache = engine.cache();
        assert_eq!(cache.p(), model.p, "step {i}: p diverged");
        let lists = cache.lists();
        assert_eq!(lists.t1, model.t1, "step {i}: T1 diverged");
        assert_eq!(lists.t2, model.t2, "step {i}: T2 diverged");
        assert_eq!(lists.b1, model.b1, "step {i}: B1 diverged");
        assert_eq!(lists.b2, model.b2, "step {i}: B2 diverged");
        assert_eq!(
            cache.dirty_pages(),
            model.dirty_ids(),
            "step {i}: dirty set diverged"
        );
        assert_eq!(cache.stats(), &model.stats, "step {i}: stats diverged");
        assert_eq!(
            store.entries(),
            model.store,
            "step {i}: backing store diverged"
        );
    }
}

#[test]
fn parity_capacity_4() {
    run_trace(4, 3000, 0x5EED_0001);
}

#[test]
fn parity_capacity_2() {
    run_trace(2, 3000, 0x5EED_0002);
}

#[test]
fn parity_capacity_1() {
    run_trace(1, 2000, 0x5EED_0003);
}

#[test]
fn parity_capacity_zero_start() {
    run_trace(0, 2000, 0x5EED_0004);
}

#[test]
fn parity_capacity_7() {
    run_trace(7, 3000, 0x5EED_0005);
}
