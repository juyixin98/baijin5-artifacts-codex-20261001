//! Execution engine for the six operators.
//!
//! GRACE-style recursive partition hash aggregation with a shared byte budget:
//!
//! ```text
//! ingest (one pass per side):
//!   rows -> canonical RowKey -> bucket table[b] (aggregating multiplicity)
//!   when sum of resident key bytes across ALL buckets exceeds the budget:
//!       flush the largest bucket table to a checksummed spill frame
//! per bucket:
//!   spilled? -> flush resident remnant as a final part, then read both sides'
//!               parts back through an independent level+1 hash and recurse
//!   clean?   -> both sides resident -> byte-wise multiset combine -> output
//! ```
//!
//! Why this satisfies the contract:
//! * hash collisions cannot merge rows — tables key on the full canonical key
//!   bytes; the hash only picks a bucket;
//! * ALL is arithmetic on multiplicities (add / saturating-sub / min), DISTINCT
//!   is presence;
//! * skew from duplicate multiplicity collapses during aggregation (a hot key
//!   is one row with a count), skew from distinct keys is broken by an
//!   independent per-level SplitMix hash;
//! * every addition is checked against `max_count` — overflow is rejected with
//!   `resource_exhausted/count_overflow`.

use std::path::Path;

use crate::batch::decode::decode_keys;
use crate::batch::encode::{RowKey, encode_rows, level_partition};
use crate::batch::{Schema, TypedBatch};
use crate::error::{InputCode, ResourceCode, Result, SetOpsError};
use crate::operator::counts::{CountTable, combine};
use crate::operator::{ExecutionMode, Qualifier, Query, SetOp};
use crate::resource::ResourceLimits;
use crate::runlog::{RunEvent, RunLog, Side};
use crate::spill::{FrameReader, SpillManager, SpillPart};

/// Rows per emitted Arrow2 output chunk.
const OUTPUT_BATCH_ROWS: u64 = 1024;

#[derive(Debug, Clone, serde::Serialize)]
pub struct SetOpStats {
    pub run_id: String,
    pub mode: ExecutionMode,
    pub op: SetOp,
    pub qualifier: Qualifier,
    pub left_rows: u64,
    pub right_rows: u64,
    pub output_rows: u64,
    pub output_distinct: u64,
    pub spills: u64,
    pub spilled_rows: u64,
    pub spill_bytes: u64,
    pub recursions: u64,
    pub max_resident_bytes: usize,
}

pub struct SetOpOutput {
    pub schema: Schema,
    pub batches: Vec<TypedBatch>,
    pub stats: SetOpStats,
    pub log: RunLog,
}

impl std::fmt::Debug for SetOpOutput {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("SetOpOutput")
            .field("run_id", &self.stats.run_id)
            .field("op", &self.stats.op)
            .field("qualifier", &self.stats.qualifier)
            .field("output_rows", &self.stats.output_rows)
            .field("output_distinct", &self.stats.output_distinct)
            .finish_non_exhaustive()
    }
}

impl SetOpOutput {
    /// All output rows flattened to canonical keys (multiplicity expanded).
    pub fn all_keys(&self) -> Vec<RowKey> {
        self.batches.iter().flat_map(encode_rows).collect()
    }
}

pub fn execute(
    query: Query,
    limits: ResourceLimits,
    mode: ExecutionMode,
    spill_dir: Option<&Path>,
    run_id: Option<String>,
) -> Result<SetOpOutput> {
    limits.validate()?;
    if matches!(mode, ExecutionMode::InMemory) && spill_dir.is_some() {
        return Err(SetOpsError::input(
            InputCode::InvalidRequest,
            "in_memory mode cannot use a spill directory",
        ));
    }
    // allow_spill=false forces the resident-only contract even in auto mode:
    // any budget breach is rejected as memory_budget. external mode requires
    // spilling and therefore conflicts with it.
    let mode = if limits.allow_spill {
        mode
    } else {
        if matches!(mode, ExecutionMode::External) {
            return Err(SetOpsError::input(
                InputCode::InvalidRequest,
                "execution_mode=external conflicts with allow_spill=false",
            ));
        }
        ExecutionMode::InMemory
    };
    let spill_dir = if matches!(mode, ExecutionMode::InMemory) {
        None
    } else {
        spill_dir
    };

    let schema = resolve_schema(&query)?;
    for b in query.left.iter().chain(query.right.iter()) {
        schema.compatible_with(&b.schema)?;
    }

    let left_rows: u64 = query.left.iter().map(|b| b.rows() as u64).sum();
    let right_rows: u64 = query.right.iter().map(|b| b.rows() as u64).sum();

    let run_id = run_id.unwrap_or_else(crate::runlog::new_run_id);
    let mut log = match spill_dir {
        Some(dir) => RunLog::with_file(dir.join("logs"), run_id.clone())?,
        None => RunLog::new(run_id.clone()),
    };
    log.record(RunEvent::QueryStart {
        op: query.op.sql().to_string(),
        qualifier: query.qualifier.sql().to_string(),
        mode: format!("{mode:?}").to_ascii_lowercase(),
        left_rows: left_rows as usize,
        right_rows: right_rows as usize,
        columns: schema.len(),
    });

    let spill_root = spill_dir
        .map(|d| d.join(&run_id))
        .unwrap_or_else(|| std::env::temp_dir().join(format!("setops-{run_id}")));
    // When no caller-provided spill directory exists, frames go to a private
    // temp directory that is always removed before returning (success or not).
    let remove_spill_root = spill_dir.is_none();
    let mut mgr = SpillManager::new(&spill_root, &limits)?;

    let mut engine = Engine {
        op: query.op,
        qualifier: query.qualifier,
        limits: &limits,
        mode,
        fanout: limits.partition_fanout,
        max_depth: limits.max_partition_depth,
        mgr: &mut mgr,
        log: &mut log,
        out_pending: Vec::new(),
        pending_rows: 0,
        output_rows: 0,
        output_distinct: 0,
        spills: 0,
        spilled_rows: 0,
        recursions: 0,
        max_resident: 0,
        schema: &schema,
        batches: Vec::new(),
        part_counter: 0,
        consumed_paths: std::collections::HashSet::new(),
    };

    let mut run_result = engine.run(query.left, query.right);
    if run_result.is_ok()
        && let Err(e) = engine.flush_output()
    {
        run_result = Err(e);
    }

    match &run_result {
        Ok(()) => {
            engine.log.record(RunEvent::Verdict {
                status: "success",
                error_kind: None,
                error_code: None,
                message: format!(
                    "{} {} produced {} rows ({} distinct)",
                    query.op.sql(),
                    query.qualifier.sql(),
                    engine.output_rows,
                    engine.output_distinct
                ),
            });
        }
        Err(e) => {
            engine.log.record(RunEvent::Verdict {
                status: "error",
                error_kind: Some(error_kind_name(&e.kind)),
                error_code: Some(serde_json::to_string(&e.kind).unwrap_or_default()),
                message: e.message.clone(),
            });
        }
    }
    let output_rows = engine.output_rows;
    let output_distinct = engine.output_distinct;
    let spills = engine.spills;
    let spilled_rows = engine.spilled_rows;
    let recursions = engine.recursions;
    let max_resident = engine.max_resident;
    let batches = std::mem::take(&mut engine.batches);
    drop(engine);
    let spill_bytes_total = mgr.written_bytes();
    drop(mgr);
    if remove_spill_root {
        let _ = std::fs::remove_dir_all(&spill_root);
    }
    let stats = SetOpStats {
        run_id: run_id.clone(),
        mode,
        op: query.op,
        qualifier: query.qualifier,
        left_rows,
        right_rows,
        output_rows,
        output_distinct,
        spills,
        spilled_rows,
        spill_bytes: spill_bytes_total,
        recursions,
        max_resident_bytes: max_resident,
    };
    run_result?;
    Ok(SetOpOutput {
        schema,
        batches,
        stats,
        log,
    })
}

fn error_kind_name(kind: &crate::error::ErrorKind) -> String {
    match kind {
        crate::error::ErrorKind::Input(_) => "input",
        crate::error::ErrorKind::StateConflict(_) => "state_conflict",
        crate::error::ErrorKind::ResourceExhausted(_) => "resource_exhausted",
        crate::error::ErrorKind::Compute => "compute",
    }
    .to_string()
}

fn resolve_schema(query: &Query) -> Result<Schema> {
    query
        .left
        .first()
        .or_else(|| query.right.first())
        .map(|b| b.schema.clone())
        .ok_or_else(|| {
            SetOpsError::input(
                InputCode::InvalidRequest,
                "at least one input batch is required to infer the schema",
            )
        })
}

/// Per-side result of one ingestion pass.
struct Ingested {
    tables: Vec<CountTable>,
    /// Finished spill parts, grouped by bucket.
    parts: Vec<Vec<SpillPart>>,
}

impl Ingested {
    fn empty(fanout: usize) -> Self {
        Self {
            tables: (0..fanout).map(|_| CountTable::default()).collect(),
            parts: (0..fanout).map(|_| Vec::new()).collect(),
        }
    }
}

struct Engine<'a> {
    op: SetOp,
    qualifier: Qualifier,
    limits: &'a ResourceLimits,
    mode: ExecutionMode,
    fanout: usize,
    max_depth: usize,
    mgr: &'a mut SpillManager,
    log: &'a mut RunLog,
    out_pending: Vec<(RowKey, u64)>,
    pending_rows: u64,
    output_rows: u64,
    output_distinct: u64,
    spills: u64,
    spilled_rows: u64,
    recursions: u64,
    max_resident: usize,
    schema: &'a Schema,
    batches: Vec<TypedBatch>,
    /// Monotonic per-run part number so spill filenames never collide across
    /// buckets or recursion levels.
    part_counter: usize,
    /// Spill frames fully absorbed during the current bucket's resident
    /// combine attempt (so repartition must not read them a second time).
    consumed_paths: std::collections::HashSet<std::path::PathBuf>,
}

impl<'a> Engine<'a> {
    fn run(&mut self, left: Vec<TypedBatch>, right: Vec<TypedBatch>) -> Result<()> {
        let ing_l = self.ingest_batches(Side::Left, "", 0, &left)?;
        let ing_r = self.ingest_batches(Side::Right, "", 0, &right)?;
        self.process_level(0, "", ing_l, ing_r)
    }

    /// One pass over typed input batches: encode one batch at a time (so
    /// resident encoding state is bounded by batch size), route into buckets.
    fn ingest_batches(
        &mut self,
        side: Side,
        pid_prefix: &str,
        level: usize,
        batches: &[TypedBatch],
    ) -> Result<Ingested> {
        let mut ing = Ingested::empty(self.fanout);
        let mut global_bytes = 0usize;
        let mut total_rows = 0usize;

        for (bi, batch) in batches.iter().enumerate() {
            let keys = encode_rows(batch);
            total_rows += keys.len();
            for key in keys {
                let b = level_partition(key.hash(), level, self.fanout);
                let before = ing.tables[b].resident_bytes();
                ing.tables[b].add(key, 1, self.limits)?;
                global_bytes += ing.tables[b].resident_bytes() - before;
                self.max_resident = self.max_resident.max(global_bytes);

                if global_bytes > self.limits.memory_bytes {
                    global_bytes =
                        self.enforce_budget(side, pid_prefix, level, &mut ing, global_bytes)?;
                }
            }
            self.log.record(RunEvent::InputRead {
                side: side.label().to_string(),
                source: format!("batch#{bi}"),
                batches: bi + 1,
                rows: total_rows,
            });
        }
        Ok(ing)
    }

    /// One pass over a bucket's spill frames at `level`.
    fn ingest_frames(
        &mut self,
        side: Side,
        pid_prefix: &str,
        level: usize,
        parts: &[SpillPart],
    ) -> Result<Ingested> {
        let mut ing = Ingested::empty(self.fanout);
        let mut global_bytes = 0usize;
        for part in parts {
            let mut read_rows = 0usize;
            for rec in FrameReader::open(&part.path)? {
                let (key, mult) = rec?;
                read_rows += 1;
                let b = level_partition(key.hash(), level, self.fanout);
                let before = ing.tables[b].resident_bytes();
                ing.tables[b].add(key, mult, self.limits)?;
                global_bytes += ing.tables[b].resident_bytes() - before;
                self.max_resident = self.max_resident.max(global_bytes);
                if global_bytes > self.limits.memory_bytes {
                    global_bytes =
                        self.enforce_budget(side, pid_prefix, level, &mut ing, global_bytes)?;
                }
            }
            self.log.record(RunEvent::SpillRead {
                side: side.label().to_string(),
                level,
                partition: trailing_index(&part.partition_id),
                rows: read_rows,
            });
        }
        Ok(ing)
    }

    /// Bring resident bytes back under budget by evicting the largest bucket
    /// table to a spill frame, repeated until the budget is met. In in-memory
    /// mode any breach is rejected instead.
    fn enforce_budget(
        &mut self,
        side: Side,
        pid_prefix: &str,
        level: usize,
        ing: &mut Ingested,
        mut global_bytes: usize,
    ) -> Result<usize> {
        if matches!(self.mode, ExecutionMode::InMemory) {
            self.log.record(RunEvent::MemoryCheck {
                level,
                partition: pid_prefix.to_string(),
                resident_bytes: global_bytes,
                budget_bytes: self.limits.memory_bytes,
                decision: "reject_in_memory",
            });
            return Err(SetOpsError::resource(
                ResourceCode::MemoryBudget,
                format!(
                    "{} side needs {global_bytes} resident key bytes but in-memory budget is {}",
                    side.label(),
                    self.limits.memory_bytes
                ),
            ));
        }
        while global_bytes > self.limits.memory_bytes {
            let Some(b) = (0..self.fanout)
                .filter(|&b| ing.tables[b].distinct() > 0)
                .max_by_key(|&b| ing.tables[b].resident_bytes())
            else {
                return Ok(0);
            };
            self.log.record(RunEvent::MemoryCheck {
                level,
                partition: child_pid(pid_prefix, b),
                resident_bytes: global_bytes,
                budget_bytes: self.limits.memory_bytes,
                decision: "spill",
            });
            let part_no = self.next_part_no();
            let pid = child_pid(pid_prefix, b);
            let part = self.flush_table(side, &pid, level, b, part_no, &mut ing.tables[b])?;
            ing.parts[b].push(part);
            global_bytes = sum_resident(&ing.tables);
        }
        Ok(global_bytes)
    }

    fn flush_table(
        &mut self,
        side: Side,
        pid: &str,
        level: usize,
        bucket: usize,
        part_no: usize,
        table: &mut CountTable,
    ) -> Result<SpillPart> {
        let mut w = self.mgr.begin_part(side.label(), pid, part_no)?;
        let mut distinct = 0u64;
        let mut bytes = 0u64;
        for (key, mult) in table.drain() {
            self.mgr.write_record(&mut w, &key, mult)?;
            distinct += 1;
            bytes += key.bytes().len() as u64;
        }
        let (path, n) = w.finish()?;
        debug_assert_eq!(n, distinct);
        self.spills += 1;
        self.spilled_rows += distinct;
        self.log.record(RunEvent::Spill {
            side: side.label().to_string(),
            level,
            partition: bucket,
            rows: distinct as usize,
            bytes,
            path: path.display().to_string(),
        });
        Ok(SpillPart {
            path,
            key_bytes: usize::try_from(bytes).unwrap_or(usize::MAX),
            partition_id: pid.to_string(),
        })
    }

    /// Pair buckets at one level.
    ///
    /// Hybrid policy per bucket:
    /// 1. both sides fully resident (no parts, not forced external) → combine;
    /// 2. otherwise try to absorb the spill frames back into resident tables
    ///    while staying within budget; on success combine directly even
    ///    though the bucket was spilled earlier (dedup merge often shrinks it);
    /// 3. only when that fails, flush remnants and repartition with the
    ///    independent level+1 hash and recurse.
    fn process_level(
        &mut self,
        level: usize,
        pid_prefix: &str,
        mut ing_l: Ingested,
        mut ing_r: Ingested,
    ) -> Result<()> {
        let force_external = matches!(self.mode, ExecutionMode::External) && level == 0;

        for b in 0..self.fanout {
            let pid = child_pid(pid_prefix, b);
            let mut t_l = std::mem::take(&mut ing_l.tables[b]);
            let mut t_r = std::mem::take(&mut ing_r.tables[b]);
            let parts_l = std::mem::take(&mut ing_l.parts[b]);
            let parts_r = std::mem::take(&mut ing_r.parts[b]);

            if parts_l.is_empty() && parts_r.is_empty() && !force_external {
                self.combine_resident(&pid, &t_l, &t_r)?;
                continue;
            }

            if !force_external
                && self.try_resident_combine(&pid, &mut t_l, &mut t_r, &parts_l, &parts_r)?
            {
                self.mgr.cleanup_parts(&parts_l);
                self.mgr.cleanup_parts(&parts_r);
                continue;
            }

            // Repartition path. t_l/t_r now aggregate (deduped) records of all
            // fully-consumed frames; unconsumed frames stay for level+1.
            let unconsumed = |parts: &[SpillPart]| -> Vec<SpillPart> {
                parts
                    .iter()
                    .filter(|p| !self.consumed_paths.contains(&p.path))
                    .cloned()
                    .collect()
            };
            let mut next_l = unconsumed(&parts_l);
            let mut next_r = unconsumed(&parts_r);
            if t_l.distinct() > 0 {
                let no = self.next_part_no();
                next_l.push(self.flush_table(Side::Left, &pid, level, b, no, &mut t_l)?);
            }
            if t_r.distinct() > 0 {
                let no = self.next_part_no();
                next_r.push(self.flush_table(Side::Right, &pid, level, b, no, &mut t_r)?);
            }
            drop((t_l, t_r));
            if next_l.is_empty() && next_r.is_empty() {
                continue;
            }
            if level + 1 > self.max_depth {
                return Err(SetOpsError::resource(
                    ResourceCode::PartitionDepth,
                    format!(
                        "partition {pid} still exceeds the {}-byte resident key budget at max \
                         recursion depth {level}; distinct keys cannot be split further",
                        self.limits.memory_bytes
                    ),
                ));
            }
            self.log.record(RunEvent::PartitionRecursed {
                level,
                partition: b,
                reason: if force_external {
                    "external_forced"
                } else {
                    "memory_pressure"
                }
                .to_string(),
                fanout: self.fanout,
            });
            self.recursions += 1;

            let child_l = if next_l.is_empty() {
                Ingested::empty(self.fanout)
            } else {
                self.ingest_frames(Side::Left, &pid, level + 1, &next_l)?
            };
            let child_r = if next_r.is_empty() {
                Ingested::empty(self.fanout)
            } else {
                self.ingest_frames(Side::Right, &pid, level + 1, &next_r)?
            };
            self.process_level(level + 1, &pid, child_l, child_r)?;

            self.mgr.cleanup_parts(&parts_l);
            self.mgr.cleanup_parts(&parts_r);
            self.mgr.cleanup_parts(&next_l);
            self.mgr.cleanup_parts(&next_r);
        }
        Ok(())
    }

    fn next_part_no(&mut self) -> usize {
        let n = self.part_counter;
        self.part_counter += 1;
        n
    }

    /// Frame-atomically absorb spill frames into one resident table. A frame
    /// is merged only when its distinct-key byte upper bound fits the
    /// remaining budget, so the resident table never breaches it. Returns the
    /// number of frames consumed; the failing frame and everything after it
    /// remain on disk for repartitioning.
    fn absorb_frames(&mut self, t: &mut CountTable, parts: &[SpillPart]) -> Result<usize> {
        let budget = self.limits.memory_bytes;
        for (i, part) in parts.iter().enumerate() {
            if t.resident_bytes() + part.key_bytes > budget {
                return Ok(i);
            }
            for rec in FrameReader::open(&part.path)? {
                let (key, mult) = rec?;
                t.add(key, mult, self.limits)?;
            }
            self.max_resident = self.max_resident.max(t.resident_bytes());
            self.consumed_paths.insert(part.path.clone());
        }
        Ok(parts.len())
    }

    /// Try to absorb both sides' frames and combine immediately. On failure
    /// tables keep the (aggregated) contents of the frames consumed so far.
    fn try_resident_combine(
        &mut self,
        pid: &str,
        t_l: &mut CountTable,
        t_r: &mut CountTable,
        parts_l: &[SpillPart],
        parts_r: &[SpillPart],
    ) -> Result<bool> {
        self.consumed_paths.clear();
        let n_l = self.absorb_frames(t_l, parts_l)?;
        let n_r = self.absorb_frames(t_r, parts_r)?;
        if n_l == parts_l.len() && n_r == parts_r.len() {
            self.combine_resident(pid, t_l, t_r)?;
            Ok(true)
        } else {
            Ok(false)
        }
    }

    fn combine_resident(&mut self, pid: &str, left: &CountTable, right: &CountTable) -> Result<()> {
        let mut keys: Vec<&RowKey> = left.iter().map(|(k, _)| k).collect();
        for (k, _) in right.iter() {
            if left.get(k).is_none() {
                keys.push(k);
            }
        }
        keys.sort_by(|a, b| a.bytes().cmp(b.bytes()));

        for key in keys {
            let l = left.get(key);
            let r = right.get(key);
            let n = combine(self.op, self.qualifier, l, r, self.limits)?;
            if n > 0 {
                self.log.record(RunEvent::CountArithmetic {
                    op: self.op.sql().to_string(),
                    key_hex_prefix: hex_prefix(key.bytes()),
                    left_count: l.unwrap_or(0),
                    right_count: r.unwrap_or(0),
                    result_count: n,
                    reason: arithmetic_reason(self.op, self.qualifier, l, r),
                });
                self.push_output(key.clone(), n, pid)?;
            }
        }
        Ok(())
    }

    fn push_output(&mut self, key: RowKey, mult: u64, _pid: &str) -> Result<()> {
        self.output_rows = self.output_rows.checked_add(mult).ok_or_else(|| {
            SetOpsError::resource(ResourceCode::OutputLimit, "output row count overflow")
        })?;
        if self.output_rows > self.limits.max_output_rows {
            return Err(SetOpsError::resource(
                ResourceCode::OutputLimit,
                format!(
                    "output exceeds configured max_output_rows of {}",
                    self.limits.max_output_rows
                ),
            ));
        }
        self.output_distinct += 1;
        self.pending_rows += mult;
        self.out_pending.push((key, mult));
        if self.pending_rows >= OUTPUT_BATCH_ROWS {
            self.flush_output()?;
        }
        Ok(())
    }

    fn flush_output(&mut self) -> Result<()> {
        if self.out_pending.is_empty() {
            return Ok(());
        }
        let total = self.pending_rows;
        let pairs = std::mem::take(&mut self.out_pending);
        let batch = decode_keys(self.schema, pairs)?;
        debug_assert_eq!(batch.rows() as u64, total);
        self.batches.push(batch);
        self.pending_rows = 0;
        self.log.record(RunEvent::OutputBatch {
            rows: total as usize,
            running_total: self.output_rows,
            distinct_keys: self.output_distinct as usize,
        });
        Ok(())
    }
}

// CountTable values are moved out of Ingested with `std::mem::take`.

fn child_pid(prefix: &str, bucket: usize) -> String {
    if prefix.is_empty() {
        format!("p{bucket:02}")
    } else {
        format!("{prefix}.p{bucket:02}")
    }
}

fn sum_resident(tables: &[CountTable]) -> usize {
    tables.iter().map(|t| t.resident_bytes()).sum()
}

fn arithmetic_reason(op: SetOp, q: Qualifier, l: Option<u64>, r: Option<u64>) -> String {
    let l = l.unwrap_or(0);
    let r = r.unwrap_or(0);
    match (op, q) {
        (SetOp::Union, Qualifier::All) => format!("{l} + {r} = {}", l + r),
        (SetOp::Intersect, Qualifier::All) => format!("min({l}, {r}) = {}", l.min(r)),
        (SetOp::Except, Qualifier::All) => {
            format!("{l} - {r} floored at 0 = {}", l.saturating_sub(r))
        }
        (_, Qualifier::Distinct) => "presence test -> 1".to_string(),
    }
}

fn hex_prefix(bytes: &[u8]) -> String {
    bytes
        .iter()
        .take(8)
        .map(|b| format!("{b:02x}"))
        .collect::<Vec<_>>()
        .join("")
}

fn trailing_index(pid: &str) -> usize {
    pid.rsplit('p')
        .next()
        .and_then(|s| s.parse().ok())
        .unwrap_or(0)
}
