//! Larger-than-memory demonstration / acceptance harness.
//!
//! Generates two relations *lazily* (no input materialization), forces the
//! engine into the external path with tiny buffers/table budgets, runs
//! EXCEPT ALL, and verifies the spilled result key-by-key against an
//! independent closed-form expectation. It prints replay-relevant counters.
//!
//! Run:
//! ```sh
//! cargo run --release --example larger_than_memory
//! # knobs:
//! DISTINCT_KEYS=50000 LEFT_REP=3 SETOPS_BUFFER=4096 SETOPS_TABLE=65536 \
//!   cargo run --release --example larger_than_memory
//! ```

use std::collections::BTreeMap;
use std::time::Instant;

use setops::executor::{Executor, Partitioner, Quantifier, SetOperator};
use setops::resource::Budget;
use setops::spill::SpillManager;
use setops::value::{Schema, Value};

fn env_usize(key: &str, default: usize) -> usize {
    std::env::var(key)
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(default)
}

/// One logical row: `(key, payload)` where payload embeds the key and some
/// padding so keys are realistically sized.
fn make_row(key: u64) -> Vec<Value> {
    vec![
        Value::Int64(key as i64),
        Value::Utf8(format!("pad-pad-pad-{key:010}")),
    ]
}

/// Lazy repetition iterator for one side. `rep(key)` gives multiplicity.
struct RepeatRows {
    n_keys: u64,
    key: u64,
    rep_left: u64,
    within: u64,
    is_left: bool,
}

impl RepeatRows {
    fn side(n_keys: u64, rep_left: u64, is_left: bool) -> Self {
        Self {
            n_keys,
            key: 0,
            rep_left,
            within: 0,
            is_left,
        }
    }
    /// multiplicity of key on the right: 1 for even keys, 0 for odd.
    fn right_rep(key: u64) -> u64 {
        u64::from(key & 1 == 0)
    }
}

impl Iterator for RepeatRows {
    type Item = Vec<Value>;
    fn next(&mut self) -> Option<Vec<Value>> {
        loop {
            if self.key >= self.n_keys {
                return None;
            }
            let rep = if self.is_left {
                self.rep_left
            } else {
                Self::right_rep(self.key)
            };
            if self.within < rep {
                self.within += 1;
                return Some(make_row(self.key));
            }
            self.key += 1;
            self.within = 0;
        }
    }
}

fn schema() -> Schema {
    serde_json::from_value(serde_json::json!({
        "fields": [
            {"name": "id", "data_type": {"kind": "int64"}, "nullable": true},
            {"name": "tag", "data_type": {"kind": "utf8"}, "nullable": true}
        ]
    }))
    .unwrap()
}

fn main() {
    let n_keys = env_usize("DISTINCT_KEYS", 50_000) as u64;
    let left_rep = env_usize("LEFT_REP", 3) as u64;
    let fanout = env_usize("FANOUT", 4);

    let budget = Budget {
        memory_bytes: env_usize("SETOPS_MEMORY", 1 << 20),
        partition_buffer_bytes: env_usize("SETOPS_BUFFER", 4096),
        partition_table_bytes: env_usize("SETOPS_TABLE", 65_536),
        spill_bytes: env_usize("SETOPS_SPILL", 1 << 30) as u64,
        spill_files: env_usize("SETOPS_SPILL_FILES", 1_000_000),
        output_buffer_rows: env_usize("SETOPS_OUTPUT_ROWS", 1024),
    };
    budget.validate().unwrap();

    let dir = tempfile::tempdir().expect("tempdir for larger-than-memory run");
    let spill = SpillManager::new(dir.path(), budget.clone());
    let schema = schema();

    let mut left = Partitioner::new(fanout);
    let mut right = Partitioner::new(fanout);

    let started = Instant::now();
    let l_count = left
        .ingest_iter(
            &mut RepeatRows::side(n_keys, left_rep, true),
            0,
            &spill,
            &budget,
            "l",
        )
        .unwrap();
    let r_count = right
        .ingest_iter(
            &mut RepeatRows::side(n_keys, left_rep, false),
            0,
            &spill,
            &budget,
            "r",
        )
        .unwrap();
    let lsegs = left.finish(0, &spill, &budget, "l").unwrap();
    let rsegs = right.finish(0, &spill, &budget, "r").unwrap();
    println!(
        "ingested left={l_count} right={r_count} rows in {:.2?} (never materialized)",
        started.elapsed()
    );
    println!(
        "partition segments: left={} right={} spill_files_after_ingest={}",
        lsegs.iter().map(Vec::len).sum::<usize>(),
        rsegs.iter().map(Vec::len).sum::<usize>(),
        spill.counters().files
    );

    let mut executor =
        Executor::with_fanout(schema.clone(), budget.clone(), &spill, fanout).unwrap();
    let out = executor
        .run_segments(SetOperator::Except, Quantifier::All, lsegs, rsegs)
        .unwrap();
    println!("executed in {:.2?}", started.elapsed());
    println!(
        "stats: {}",
        serde_json::to_string_pretty(&out.stats).unwrap()
    );
    println!(
        "spill counters: {} files / {} bytes",
        spill.counters().files,
        spill.counters().bytes
    );

    // Independent closed-form expectation for EXCEPT ALL:
    //  even key: left_rep - 1 ; odd key: left_rep - 0.
    let mut expected: BTreeMap<i64, u64> = BTreeMap::new();
    let mut expected_total = 0u64;
    for k in 0..n_keys {
        let r = RepeatRows::right_rep(k);
        let n = (left_rep as i64 - r as i64).max(0) as u64;
        if n > 0 {
            expected.insert(k as i64, n);
            expected_total += n;
        }
    }

    // Stream result fragments; aggregate multiplicity per decoded key.
    let mut got: BTreeMap<i64, u64> = BTreeMap::new();
    let mut decoded_rows = 0u64;
    for seg in &out.result_segments {
        let items = spill.read_results(seg).unwrap();
        let rows = setops::executor::decode_result_fragment(&schema, &items).unwrap();
        for row in rows {
            decoded_rows += 1;
            if let Value::Int64(k) = &row[0] {
                *got.entry(*k).or_insert(0) += 1;
            } else {
                panic!("unexpected key type");
            }
        }
    }

    assert_eq!(
        decoded_rows, out.stats.output_rows,
        "decoded rows match reported count"
    );
    assert_eq!(
        decoded_rows, expected_total,
        "EXCEPT ALL total multiplicity mismatch"
    );
    assert_eq!(
        got.len(),
        expected.len(),
        "distinct result key count mismatch"
    );
    let mut diffs = 0usize;
    for (k, want) in &expected {
        match got.get(k) {
            Some(g) if g == want => {}
            other => {
                if diffs < 10 {
                    eprintln!("MISMATCH key={k} expected={want} got={other:?}");
                }
                diffs += 1;
            }
        }
    }
    assert_eq!(
        diffs, 0,
        "{diffs} keys disagreed with the independent oracle"
    );

    // Evidence the external + recursive path was genuinely exercised.
    assert!(spill.counters().files > 50, "expected heavy spilling");
    assert!(
        out.stats.recursive_splits >= 1,
        "skewed/large partitions should have been recursively split: {:?}",
        out.stats
    );

    println!(
        "OK: {decoded_rows} result rows across {} distinct keys match independent oracle; {} recursive splits",
        got.len(),
        out.stats.recursive_splits
    );
}
