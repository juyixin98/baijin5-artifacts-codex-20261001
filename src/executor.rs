//! Out-of-core set operators.
//!
//! Implements the six SQL set operations over two typed relations:
//!
//! | op | DISTINCT | ALL (multiset) |
//! |----|----------|----------------|
//! | UNION | a key appears once if present in either side | multiplicity `a + b` (checked) |
//! | INTERSECT | once if present in both | multiplicity `min(a, b)` |
//! | EXCEPT | once if present in `left` but not `right` | multiplicity `a - b` floored at 0 |
//!
//! # Correctness under memory limits
//!
//! Relations are hash-partitioned by the canonical row key (see
//! [`crate::encoding`]) into buffer-bounded partitions spilled as immutable
//! Arrow key segments. Both sides use the *same* partition function, so equal
//! rows always land in the same partition and partitions are independent.
//!
//! The hash is only routing: within a partition, rows are grouped by **exact
//! key comparison** (`HashMap<Vec<u8>, _>`), so a hash bucket collision never
//! merges distinct rows. If a partition's distinct-key table cannot fit in the
//! table budget, the partition is **recursively re-partitioned** with a fresh
//! level salt and retried; a single distinct key larger than the table budget
//! is irreducible and reported as `resource_exhausted/partition_key_too_large`.
//!
//! Results are emitted as `(key, multiplicity)` pairs and spilled in bounded
//! fragments, so neither aggregation nor result assembly is resident in full.
//! UNION ALL multiplicity is added with checked arithmetic and *rejects* u64
//! overflow rather than wrapping.

use std::collections::HashMap;

use crate::encoding::decode_row_typed;
use crate::error::{Result, SetOpError};
use crate::partition::{self, Buffer, choose_fanout};
// Re-exported so callers pair `executor::Executor` with `executor::Partitioner`.
pub use crate::partition::Partitioner;
use crate::resource::{Budget, checked_add_counts, key_table_cost};
use crate::spill::{Segment, SpillManager};
use crate::value::{Schema, Value};

/// Which set operation.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Deserialize, serde::Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SetOperator {
    Union,
    Intersect,
    Except,
}

/// ALL or DISTINCT quantifier.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Deserialize, serde::Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Quantifier {
    All,
    Distinct,
}

/// One side of a set operation: a schema and ordered typed row batches.
pub struct RelationInput {
    pub schema: Schema,
    pub batches: Vec<Vec<Vec<Value>>>,
}

/// Per-side multiplicities for one distinct key.
#[derive(Clone, Copy, Default)]
struct Counts {
    left: u64,
    right: u64,
}

/// Replay-relevant intermediate state. Serialized into every run log.
#[derive(Debug, Clone, Default, serde::Serialize)]
pub struct ExecStats {
    pub fanout: usize,
    pub spill_segments: usize,
    pub spill_bytes: u64,
    pub recursive_splits: usize,
    /// Exact-key HashMap probes performed (collisions resolved by comparison).
    pub key_probes: u64,
    pub result_fragments: usize,
    pub output_rows: u64,
    pub output_distinct_keys: u64,
}

/// The output of an execution: on-disk result fragments plus statistics.
#[derive(Debug)]
pub struct ExecOutput {
    pub result_segments: Vec<Segment>,
    pub stats: ExecStats,
}

const MAX_SPLIT_DEPTH: usize = 24;

pub struct Executor<'a> {
    schema: Schema,
    budget: Budget,
    spill: &'a SpillManager,
    fanout: usize,
    stats: ExecStats,
    result_buf: Vec<(Vec<u8>, u64)>,
    result_segments: Vec<Segment>,
}

impl<'a> Executor<'a> {
    pub fn new(schema: Schema, budget: Budget, spill: &'a SpillManager) -> Result<Self> {
        schema.validate()?;
        budget.validate()?;
        Ok(Self {
            schema,
            budget,
            spill,
            fanout: 2,
            stats: ExecStats::default(),
            result_buf: Vec::new(),
            result_segments: Vec::new(),
        })
    }

    /// Construct with a predetermined fan-out (must match how the
    /// [`Partitioner`]s were built for the streaming entry point).
    pub fn with_fanout(
        schema: Schema,
        budget: Budget,
        spill: &'a SpillManager,
        fanout: usize,
    ) -> Result<Self> {
        let mut s = Self::new(schema, budget, spill)?;
        s.fanout = fanout;
        s.stats.fanout = fanout;
        Ok(s)
    }

    pub fn execute(
        mut self,
        op: SetOperator,
        quant: Quantifier,
        left: &RelationInput,
        right: &RelationInput,
    ) -> Result<ExecOutput> {
        if left.schema != self.schema || right.schema != self.schema {
            return Err(SetOpError::input(
                "schema_mismatch",
                "both inputs must use the same declared schema",
            ));
        }

        let total_rows = left.batches.iter().map(Vec::len).sum::<usize>()
            + right.batches.iter().map(Vec::len).sum::<usize>();
        self.fanout = choose_fanout(total_rows, &self.budget);
        self.stats.fanout = self.fanout;

        let mut lp = Partitioner::new(self.fanout);
        let mut rp = Partitioner::new(self.fanout);
        lp.ingest_batches(&left.batches, 0, self.spill, &self.budget, "l")?;
        rp.ingest_batches(&right.batches, 0, self.spill, &self.budget, "r")?;
        let lsegs = lp.finish(0, self.spill, &self.budget, "l")?;
        let rsegs = rp.finish(0, self.spill, &self.budget, "r")?;

        self.run_segments(op, quant, lsegs, rsegs)
    }

    /// Drain already-partitioned segment sets (used by streaming ingestion).
    pub fn run_segments(
        &mut self,
        op: SetOperator,
        quant: Quantifier,
        lsegs: Vec<Vec<Segment>>,
        rsegs: Vec<Vec<Segment>>,
    ) -> Result<ExecOutput> {
        if lsegs.len() != self.fanout || rsegs.len() != self.fanout {
            return Err(SetOpError::compute(
                "partition_arity",
                format!(
                    "ingested {}x{} partitions but fanout is {}",
                    lsegs.len(),
                    rsegs.len(),
                    self.fanout
                ),
            ));
        }
        for p in 0..self.fanout {
            self.process_partition(op, quant, 0, p, &lsegs[p], &rsegs[p])?;
        }
        self.flush_results("result_tail")?;

        self.stats.spill_segments = self.spill.counters().files;
        self.stats.spill_bytes = self.spill.counters().bytes;
        Ok(ExecOutput {
            result_segments: self.result_segments.clone(),
            stats: self.stats.clone(),
        })
    }

    /// Aggregate one aligned partition pair, re-splitting on table overflow.
    #[allow(clippy::too_many_arguments)]
    fn process_partition(
        &mut self,
        op: SetOperator,
        quant: Quantifier,
        level: u32,
        id: usize,
        lsegs: &[Segment],
        rsegs: &[Segment],
    ) -> Result<()> {
        if lsegs.is_empty() && rsegs.is_empty() {
            return Ok(());
        }
        match self.aggregate(lsegs, rsegs)? {
            Aggregation::Fits(table) => {
                self.emit_table(op, quant, table)?;
                Ok(())
            }
            Aggregation::Overflow => {
                if level as usize >= MAX_SPLIT_DEPTH {
                    return Err(SetOpError::resource(
                        "partition_too_large",
                        "recursive split depth exhausted for a skew-bound partition",
                    ));
                }
                self.stats.recursive_splits += 1;
                self.resplit(op, quant, level + 1, id, lsegs, rsegs)
            }
        }
    }

    /// Build the exact-key count table for one partition.
    fn aggregate(&mut self, lsegs: &[Segment], rsegs: &[Segment]) -> Result<Aggregation> {
        let mut table: HashMap<Vec<u8>, Counts> = HashMap::new();
        let mut used_bytes = 0usize;

        for (is_left, segs) in [(true, lsegs), (false, rsegs)] {
            for seg in segs {
                for key in self.spill.read_keys(seg)? {
                    self.stats.key_probes = self.stats.key_probes.saturating_add(1);
                    if let Some(counts) = table.get_mut(&key) {
                        bump_side(counts, is_left)?;
                    } else {
                        let cost = key_table_cost(key.len());
                        if cost > self.budget.partition_table_bytes {
                            return Err(SetOpError::resource(
                                "partition_key_too_large",
                                format!(
                                    "a single distinct row key ({key_len} bytes) exceeds the \
                                     partition table budget ({budget} bytes)",
                                    key_len = key.len(),
                                    budget = self.budget.partition_table_bytes
                                ),
                            ));
                        }
                        if used_bytes + cost > self.budget.partition_table_bytes {
                            // Multiple distinct keys: re-splitting can separate
                            // them (the existing table is discarded and the
                            // original segments re-read at the next level).
                            return Ok(Aggregation::Overflow);
                        }
                        used_bytes += cost;
                        let mut counts = Counts::default();
                        bump_side(&mut counts, is_left)?;
                        table.insert(key, counts);
                    }
                }
            }
        }
        Ok(Aggregation::Fits(table))
    }

    /// Re-partition one overflowing pair at a deeper hash level and recurse.
    #[allow(clippy::too_many_arguments)]
    fn resplit(
        &mut self,
        op: SetOperator,
        quant: Quantifier,
        level: u32,
        parent: usize,
        lsegs: &[Segment],
        rsegs: &[Segment],
    ) -> Result<()> {
        let fanout = self.fanout.max(2);
        let mut lb: Vec<Buffer> = (0..fanout).map(|_| Buffer::new()).collect();
        let mut rb: Vec<Buffer> = (0..fanout).map(|_| Buffer::new()).collect();
        let mut lout: Vec<Vec<Segment>> = vec![Vec::new(); fanout];
        let mut rout: Vec<Vec<Segment>> = vec![Vec::new(); fanout];

        for (is_left, segs) in [(true, lsegs), (false, rsegs)] {
            let side = if is_left { "l" } else { "r" };
            for seg in segs {
                for key in self.spill.read_keys(seg)? {
                    let q = partition::route(&key, level, fanout);
                    let (buff, dst) = if is_left {
                        (&mut lb[q], &mut lout[q])
                    } else {
                        (&mut rb[q], &mut rout[q])
                    };
                    buff.push(key);
                    if buff.bytes >= self.budget.partition_buffer_bytes {
                        let label = format!("lv{level}_p{parent}_{side}sub{q}");
                        let s = self.spill.write_keys(&buff.keys, &label)?;
                        dst.push(s);
                        *buff = Buffer::new();
                    }
                }
            }
        }

        for q in 0..fanout {
            if !lb[q].is_empty() {
                let s = self
                    .spill
                    .write_keys(&lb[q].keys, &format!("lv{level}_p{parent}_lsub{q}tail"))?;
                lout[q].push(s);
            }
            if !rb[q].is_empty() {
                let s = self
                    .spill
                    .write_keys(&rb[q].keys, &format!("lv{level}_p{parent}_rsub{q}tail"))?;
                rout[q].push(s);
            }
            self.process_partition(op, quant, level, q, &lout[q], &rout[q])?;
        }
        Ok(())
    }

    /// Apply the set rule to an aggregated table and push to the result sink.
    fn emit_table(
        &mut self,
        op: SetOperator,
        quant: Quantifier,
        table: HashMap<Vec<u8>, Counts>,
    ) -> Result<()> {
        for (key, c) in table {
            let count = output_count(op, quant, c.left, c.right)?;
            if count > 0 {
                self.stats.output_rows =
                    self.stats.output_rows.checked_add(count).ok_or_else(|| {
                        SetOpError::resource(
                            "count_overflow",
                            "total output multiplicity overflowed u64",
                        )
                    })?;
                self.stats.output_distinct_keys = self.stats.output_distinct_keys.saturating_add(1);
                self.result_buf.push((key, count));
                if self.result_buf.len() >= self.budget.output_buffer_rows {
                    self.flush_results("result_part")?;
                }
            }
        }
        Ok(())
    }

    fn flush_results(&mut self, label: &str) -> Result<()> {
        if self.result_buf.is_empty() {
            return Ok(());
        }
        let seg = self.spill.write_results(&self.result_buf, label)?;
        self.result_segments.push(seg);
        self.stats.result_fragments = self.result_segments.len();
        self.result_buf.clear();
        Ok(())
    }
}

enum Aggregation {
    Fits(HashMap<Vec<u8>, Counts>),
    Overflow,
}

fn bump_side(counts: &mut Counts, is_left: bool) -> Result<()> {
    if is_left {
        counts.left = counts.left.checked_add(1).ok_or_else(|| {
            SetOpError::resource("count_overflow", "left-side multiplicity overflowed u64")
        })?;
    } else {
        counts.right = counts.right.checked_add(1).ok_or_else(|| {
            SetOpError::resource("count_overflow", "right-side multiplicity overflowed u64")
        })?;
    }
    Ok(())
}

/// Result multiplicity for one key given its per-side multiplicities.
///
/// - UNION ALL adds and **rejects** u64 overflow (not saturating),
/// - INTERSECT ALL takes the minimum,
/// - EXCEPT ALL subtracts the right multiset floored at zero,
/// - DISTINCT collapses to presence.
pub fn output_count(op: SetOperator, quant: Quantifier, left: u64, right: u64) -> Result<u64> {
    match op {
        SetOperator::Union => match quant {
            Quantifier::All => checked_add_counts(left, right),
            Quantifier::Distinct => Ok(u64::from(left > 0 || right > 0)),
        },
        SetOperator::Intersect => match quant {
            Quantifier::All => Ok(left.min(right)),
            Quantifier::Distinct => Ok(u64::from(left > 0 && right > 0)),
        },
        SetOperator::Except => match quant {
            Quantifier::All => Ok(left.saturating_sub(right)),
            Quantifier::Distinct => Ok(u64::from(left > 0 && right == 0)),
        },
    }
}

/// Decode a result fragment into typed rows, expanding ALL multiplicities. The
/// returned vector is bounded to one fragment (callers page fragment-by-
/// fragment), keeping result materialization external-memory friendly.
pub fn decode_result_fragment(
    schema: &Schema,
    items: &[(Vec<u8>, u64)],
) -> Result<Vec<Vec<Value>>> {
    let total: usize = items
        .iter()
        .map(|(_, n)| (*n).try_into().unwrap_or(usize::MAX))
        .sum();
    let mut rows = Vec::with_capacity(total.min(1 << 20));
    for (key, count) in items {
        let row = decode_row_typed(key, schema)?;
        for _ in 0..*count {
            rows.push(row.clone());
        }
    }
    Ok(rows)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::value::{Schema, Value, parse_row};
    use serde_json::json;

    fn simple_schema() -> Schema {
        serde_json::from_value::<Schema>(json!({
            "fields": [
                {"name": "a", "data_type": {"kind": "int64"}, "nullable": true},
                {"name": "s", "data_type": {"kind": "utf8"}, "nullable": true}
            ]
        }))
        .unwrap()
    }

    fn rows(schema: &Schema, vals: &[serde_json::Value]) -> Vec<Vec<Value>> {
        vals.iter().map(|v| parse_row(schema, v).unwrap()).collect()
    }

    /// Run all six ops and decode every result fragment into a flat row list.
    fn run(
        op: SetOperator,
        quant: Quantifier,
        budget: Budget,
        left: &[Vec<Value>],
        right: &[Vec<Value>],
    ) -> (Vec<Vec<Value>>, ExecStats) {
        let dir = tempfile::tempdir().unwrap();
        let spill = SpillManager::new(dir.path(), budget);
        let schema = simple_schema();
        let input_l = RelationInput {
            schema: schema.clone(),
            batches: vec![left.to_vec()],
        };
        let input_r = RelationInput {
            schema: schema.clone(),
            batches: vec![right.to_vec()],
        };
        let exec = Executor::new(schema.clone(), spill.budget().clone(), &spill).unwrap();
        let out = exec.execute(op, quant, &input_l, &input_r).unwrap();
        let mut all = Vec::new();
        for seg in &out.result_segments {
            let items = spill.read_results(seg).unwrap();
            all.extend(decode_result_fragment(&schema, &items).unwrap());
        }
        (all, out.stats)
    }

    fn tight_budget() -> Budget {
        Budget {
            // memory/(2*buffer) = 512/256 = 2, forcing a minimal fan-out so a
            // partition holds many distinct keys and exercises re-splitting.
            memory_bytes: 512,
            partition_buffer_bytes: 128,
            partition_table_bytes: 256,
            spill_bytes: 4 * 1024 * 1024,
            spill_files: 10_000,
            output_buffer_rows: 4,
        }
    }

    #[test]
    fn all_six_operations_match_concrete_expectations() {
        use serde_json::Value as J;
        let schema = simple_schema();
        // left:  [1,"a"] x2, [2,"b"], [null,"a"], [3,null]
        // right: [1,"a"],      [2,"b"] x2,            [4,"d"], [null,null]
        let lj = vec![
            json!([1, "a"]),
            json!([1, "a"]),
            json!([2, "b"]),
            json!([null, "a"]),
            json!([3, null]),
        ];
        let rj = vec![
            json!([1, "a"]),
            json!([2, "b"]),
            json!([2, "b"]),
            json!([4, "d"]),
            json!([null, null]),
        ];
        let l = rows(&schema, &lj);
        let r = rows(&schema, &rj);

        // ((op, quant), expected rows as JSON)
        let cases: Vec<(SetOperator, Quantifier, Vec<J>)> = vec![
            (
                SetOperator::Union,
                Quantifier::All,
                // 1a:3, 2b:3, null-a:1, 3-null:1, 4d:1, null-null:1 => 10
                vec![
                    json!([1, "a"]),
                    json!([1, "a"]),
                    json!([1, "a"]),
                    json!([2, "b"]),
                    json!([2, "b"]),
                    json!([2, "b"]),
                    json!([null, "a"]),
                    json!([3, null]),
                    json!([4, "d"]),
                    json!([null, null]),
                ],
            ),
            (
                SetOperator::Union,
                Quantifier::Distinct,
                vec![
                    json!([1, "a"]),
                    json!([2, "b"]),
                    json!([null, "a"]),
                    json!([3, null]),
                    json!([4, "d"]),
                    json!([null, null]),
                ],
            ),
            (
                SetOperator::Intersect,
                Quantifier::All,
                // min: 1a:1, 2b:1 => 2
                vec![json!([1, "a"]), json!([2, "b"])],
            ),
            (
                SetOperator::Intersect,
                Quantifier::Distinct,
                vec![json!([1, "a"]), json!([2, "b"])],
            ),
            (
                SetOperator::Except,
                Quantifier::All,
                // 1a:2-1=1, 2b:1-2=0, null-a:1, 3-null:1
                vec![json!([1, "a"]), json!([null, "a"]), json!([3, null])],
            ),
            (
                SetOperator::Except,
                Quantifier::Distinct,
                // present only in left: null-a, 3-null  (1a,2b in both)
                vec![json!([null, "a"]), json!([3, null])],
            ),
        ];

        for (op, quant, expected) in cases {
            let budget = tight_budget();
            let (got_rows, stats) = run(op, quant, budget, &l, &r);
            let got_ms = crate::reference::Multiset::from_rows(&got_rows);
            let exp_rows = rows(&schema, &expected);
            let exp_ms = crate::reference::Multiset::from_rows(&exp_rows);
            assert!(
                got_ms.equals(&exp_ms),
                "{op:?} {quant:?} mismatch: {}\n  got {} rows, expected {}",
                crate::reference::Multiset::diff_summary(&exp_ms, &got_ms, 10),
                got_rows.len(),
                expected.len()
            );
            assert!(stats.fanout >= 2);
        }
    }

    #[test]
    fn null_keys_and_nested_shapes_are_distinct() {
        let schema = simple_schema();
        // [null,null] must differ from [0,null], [null,""], [false?] etc.
        let l = rows(
            &schema,
            &[json!([null, null]), json!([0, null]), json!([null, ""])],
        );
        let r = rows(&schema, &[json!([null, null])]);
        let (got, _) = run(
            SetOperator::Except,
            Quantifier::Distinct,
            tight_budget(),
            &l,
            &r,
        );
        let got_ms = crate::reference::Multiset::from_rows(&got);
        let exp = crate::reference::Multiset::from_rows(&rows(
            &schema,
            &[json!([0, null]), json!([null, ""])],
        ));
        assert!(
            got_ms.equals(&exp),
            "{}",
            crate::reference::Multiset::diff_summary(&exp, &got_ms, 10)
        );
    }

    #[test]
    fn output_count_rules_are_exact() {
        use crate::error::ErrorKind;
        assert_eq!(
            output_count(SetOperator::Union, Quantifier::All, 2, 3).unwrap(),
            5
        );
        assert_eq!(
            output_count(SetOperator::Intersect, Quantifier::All, 2, 3).unwrap(),
            2
        );
        assert_eq!(
            output_count(SetOperator::Except, Quantifier::All, 2, 3).unwrap(),
            0
        );
        assert_eq!(
            output_count(SetOperator::Except, Quantifier::All, 5, 2).unwrap(),
            3
        );
        let err = output_count(SetOperator::Union, Quantifier::All, u64::MAX, 1).unwrap_err();
        assert_eq!(err.kind, ErrorKind::ResourceExhausted);
        assert_eq!(err.code, "count_overflow");
    }

    #[test]
    fn skewed_partition_recursively_splits_and_stays_correct() {
        // One heavy key (many duplicates) plus a spread of distinct keys forces
        // table overflow and recursive splitting under a tiny table budget.
        let schema = simple_schema();
        let mut lj = Vec::new();
        for _ in 0..400 {
            lj.push(json!([1, "heavy"]));
        }
        for i in 0..60i64 {
            lj.push(json!([1000 + i, "x"]));
        }
        let mut rj = Vec::new();
        for _ in 0..100 {
            rj.push(json!([1, "heavy"]));
        }
        for i in 0..60i64 {
            rj.push(json!([1000 + i, "x"]));
        }
        let l = rows(&schema, &lj);
        let r = rows(&schema, &rj);
        let (got, stats) = run(SetOperator::Except, Quantifier::All, tight_budget(), &l, &r);
        // heavy: 400-100 = 300; each distinct x:1-1=0
        let got_ms = crate::reference::Multiset::from_rows(&got);
        let exp = crate::reference::Multiset::from_rows(&rows(
            &schema,
            &(0..300).map(|_| json!([1, "heavy"])).collect::<Vec<_>>(),
        ));
        assert!(
            got_ms.equals(&exp),
            "{}",
            crate::reference::Multiset::diff_summary(&exp, &got_ms, 10)
        );
        // Evidence the external path was actually exercised.
        assert!(
            stats.recursive_splits >= 1,
            "expected recursive splits, got {stats:?}"
        );
        assert!(stats.spill_segments >= 1, "expected spills, got {stats:?}");
    }

    #[test]
    fn oversized_single_key_is_reported_as_resource() {
        // A single distinct key larger than partition_table_bytes cannot be
        // split; engine must return resource_exhausted, not loop or succeed.
        let dir = tempfile::tempdir().unwrap();
        let budget = Budget {
            memory_bytes: 64 * 1024,
            partition_buffer_bytes: 1024,
            partition_table_bytes: 24, // smaller than any key
            spill_bytes: 4 * 1024 * 1024,
            spill_files: 10_000,
            output_buffer_rows: 4,
        };
        let spill = SpillManager::new(dir.path(), budget);
        let schema = simple_schema();
        let l = rows(&schema, &[json!([1, "a"])]);
        let r = rows(&schema, &[json!([2, "b"])]);
        let input_l = RelationInput {
            schema: schema.clone(),
            batches: vec![l],
        };
        let input_r = RelationInput {
            schema: schema.clone(),
            batches: vec![r],
        };
        let exec = Executor::new(schema, spill.budget().clone(), &spill).unwrap();
        let err = exec
            .execute(SetOperator::Union, Quantifier::Distinct, &input_l, &input_r)
            .unwrap_err();
        assert_eq!(err.kind, crate::error::ErrorKind::ResourceExhausted);
        assert_eq!(err.code, "partition_key_too_large");
    }
}
