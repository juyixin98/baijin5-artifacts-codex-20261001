//! Execution engine: typed ingest -> bounded accumulation with external
//! spilling -> k-way merge -> operator state machines.
//!
//! Cancellation model
//! ------------------
//! Cancellation is cooperative and is serviced only at *whole-row* boundaries:
//! never in the middle of processing a row, so the resume cursor is always an
//! exact number of fully ingested rows. When it fires (external token or the
//! local fault-injection hint), every resident buffer is flushed and
//! `checkpoint.json` is written atomically. The response carries a resume
//! token; resuming replays the same request starting at the cursor row.

pub mod codec;
pub mod merge;
pub mod state;

use std::path::PathBuf;

use crate::batch::{Scalar, TypedBatch};
use crate::diagnostics::RequestId;
use crate::error::{ErrorKind, PctlError, Result};
use crate::exec::codec::Entry;
use crate::exec::merge::{FileSource, Merger, VecSource};
use crate::exec::state::{Registry, StateId};
use crate::operator::{ContSelector, DiscSelector, ModeOutcome, SortKey, StringAggStream};
use crate::resources::{CancellationToken, MemoryGuard, SpillManager};
use crate::spec::{
    BoundOperator, GroupKey, GroupResult, LogicalType, OperatorResult, OperatorSpec,
    PercentileMethod, QueryPlan, QueryRequest, ResumeToken, SortOrder,
};
use crate::validate;

/// Result of running one request to completion or to a safe checkpoint.
#[derive(Debug)]
pub enum ExecOutcome {
    Complete(Vec<GroupResult>, ExecStats),
    Cancelled {
        resume: ResumeToken,
        cursor: u64,
        stats: ExecStats,
    },
}

#[derive(Debug, Default, Clone)]
pub struct ExecStats {
    pub runs_spilled: u32,
    pub bytes_spilled: u64,
    pub groups_tracked: usize,
    pub groups_rejected_by_cap: u64,
    pub resident_high_water: usize,
    pub resident_budget: usize,
}

/// Everything needed to run one query.
pub struct Executor<'a> {
    plan: &'a QueryPlan,
    batch: &'a TypedBatch,
    registry: Registry,
    memory: MemoryGuard,
    spill: SpillManager,
    cancel: CancellationToken,
    stats: ExecStats,
    /// runs spilled during *this* attempt, for fault injection
    attempt_runs: u32,
    cancel_after_runs: Option<u32>,
    fingerprint: String,
    /// Fixed output direction per operator index (false = descending).
    op_ascending: Vec<bool>,
}

/// Stable fingerprint of query shape *and* input payload, so a resume token
/// cannot be replayed against different data.
fn plan_fingerprint(req: &QueryRequest) -> String {
    use std::collections::hash_map::DefaultHasher;
    use std::hash::{Hash, Hasher};
    let mut h = DefaultHasher::new();
    req.group_by.hash(&mut h);
    for op in &req.operators {
        format!("{op:?}").hash(&mut h);
    }
    for c in &req.columns {
        c.name.hash(&mut h);
        std::mem::discriminant(&c.data_type).hash(&mut h);
        c.values.len().hash(&mut h);
        for v in &c.values {
            // serde_json::Value: Hash is not implemented; use its string form,
            // which is canonical enough for a local integrity check.
            v.to_string().hash(&mut h);
        }
    }
    format!("{:016x}", h.finish())
}

/// Each operator has one fixed sort direction: string_agg honors `order`,
/// everything else is ascending.
fn op_directions(plan: &QueryPlan) -> Vec<bool> {
    plan.operators
        .iter()
        .map(|bo| {
            !matches!(
                bo.spec,
                OperatorSpec::StringAgg {
                    order: SortOrder::Desc,
                    ..
                }
            )
        })
        .collect()
}

impl<'a> Executor<'a> {
    /// Start a fresh query.
    pub fn start(
        req: &'a QueryRequest,
        plan: &'a QueryPlan,
        batch: &'a TypedBatch,
        rid: &'a RequestId,
        config: &'a crate::config::Config,
        cancel: CancellationToken,
    ) -> Result<Self> {
        let budget = req
            .hints
            .memory_budget_bytes
            .unwrap_or(config.memory_budget_bytes);
        let spill = SpillManager::new(&config.spill_dir, rid, config.max_spill_bytes)?;
        Ok(Self {
            plan,
            batch,
            registry: Registry::empty(config.group_table_cap),
            memory: MemoryGuard::new(budget),
            spill,
            cancel,
            stats: ExecStats {
                resident_budget: budget,
                ..ExecStats::default()
            },
            attempt_runs: 0,
            cancel_after_runs: req.hints.cancel_after_runs,
            fingerprint: plan_fingerprint(req),
            op_ascending: op_directions(plan),
        })
    }

    /// Resume from a checkpoint token carried by the same request payload.
    /// Returns the executor and the row cursor to continue from.
    pub fn resume(
        req: &'a QueryRequest,
        plan: &'a QueryPlan,
        batch: &'a TypedBatch,
        _rid: &'a RequestId,
        config: &'a crate::config::Config,
        cancel: CancellationToken,
        token: &ResumeToken,
    ) -> Result<(Self, u64)> {
        let budget = req
            .hints
            .memory_budget_bytes
            .unwrap_or(config.memory_budget_bytes);
        let root: PathBuf = config.spill_dir.join(&token.spill_dir);
        let spill = SpillManager::reopen(root, config.max_spill_bytes)?;
        let fingerprint = plan_fingerprint(req);
        let (registry, cursor) = state::recover(&spill, config.group_table_cap, &fingerprint)?;
        if cursor != token.ordinal_cursor {
            return Err(PctlError::new(
                ErrorKind::Validation,
                "resume_cursor_mismatch",
                format!(
                    "checkpoint cursor {cursor} does not match token cursor {}",
                    token.ordinal_cursor
                ),
            ));
        }
        let mut runs = 0u32;
        for s in registry.states() {
            runs += s.1.run_paths.len() as u32;
        }
        let bytes_spilled = spill.total_spilled_bytes();
        let exec = Self {
            plan,
            batch,
            registry,
            memory: MemoryGuard::new(budget),
            spill,
            cancel,
            stats: ExecStats {
                resident_budget: budget,
                runs_spilled: runs,
                bytes_spilled,
                ..ExecStats::default()
            },
            attempt_runs: 0,
            cancel_after_runs: req.hints.cancel_after_runs,
            fingerprint,
            op_ascending: op_directions(plan),
        };
        Ok((exec, cursor))
    }

    pub fn cancel_token(&self) -> CancellationToken {
        self.cancel.clone()
    }

    /// Ingest rows in `[skip .. n]`, then finalize. Consumes self because a
    /// finished query either deletes or hands over its spill directory.
    pub fn run(mut self, skip: u64) -> Result<ExecOutcome> {
        if let Err(e) = self.ingest(skip) {
            if e.kind == ErrorKind::Cancelled {
                let (cursor, _fp) = state::read_checkpoint_header(self.spill.dir())
                    .unwrap_or((skip, String::new()));
                let stats = self.stats_snapshot();
                let dir = self.spill_dir_name();
                return Ok(ExecOutcome::Cancelled {
                    resume: ResumeToken {
                        spill_dir: dir,
                        ordinal_cursor: cursor,
                    },
                    cursor,
                    stats,
                });
            }
            self.spill.cleanup();
            return Err(e);
        }
        if let Err(e) = self.service_cancellation() {
            if e.kind == ErrorKind::Cancelled {
                let (cursor, _fp) = state::read_checkpoint_header(self.spill.dir())
                    .unwrap_or((skip, String::new()));
                let stats = self.stats_snapshot();
                let dir = self.spill_dir_name();
                return Ok(ExecOutcome::Cancelled {
                    resume: ResumeToken {
                        spill_dir: dir,
                        ordinal_cursor: cursor,
                    },
                    cursor,
                    stats,
                });
            }
            return Err(e);
        }

        // Final flush: before producing results, move every resident buffer to
        // disk so the *result* strings have the full budget available and the
        // finalize phase is a pure k-way merge over run files.
        self.flush_all_resident()?;

        let groups = match self.finalize() {
            Ok(g) => g,
            Err(e) => {
                self.spill.cleanup();
                return Err(e);
            }
        };
        let stats = ExecStats {
            groups_tracked: self.registry.groups_tracked(),
            groups_rejected_by_cap: self.registry.rejected_by_cap,
            resident_high_water: self.memory.high_water(),
            ..self.stats
        };
        self.spill.cleanup();
        Ok(ExecOutcome::Complete(groups, stats))
    }

    // ---- ingest ---------------------------------------------------------------

    fn ingest(&mut self, skip: u64) -> Result<()> {
        let n = self.batch.len as u64;
        let operators = self.plan.operators.clone();
        for ordinal in skip..n {
            // Whole-row boundary: this is the only place cancellation is
            // serviced during ingestion.
            self.service_cancellation()?;

            let row = ordinal as usize;
            let group = self.extract_group(row)?;
            let g = self.registry.ordinal_for(group)?;
            self.registry.bump_rows(g);

            for bo in &operators {
                let scalar = self.batch.get(bo.column_index, row);
                if let Some(scalar) = scalar {
                    let is_nan = matches!(scalar, Scalar::F64(v) if v.is_nan());
                    let key = match scalar {
                        Scalar::I64(v) => SortKey::I64(v),
                        Scalar::F64(v) => SortKey::F64(v),
                        Scalar::Utf8(s) => SortKey::Utf8(s.to_string()),
                    };
                    let id = (g, bo.index);
                    self.append_with_budget(id, Entry { key, ordinal })?;
                    let st = self.registry.state_mut(id);
                    st.non_null += 1;
                    if is_nan {
                        st.has_nan = true;
                    }
                }
            }
        }
        Ok(())
    }

    fn extract_group(&self, row: usize) -> Result<Option<GroupKey>> {
        match self.batch.get(self.plan.group_column_index, row) {
            None => Ok(None), // NULL group: SQL NULLs share one group
            Some(Scalar::I64(v)) => Ok(Some(GroupKey::I64(v))),
            Some(Scalar::Utf8(s)) => Ok(Some(GroupKey::Utf8(s.to_string()))),
            Some(Scalar::F64(_)) => Err(PctlError::new(
                ErrorKind::Validation,
                "unsupported_group_type",
                "f64 GROUP BY rejected during validation",
            )),
        }
    }

    /// Append an entry, evicting the largest resident buffers until the shared
    /// budget admits it. A heavy group keeps flushing its own buffer: resident
    /// memory stays bounded under arbitrary skew.
    fn append_with_budget(&mut self, id: StateId, entry: Entry) -> Result<()> {
        let bytes = entry.charged_bytes();
        if self.memory.charge(bytes).is_ok() {
            self.registry.append_entry(id, entry, bytes);
            return Ok(());
        }
        loop {
            let victim = self.registry.largest_resident().ok_or_else(|| {
                PctlError::new(
                    ErrorKind::Resource,
                    "memory_budget_exceeded",
                    "no resident buffer is spillable but the budget is full",
                )
            })?;
            let size = self.registry.spill_state(
                victim,
                &self.spill,
                &self.memory,
                self.op_ascending[victim.1],
            )?;
            self.note_run(size);
            if self.memory.remaining() >= bytes {
                self.memory.charge(bytes)?;
                self.registry.append_entry(id, entry, bytes);
                return Ok(());
            }
        }
    }

    fn note_run(&mut self, size: u64) {
        if size == 0 {
            return;
        }
        self.stats.runs_spilled += 1;
        self.stats.bytes_spilled += size;
        self.attempt_runs += 1;
        if self.cancel_after_runs == Some(self.attempt_runs) {
            // Armed here; serviced at the next whole-row boundary.
            self.cancel.cancel();
        }
    }

    // ---- cancellation ---------------------------------------------------------

    /// If cancellation was requested, flush all resident buffers durably and
    /// publish the checkpoint, then return a `Cancelled` error.
    fn service_cancellation(&mut self) -> Result<()> {
        if !self.cancel.is_cancelled() {
            return Ok(());
        }
        while let Some(victim) = self.registry.largest_resident() {
            let size = self.registry.spill_state(
                victim,
                &self.spill,
                &self.memory,
                self.op_ascending[victim.1],
            )?;
            if size > 0 {
                self.stats.runs_spilled += 1;
                self.stats.bytes_spilled += size;
            }
        }
        let cursor: u64 = self.registry.groups().iter().map(|(_, rows)| *rows).sum();
        state::write_checkpoint(self.spill.dir(), &self.registry, cursor, &self.fingerprint)?;
        Err(PctlError::new(
            ErrorKind::Cancelled,
            "checkpoint",
            format!("cancelled after {cursor} fully-ingested rows; checkpoint durable"),
        ))
    }

    // ---- finalize -------------------------------------------------------------

    /// Spill every resident buffer before producing output. After this the
    /// finalize phase is a pure k-way merge over run files and the *result*
    /// strings can grow against the whole resident budget.
    fn flush_all_resident(&mut self) -> Result<()> {
        while let Some(victim) = self.registry.largest_resident() {
            let size = self.registry.spill_state(
                victim,
                &self.spill,
                &self.memory,
                self.op_ascending[victim.1],
            )?;
            if size > 0 {
                self.stats.runs_spilled += 1;
                self.stats.bytes_spilled += size;
            } else {
                // State reported resident but spill produced nothing: stop to
                // avoid spinning; budget accounting stays consistent.
                break;
            }
        }
        Ok(())
    }

    fn finalize(&mut self) -> Result<Vec<GroupResult>> {
        let mut out = Vec::with_capacity(self.registry.groups().len());
        let group_count = self.registry.groups().len();
        let operators = self.plan.operators.clone();
        for g in 0..group_count {
            let (key, rows) = self.registry.groups()[g].clone();
            let mut results = Vec::with_capacity(operators.len());
            for bo in &operators {
                let ascending = !matches!(
                    bo.spec,
                    OperatorSpec::StringAgg {
                        order: SortOrder::Desc,
                        ..
                    }
                );
                let r = self.finalize_one((g as u32, bo.index), bo, ascending)?;
                results.push(r);
            }
            out.push(GroupResult {
                group: key,
                rows,
                results,
            });
        }
        Ok(out)
    }

    fn finalize_one(
        &mut self,
        id: StateId,
        bo: &BoundOperator,
        ascending: bool,
    ) -> Result<OperatorResult> {
        let (non_null, has_nan) = {
            let st = self.registry.state_mut(id);
            (st.non_null, st.has_nan)
        };

        let (resident, _resident_charged) = self.registry.take_resident(id);
        // Resident vectors are sorted in the operator's fixed direction before
        // merge (normally empty here because finalize flushes first).
        let mut resident = resident;
        resident.sort_by(|a, b| a.compare(b, ascending));
        let paths: Vec<PathBuf> = self.registry.state_mut(id).run_paths.clone();

        let mut sources: Vec<Box<dyn merge::SortedSource>> = Vec::with_capacity(paths.len() + 1);
        sources.push(Box::new(VecSource::new(resident)));
        for p in &paths {
            sources.push(Box::new(FileSource::open(p)?));
        }
        let mut merger = Merger::new(sources, ascending, &self.cancel)?;
        let next = |m: &mut Merger, t: &mut Self| -> Result<Option<Entry>> {
            Ok(m.next(&t.cancel)?.map(|(_src, e)| e))
        };

        let result = match &bo.spec {
            OperatorSpec::Percentile { p, method, .. } => {
                if non_null == 0 {
                    null_ok(0)
                } else if has_nan {
                    OperatorResult::Indeterminate {
                        code: "nan_measure".into(),
                        detail: "measure contains NaN; a total percentile order is not decidable"
                            .into(),
                    }
                } else {
                    match method {
                        PercentileMethod::Continuous => {
                            let mut sel = ContSelector::new(non_null, *p);
                            while let Some(e) = next(&mut merger, self)? {
                                match e.key {
                                    SortKey::F64(v) => sel.feed(v),
                                    SortKey::I64(v) => sel.feed(v as f64),
                                    SortKey::Utf8(_) => unreachable!("cont rejects utf8"),
                                }
                            }
                            let value = sel.finish(*p).expect("non-null count > 0");
                            f64_result(
                                value,
                                "percentile_cont produced non-finite result",
                                non_null,
                            )?
                        }
                        PercentileMethod::Discrete => match bo.column_type {
                            LogicalType::I64 => {
                                let mut sel = DiscSelector::<i64>::new(non_null, *p);
                                while let Some(e) = next(&mut merger, self)? {
                                    if let SortKey::I64(v) = e.key {
                                        sel.feed(v);
                                    }
                                }
                                OperatorResult::Ok {
                                    result_type: "i64".into(),
                                    value: Some(sel.finish().unwrap().into()),
                                    tie: false,
                                    frequency: None,
                                    non_null,
                                }
                            }
                            LogicalType::F64 => {
                                let mut sel = DiscSelector::<f64>::new(non_null, *p);
                                while let Some(e) = next(&mut merger, self)? {
                                    if let SortKey::F64(v) = e.key {
                                        sel.feed(v);
                                    }
                                }
                                let v = sel.finish().unwrap();
                                f64_result(
                                    v,
                                    "percentile_disc produced non-finite result",
                                    non_null,
                                )?
                            }
                            LogicalType::Utf8 => {
                                let target = crate::operator::disc_target_index(non_null, *p);
                                let mut seen = 0usize;
                                let mut chosen: Option<String> = None;
                                while let Some(e) = next(&mut merger, self)? {
                                    if let SortKey::Utf8(v) = e.key {
                                        if seen == target {
                                            chosen = Some(v);
                                        }
                                        seen += 1;
                                    }
                                }
                                OperatorResult::Ok {
                                    result_type: "utf8".into(),
                                    value: Some(chosen.unwrap().into()),
                                    tie: false,
                                    frequency: None,
                                    non_null,
                                }
                            }
                        },
                    }
                }
            }
            OperatorSpec::Mode { .. } => {
                if non_null == 0 {
                    null_ok(0)
                } else {
                    // mode always merges ascending: tie-break is smallest key
                    let outcome = stream_mode(&mut merger, &self.cancel)?;
                    match outcome {
                        ModeStream::Empty => null_ok(0),
                        ModeStream::Nan => OperatorResult::Indeterminate {
                            code: "nan_mode".into(),
                            detail: "mode winner is NaN, which has no ordered JSON representation"
                                .into(),
                        },
                        ModeStream::Value(o) => {
                            let (rt, val) = sort_key_to_json(o.winner)?;
                            OperatorResult::Ok {
                                result_type: rt,
                                value: Some(val),
                                tie: o.tie,
                                frequency: Some(o.frequency),
                                non_null,
                            }
                        }
                    }
                }
            }
            OperatorSpec::StringAgg { delimiter, .. } => {
                if non_null == 0 {
                    null_ok(0)
                } else {
                    let mut agg = StringAggStream::new();
                    while let Some(e) = next(&mut merger, self)? {
                        if let SortKey::Utf8(v) = e.key {
                            let grew = agg.push(&v, delimiter);
                            // The output string competes for the same budget,
                            // including its payload bytes.
                            self.memory.charge(grew).map_err(|_| {
                                PctlError::new(
                                    ErrorKind::Resource,
                                    "output_budget_exceeded",
                                    "ordered string aggregate exceeds the resident budget",
                                )
                            })?;
                        }
                    }
                    OperatorResult::Ok {
                        result_type: "utf8".into(),
                        value: Some(agg.finish().into()),
                        tie: false,
                        frequency: None,
                        non_null,
                    }
                }
            }
        };

        // Resident buffers were flushed before finalization, so there is
        // nothing left to reconcile here.
        Ok(result)
    }

    fn stats_snapshot(&self) -> ExecStats {
        ExecStats {
            runs_spilled: self.stats.runs_spilled,
            bytes_spilled: self.stats.bytes_spilled,
            groups_tracked: self.registry.groups_tracked(),
            groups_rejected_by_cap: self.registry.rejected_by_cap,
            resident_high_water: self.memory.high_water(),
            resident_budget: self.stats.resident_budget,
        }
    }

    fn spill_dir_name(&self) -> String {
        self.spill
            .dir()
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_default()
    }
}

fn null_ok(non_null: u64) -> OperatorResult {
    OperatorResult::Ok {
        result_type: "null".into(),
        value: None,
        tie: false,
        frequency: None,
        non_null,
    }
}

fn f64_result(v: f64, err: &str, non_null: u64) -> Result<OperatorResult> {
    Ok(OperatorResult::Ok {
        result_type: "f64".into(),
        value: Some(
            serde_json::Number::from_f64(v)
                .ok_or_else(|| PctlError::new(ErrorKind::Indeterminate, "non_finite_result", err))?
                .into(),
        ),
        tie: false,
        frequency: None,
        non_null,
    })
}

enum ModeStream {
    Empty,
    Nan,
    Value(ModeOutcome<SortKey>),
}

/// Single linear pass over the globally sorted feed. Ties keep the smallest
/// key (the first one encountered in ascending order) and set `tie = true`.
fn stream_mode(merger: &mut Merger, cancel: &CancellationToken) -> Result<ModeStream> {
    let mut current: Option<SortKey> = None;
    let mut cur_freq = 0u64;
    let mut best: Option<SortKey> = None;
    let mut best_freq = 0u64;
    let mut tie = false;

    while let Some((_src, e)) = merger.next(cancel)? {
        let key = e.key;
        match &current {
            Some(cur) if cur.cmp_asc(&key) == std::cmp::Ordering::Equal => {
                cur_freq += 1;
            }
            _ => {
                if let Some(k) = current.take() {
                    match cur_freq.cmp(&best_freq) {
                        std::cmp::Ordering::Greater => {
                            best_freq = cur_freq;
                            best = Some(k);
                            tie = false;
                        }
                        std::cmp::Ordering::Equal if best_freq > 0 => tie = true,
                        _ => {}
                    }
                }
                current = Some(key);
                cur_freq = 1;
            }
        }
    }
    if let Some(k) = current.take() {
        match cur_freq.cmp(&best_freq) {
            std::cmp::Ordering::Greater => {
                best_freq = cur_freq;
                best = Some(k);
                tie = false; // a strictly larger final run breaks earlier ties
            }
            std::cmp::Ordering::Equal if best_freq > 0 => tie = true,
            _ => {}
        }
    }

    match best {
        None => Ok(ModeStream::Empty),
        Some(SortKey::F64(v)) if v.is_nan() => Ok(ModeStream::Nan),
        Some(winner) => Ok(ModeStream::Value(ModeOutcome {
            winner,
            frequency: best_freq,
            tie,
        })),
    }
}

fn sort_key_to_json(k: SortKey) -> Result<(String, serde_json::Value)> {
    match k {
        SortKey::I64(v) => Ok(("i64".into(), v.into())),
        SortKey::F64(v) => Ok((
            "f64".into(),
            serde_json::Number::from_f64(v)
                .ok_or_else(|| {
                    PctlError::new(
                        ErrorKind::Indeterminate,
                        "non_finite_result",
                        "non-finite mode",
                    )
                })?
                .into(),
        )),
        SortKey::Utf8(v) => Ok(("utf8".into(), v.into())),
    }
}

/// Validate, decode, execute; cancellation surfaces as an outcome with a token.
pub fn execute_request(
    req: &QueryRequest,
    rid: &RequestId,
    config: &crate::config::Config,
    cancel: CancellationToken,
) -> Result<ExecOutcome> {
    let plan = validate::validate_request(req)?;
    let batch = TypedBatch::from_input(&req.columns)?;

    match &req.hints.resume {
        None => Executor::start(req, &plan, &batch, rid, config, cancel)?.run(0),
        Some(token) => {
            let (exec, cursor) = Executor::resume(req, &plan, &batch, rid, config, cancel, token)?;
            exec.run(cursor)
        }
    }
}
