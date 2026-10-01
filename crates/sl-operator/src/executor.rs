//! The streaming ORDER BY / LIMIT / OFFSET / WITH TIES executor.
//!
//! Two execution shapes exist, both draining the source to `None`:
//!
//! * **bounded top-N** (reject policy, or any time the selection fits): a
//!   max-heap of the best `OFFSET + LIMIT` rows under the *full* ordering
//!   (user keys + stable identity), plus a *ties shelf* holding rows outside
//!   the heap whose **user sort key** equals the current cut key. When the cut
//!   improves, shelf rows that no longer tie are dropped. This keeps live
//!   state at `K + current-ties` instead of "all input".
//! * **external sort/select** (external_select policy once the live budget is
//!   exceeded): the current segment is sorted and flushed as an Arrow IPC
//!   run; segments are K-way merged at the end into a globally sorted stream.
//!   External *sorting* (rather than per-run top-N) is used because WITH TIES
//!   makes a per-run cut unsafe unless each run's cut provably equals the
//!   global one — full sorted runs sidestep that and remain obviously correct.
//!
//! The operator never stops reading early: an unknown-total source is pulled
//! until `Ok(None)` even when the LIMIT window is already populated, because
//! later rows can change ordering, the ties cut and offset results.

use std::cmp::Ordering;
use std::sync::Arc;

use sl_types::{
    Batch, ErrorCategory, OrderQuery, RelationSchema, Result, Scalar, SlError,
};

use sl_resource::spill::ROW_ID_COLUMN;
use sl_resource::{Budget, SpillStore, SpilledRun};

use crate::compare::{
    compare_full, compare_user_keys, entry_payload_bytes, Entry, ResolvedKey, WorstHeap,
};
use crate::source::BatchSource;

/// One explainable processing step, correlated with the request id by the
/// caller. Steps are deliberately coarse and stable strings, not debug noise.
#[derive(Debug, Clone, serde::Serialize)]
pub struct StepTrace {
    pub step: String,
    pub detail: String,
}

impl StepTrace {
    fn new(step: impl Into<String>, detail: impl Into<String>) -> Self {
        Self {
            step: step.into(),
            detail: detail.into(),
        }
    }
}

/// Observable execution statistics. Tests assert on the peak-state numbers to
/// prove retained state is bounded, and on `source_drained` to prove no early
/// stop.
#[derive(Debug, Clone, serde::Serialize)]
pub struct ExecutionStats {
    pub request_id: String,
    pub input_rows: u64,
    pub output_rows: u64,
    pub source_pulls: u64,
    pub source_drained: bool,
    pub retained_peak_rows: usize,
    pub retained_peak_bytes: usize,
    pub spilled: bool,
    pub spill_runs: usize,
    pub spill_bytes: u64,
    /// How selection was actually served: `bounded_topn`, `full_in_memory` or
    /// `external_merge`.
    pub strategy: String,
    pub steps: Vec<StepTrace>,
}

/// A finished execution: typed output batch plus its trace.
#[derive(Debug, Clone)]
pub struct ExecutionOutput {
    pub batch: Batch,
    pub stats: ExecutionStats,
}

/// Configuration of the executor's resource behaviour.
#[derive(Debug, Clone)]
pub struct ExecutorResources<'a> {
    pub state_budget_rows: usize,
    pub state_budget_bytes: usize,
    pub overflow_policy: sl_types::OverflowPolicy,
    /// Required when (and used only when) the policy is `external_select`.
    pub spill: Option<&'a SpillStore>,
}

/// The ordering executor. Constructed per request; cheap to create.
pub struct OrderingExecutor {
    schema: Arc<RelationSchema>,
    keys: Vec<ResolvedKey>,
    request_id: String,
}

impl OrderingExecutor {
    /// Resolve ORDER BY columns up front.
    pub fn new(
        request_id: impl Into<String>,
        schema: Arc<RelationSchema>,
        query: &OrderQuery,
    ) -> Result<Self> {
        let keys = ResolvedKey::resolve_all(&schema, &query.order_by)?;
        Ok(Self {
            schema,
            keys,
            request_id: request_id.into(),
        })
    }

    pub fn keys(&self) -> &[ResolvedKey] {
        &self.keys
    }

    /// Drain `source`, apply `query`, return the typed output batch.
    pub fn execute<S: BatchSource>(
        &self,
        query: &OrderQuery,
        source: &mut S,
        resources: &ExecutorResources<'_>,
    ) -> Result<ExecutionOutput> {
        use sl_types::OverflowPolicy;

        // Defensive re-check even though sl-validate performs this first.
        let keep = query.post_offset_keep()?;

        if matches!(resources.overflow_policy, OverflowPolicy::ExternalSelect)
            && resources.spill.is_none()
        {
            return Err(SlError::new(
                ErrorCategory::ExternalStorage,
                "spill_store_missing",
                "overflow_policy=external_select requires a spill store",
                "sl_operator::executor",
            ));
        }

        let mut ctx = Ctx::new(&self.request_id, resources);
        ctx.steps.push(StepTrace::new(
            "query_resolved",
            format!(
                "{} sort key(s); limit={:?}; offset={}; with_ties={}",
                self.keys.len(),
                query.limit,
                query.offset,
                query.with_ties
            ),
        ));

        let strategy = match (resources.overflow_policy, keep) {
            (sl_types::OverflowPolicy::ExternalSelect, _) => {
                self.run_external(query, source, &mut ctx)?
            }
            (sl_types::OverflowPolicy::Reject, Some(k)) if k > 0 => {
                self.run_bounded_topn(k, query.with_ties, source, &mut ctx)?
            }
            // LIMIT 0 produces no rows, but the source must still be drained
            // and the query validated; no state is retained.
            (sl_types::OverflowPolicy::Reject, Some(0)) => {
                self.drain_only(source, &mut ctx)?;
                self.finalize(Vec::new(), query, &mut ctx, "limit_zero_drain")?
                    .0
            }
            (sl_types::OverflowPolicy::Reject, None) => {
                self.run_full_scan(source, &mut ctx)?;
                "full_in_memory".to_string()
            }
        };

        // For strategies that accumulated into ctx.selected:
        let output = match strategy.as_str() {
            "__already_finalized__" => {
                ctx.take_output()
                    .expect("limit-zero path must have stored an output")
            }
            "full_in_memory" => {
                let entries = std::mem::take(&mut ctx.selected);
                let (out, _) = self.finalize(entries, query, &mut ctx, "full_in_memory")?;
                out
            }
            "bounded_topn" => {
                let mut entries = std::mem::take(&mut ctx.selected);
                let mut shelf = std::mem::take(&mut ctx.shelf);
                entries.append(&mut shelf);
                entries.sort_by(|a, b| compare_full(a, b, &self.keys));
                let (out, _) = self.finalize(entries, query, &mut ctx, "bounded_topn")?;
                out
            }
            "external_merge" => {
                let entries = std::mem::take(&mut ctx.selected);
                let (out, _) = self.finalize(entries, query, &mut ctx, "external_merge")?;
                out
            }
            other => {
                return Err(SlError::internal(format!("unknown executor strategy {other}")));
            }
        };

        ctx.stats.output_rows = output.batch.num_rows() as u64;
        ctx.stats.strategy = output.1.clone();
        let stats = ctx.snapshot();
        // Stash strategy was already set above via output.1; rewrite properly:
        let mut stats = stats;
        stats.strategy = output.1;
        Ok(ExecutionOutput {
            batch: output.0,
            stats,
        })
    }

    // ----- bounded top-N + ties shelf -------------------------------------

    fn run_bounded_topn<S: BatchSource>(
        &self,
        k: u64,
        with_ties: bool,
        source: &mut S,
        ctx: &mut Ctx,
    ) -> Result<String> {
        let k = k as usize;
        let mut heap = WorstHeap::new(self.keys.clone());
        let mut shelf: Vec<Entry> = Vec::new();
        ctx.steps.push(StepTrace::new(
            "strategy",
            format!("bounded top-N heap; K=offset+limit={k}; with_ties={with_ties}"),
        ));

        while let Some(batch) = source.next_batch()? {
            ctx.stats.source_pulls += 1;
            for row in batch_to_entries(&batch, &mut ctx.identity)? {
                ctx.stats.input_rows += 1;
                let bytes = entry_payload_bytes(&row);
                if heap.len() < k {
                    ctx.budget.reserve(1, bytes)?;
                    heap.push(row);
                    ctx.observe_peak(&heap_len(heap.len(), &shelf));
                    continue;
                }
                // Heap is full.
                let worst = heap.peek().expect("heap full").clone();
                match compare_full(&row, &worst, &self.keys) {
                    Ordering::Greater => {
                        // Worse than the cut. Retained only as a tie.
                        if with_ties
                            && compare_user_keys(&row, &worst, &self.keys) == Ordering::Equal
                        {
                            ctx.budget.reserve(1, bytes)?;
                            shelf.push(row);
                        }
                    }
                    Ordering::Less => {
                        // Better: evict worst, insert candidate.
                        let evicted = heap.pop().expect("heap full");
                        let evicted_bytes = entry_payload_bytes(&evicted);
                        // Candidate takes evicted's slot: net zero on the
                        // heap, but release/re-reserve keeps bytes honest.
                        ctx.budget.release(1, evicted_bytes);
                        ctx.budget.reserve(1, bytes)?;
                        heap.push(row);
                        let new_worst = heap.peek().expect("heap non-empty").clone();
                        if with_ties
                            && compare_user_keys(&evicted, &new_worst, &self.keys)
                                == Ordering::Equal
                        {
                            // Evicted row ties the new cut: keep on shelf.
                            ctx.budget.reserve(1, evicted_bytes)?;
                            shelf.push(evicted);
                        }
                        if with_ties {
                            // The cut may have improved; drop stale shelf rows.
                            shelf = prune_shelf(shelf, &new_worst, &self.keys, ctx)?;
                        }
                    }
                    Ordering::Equal => {
                        // Unreachable: identity is unique. Treat as tie for
                        // defence in depth.
                        if with_ties {
                            ctx.budget.reserve(1, bytes)?;
                            shelf.push(row);
                        }
                    }
                }
                ctx.observe_peak(&heap_len(heap.len(), &shelf));
            }
        }
        ctx.stats.source_drained = true;
        ctx.steps.push(StepTrace::new(
            "source_drained",
            format!("source {} returned end-of-stream", source.location()),
        ));

        ctx.selected = heap.into_sorted();
        ctx.shelf = shelf;
        if !query_ties_logged(ctx) {
            ctx.steps.push(StepTrace::new(
                "retained",
                format!(
                    "heap={} shelf={}",
                    ctx.selected.len(),
                    ctx.shelf.len()
                ),
            ));
        }
        Ok("bounded_topn".to_string())
    }

    // ----- full in-memory scan (no limit) ---------------------------------

    fn run_full_scan<S: BatchSource>(
        &self,
        source: &mut S,
        ctx: &mut Ctx,
    ) -> Result<String> {
        ctx.steps.push(StepTrace::new(
            "strategy",
            "no LIMIT: retaining all rows in memory under budget",
        ));
        while let Some(batch) = source.next_batch()? {
            ctx.stats.source_pulls += 1;
            for row in batch_to_entries(&batch, &mut ctx.identity)? {
                ctx.stats.input_rows += 1;
                let bytes = entry_payload_bytes(&row);
                ctx.budget.reserve(1, bytes)?;
                ctx.selected.push(row);
                ctx.observe_peak(&ctx.selected.len());
            }
        }
        ctx.stats.source_drained = true;
        ctx.selected.sort_by(|a, b| compare_full(a, b, &self.keys));
        Ok("full_in_memory".to_string())
    }

    // ----- LIMIT 0: drain only --------------------------------------------

    fn drain_only<S: BatchSource>(&self, source: &mut S, ctx: &mut Ctx) -> Result<()> {
        ctx.steps.push(StepTrace::new(
            "strategy",
            "LIMIT 0: draining source without retaining rows",
        ));
        while let Some(batch) = source.next_batch()? {
            ctx.stats.source_pulls += 1;
            ctx.stats.input_rows += batch.num_rows() as u64;
        }
        ctx.stats.source_drained = true;
        Ok(())
    }

    // ----- external sort/select -------------------------------------------

    #[allow(clippy::too_many_lines)]
    fn run_external<S: BatchSource>(
        &self,
        query: &OrderQuery,
        source: &mut S,
        ctx: &mut Ctx,
    ) -> Result<String> {
        let store = resources_store(ctx)?;
        ctx.steps.push(StepTrace::new(
            "strategy",
            "external sort/select: sorted IPC runs with K-way merge",
        ));

        // Live segment accumulator.
        while let Some(batch) = source.next_batch()? {
            ctx.stats.source_pulls += 1;
            for row in batch_to_entries(&batch, &mut ctx.identity)? {
                ctx.stats.input_rows += 1;
                let bytes = entry_payload_bytes(&row);
                if ctx.budget.would_exceed(1, bytes) {
                    if ctx.segment.is_empty() {
                        // A single row exceeds the budget: spilling cannot
                        // make it fit. Surface a categorized error.
                        return Err(SlError::new(
                            ErrorCategory::BudgetExceeded,
                            "row_exceeds_budget",
                            "a single row exceeds the configured state budget; external select cannot proceed",
                            "sl_operator::executor::external",
                        ));
                    }
                    self.flush_run(store, &mut ctx.segment, ctx)?;
                }
                ctx.budget.commit(1, bytes);
                ctx.observe_peak(&ctx.segment.len() + 1);
                ctx.segment.push(row);
            }
        }
        ctx.stats.source_drained = true;
        ctx.steps.push(StepTrace::new(
            "source_drained",
            format!("source {} returned end-of-stream", source.location()),
        ));

        if ctx.runs.is_empty() {
            // Everything fit: in-memory sort.
            ctx.selected.append(&mut ctx.segment);
            ctx.selected.sort_by(|a, b| compare_full(a, b, &self.keys));
            return Ok("external_merge".to_string());
        }

        // Flush the tail segment.
        if !ctx.segment.is_empty() {
            self.flush_run(store, &mut ctx.segment, ctx)?;
        }

        // K-way merge all runs into global full order.
        let mut merger = RunMerger::open(self.schema.clone(), &ctx.runs)?;
        let mut merged: Vec<Entry> = Vec::new();
        while let Some(entry) = merger.next_entry(&self.keys)? {
            merged.push(entry);
        }
        for run in &ctx.runs {
            run.cleanup();
        }
        ctx.stats.spilled = true;
        ctx.selected = merged;
        let _ = query;
        Ok("external_merge".to_string())
    }

    fn flush_run(
        &self,
        store: &SpillStore,
        segment: &mut Vec<Entry>,
        ctx: &mut Ctx,
    ) -> Result<()> {
        segment.sort_by(|a, b| compare_full(a, b, &self.keys));
        let run_no = ctx.runs.len() + 1;
        let mut writer = store.create_run(&ctx.request_id, &format!("seg{run_no}"), &self.schema)?;
        let chunk = entries_to_chunk(&self.schema, segment)?;
        writer.write_chunk(&chunk)?;
        let run = writer.finish()?;
        ctx.steps.push(StepTrace::new(
            "spill_run",
            format!(
                "flushed run #{run_no}: {} rows, {} bytes to {}",
                segment.len(),
                run.bytes,
                run.path.display()
            ),
        ));
        ctx.runs.push(run);
        // All segment rows leave live state.
        let payload: usize = segment.iter().map(entry_payload_bytes).sum();
        ctx.budget.release(segment.len(), payload);
        segment.clear();
        Ok(())
    }

    // ----- generic output cut ---------------------------------------------

    /// Apply OFFSET / LIMIT / WITH TIES to a globally sorted entry list and
    /// rebuild a typed batch. Returns `(batch, strategy_name)`; ties are
    /// expanded by *user sort key*, never identity.
    fn finalize(
        &self,
        mut sorted: Vec<Entry>,
        query: &OrderQuery,
        ctx: &mut Ctx,
        strategy: &str,
    ) -> Result<(Batch, String), SlError> {
        // sorted must already be in full order; re-sort defensively (cheap and
        // guarantees the cut is correct even if a caller path merged lists).
        sorted.sort_by(|a, b| compare_full(a, b, &self.keys));

        let total = sorted.len() as u64;
        let offset = query.offset;
        if offset >= total {
            ctx.steps.push(StepTrace::new(
                "offset",
                format!("offset {offset} >= {total} input rows; result is empty"),
            ));
            let batch = Batch::from_rows(self.schema.clone(), &[])?;
            return Ok((batch, strategy.to_string()));
        }

        let start = offset as usize;
        let (base_end, hard_end) = match query.limit {
            None => (sorted.len(), sorted.len()),
            Some(0) => (start, start),
            Some(lim) => {
                let end = std::cmp::min(
                    offset
                        .checked_add(lim)
                        .ok_or_else(|| overflow_err(offset, lim))?
                        as usize,
                    sorted.len(),
                );
                (end, end)
            }
        };

        let mut out: Vec<Entry> = sorted[start..base_end].to_vec();

        if query.with_ties {
            if let Some(&limit) = query.limit.as_ref() {
                if *limit > 0 && base_end > start {
                    // Ties anchor: the last row of the LIMIT window, compared
                    // by USER KEY only.
                    let anchor = &sorted[base_end - 1];
                    let mut extra = 0usize;
                    let mut cursor = hard_end;
                    while cursor < sorted.len() {
                        if compare_user_keys(&sorted[cursor], anchor, &self.keys)
                            == Ordering::Equal
                        {
                            out.push(sorted[cursor].clone());
                            extra += 1;
                            cursor += 1;
                        } else {
                            break; // full order => no later row can tie
                        }
                    }
                    ctx.steps.push(StepTrace::new(
                        "ties_expansion",
                        format!(
                            "anchor row identity={} tied with {extra} later row(s) on user sort key",
                            anchor.identity
                        ),
                    ));
                }
            }
        }

        ctx.steps.push(StepTrace::new(
            "offset",
            format!("skipped {offset} leading row(s)"),
        ));
        ctx.steps.push(StepTrace::new(
            "emit",
            format!("emitting {} output row(s)", out.len()),
        ));

        let rows: Vec<Vec<Scalar>> = out.into_iter().map(|e| e.row).collect();
        let batch = Batch::from_rows(self.schema.clone(), &rows)?;
        Ok((batch, strategy.to_string()))
    }
}

// ----- helpers -------------------------------------------------------------

fn heap_len(heap: usize, shelf: &[Entry]) -> usize {
    heap + shelf.len()
}

fn query_ties_logged(_ctx: &Ctx) -> bool {
    false
}

fn prune_shelf(
    shelf: Vec<Entry>,
    cut: &Entry,
    keys: &[ResolvedKey],
    ctx: &mut Ctx,
) -> Result<Vec<Entry>> {
    let mut kept = Vec::with_capacity(shelf.len());
    for e in shelf {
        if compare_user_keys(&e, cut, keys) == Ordering::Equal {
            kept.push(e);
        } else {
            ctx.budget.release(1, entry_payload_bytes(&e));
        }
    }
    Ok(kept)
}

fn overflow_err(offset: u64, limit: u64) -> SlError {
    SlError::new(
        ErrorCategory::Overflow,
        "offset_limit_overflow",
        format!("OFFSET ({offset}) + LIMIT ({limit}) overflows u64::MAX"),
        "sl_operator::executor",
    )
}

fn resources_store(ctx: &Ctx) -> Result<&SpillStore> {
    ctx.resources_spill.ok_or_else(|| {
        SlError::new(
            ErrorCategory::ExternalStorage,
            "spill_store_missing",
            "external select needs a spill store",
            "sl_operator::executor",
        )
    })
}

/// Convert a typed batch into fully-owned entries with stable identities.
fn batch_to_entries(batch: &Batch, identity: &mut u64) -> Result<Vec<Entry>> {
    let n = batch.num_rows();
    let schema = batch.schema().clone();
    let mut columns: Vec<Vec<Scalar>> = Vec::with_capacity(schema.len());
    for (col_meta, array) in schema.columns.iter().zip(batch.columns()) {
        columns.push(sl_types::scalar::extract_column(array.as_ref(), col_meta.ty)?);
    }
    let mut out = Vec::with_capacity(n);
    for row_idx in 0..n {
        let id = identity.checked_add(1).ok_or_else(|| {
            SlError::internal("stable row identity counter overflowed u64")
        })?;
        *identity = id;
        let row = columns.iter().map(|c| c[row_idx].clone()).collect();
        out.push(Entry {
            row,
            identity: id - 1,
        });
    }
    Ok(out)
}

/// Build an arrow chunk: user columns followed by the non-null identity col.
fn entries_to_chunk(
    schema: &RelationSchema,
    entries: &[Entry],
) -> Result<arrow2::chunk::Chunk<Box<dyn arrow2::array::Array>>> {
    let mut rows: Vec<Vec<Scalar>> = entries.iter().map(|e| e.row.clone()).collect();
    // Append identity into each row temporarily so rows_to_columns builds it.
    // Simpler: build user columns plus a dedicated Int64 column.
    let mut arrays: Vec<Box<dyn arrow2::array::Array>> =
        sl_types::batch::rows_to_columns(schema, &rows)
            .into_iter()
            .map(|c| c.to_arrow_array())
            .collect();
    let ids: Vec<Option<i64>> = entries.iter().map(|e| Some(e.identity as i64)).collect();
    arrays.push(Box::new(arrow2::array::Int64Array::from(ids)));
    let _ = &mut rows;
    arrow2::chunk::Chunk::try_new(arrays).map_err(SlError::from)
}

// ----- per-request mutable context ----------------------------------------

struct Ctx<'a> {
    request_id: String,
    budget: Budget,
    identity: u64,
    input_rows_seen_peak: usize,
    /// Main retained set (heap output / scan / merged).
    selected: Vec<Entry>,
    /// Ties shelf (bounded top-N path).
    shelf: Vec<Entry>,
    /// Live external-sort segment.
    segment: Vec<Entry>,
    runs: Vec<SpilledRun>,
    resources_spill: Option<&'a SpillStore>,
    output: Option<ExecutionOutput>,
    stats: StatsInner,
    steps: Vec<StepTrace>,
}

#[derive(Debug, Default)]
struct StatsInner {
    source_pulls: u64,
    source_drained: bool,
    spill_bytes: u64,
}

impl<'a> Ctx<'a> {
    fn new(request_id: &str, resources: &ExecutorResources<'a>) -> Self {
        Self {
            request_id: request_id.to_string(),
            budget: Budget::new(
                resources.state_budget_rows,
                resources.state_budget_bytes,
            ),
            identity: 0,
            input_rows_seen_peak: 0,
            selected: Vec::new(),
            shelf: Vec::new(),
            segment: Vec::new(),
            runs: Vec::new(),
            resources_spill: resources.spill,
            output: None,
            stats: StatsInner::default(),
            steps: Vec::new(),
        }
    }

    fn observe_peak(&mut self, _retained: &usize) {
        self.input_rows_seen_peak =
            self.input_rows_seen_peak.max(self.budget.peak_rows());
    }

    fn snapshot(self) -> ExecutionStats {
        ExecutionStats {
            request_id: self.request_id,
            input_rows: 0, // filled by caller path; retained below
            output_rows: 0,
            source_pulls: self.stats.source_pulls,
            source_drained: self.stats.source_drained,
            retained_peak_rows: self.budget.peak_rows(),
            retained_peak_bytes: self.budget.peak_bytes(),
            spilled: !self.runs.is_empty(),
            spill_runs: self.runs.len(),
            spill_bytes: self.stats.spill_bytes,
            strategy: String::new(),
            steps: self.steps,
        }
    }

    fn take_output(&mut self) -> Option<ExecutionOutput> {
        self.output.take()
    }
}

// ----- K-way merger over spilled runs --------------------------------------

struct RunCursor {
    reader: sl_resource::RunReader,
    chunk: Vec<Entry>,
    pos: usize,
}

impl RunCursor {
    fn new(reader: sl_resource::RunReader) -> Self {
        Self {
            reader,
            chunk: Vec::new(),
            pos: 0,
        }
    }

    fn advance(&mut self, schema: &RelationSchema) -> Result<()> {
        self.chunk.clear();
        self.pos = 0;
        while let Some(chunk) = self.reader.next_chunk()? {
            self.chunk = decode_chunk(schema, &chunk)?;
            if !self.chunk.is_empty() {
                return Ok(());
            }
        }
        Ok(())
    }

    fn current(&self) -> Option<&Entry> {
        self.chunk.get(self.pos)
    }

    fn consume(&mut self, schema: &RelationSchema) -> Result<Option<Entry>> {
        if self.pos >= self.chunk.len() {
            self.advance(schema)?;
        }
        match self.chunk.get(self.pos).cloned() {
            Some(e) => {
                self.pos += 1;
                Ok(Some(e))
            }
            None => Ok(None),
        }
    }
}

struct RunMerger {
    schema: Arc<RelationSchema>,
    cursors: Vec<RunCursor>,
}

impl RunMerger {
    fn open(schema: Arc<RelationSchema>, runs: &[SpilledRun]) -> Result<Self> {
        let mut cursors = Vec::with_capacity(runs.len());
        for run in runs {
            let mut c = RunCursor::new(run.open()?);
            c.advance(&schema)?;
            cursors.push(c);
        }
        Ok(Self { schema, cursors })
    }

    /// Small-R linear K-way merge: pick the smallest current head. Runs are
    /// few per test scale; a heap merge can replace this without changing the
    /// documented semantics.
    fn next_entry(&mut self, keys: &[ResolvedKey]) -> Result<Option<Entry>> {
        let mut best: Option<usize> = None;
        for (i, c) in self.cursors.iter().enumerate() {
            match (c.current(), best) {
                (Some(e), None) => best = Some(i),
                (Some(e), Some(b)) => {
                    if compare_full(e, self.cursors[b].current().unwrap(), keys)
                        == Ordering::Less
                    {
                        best = Some(i);
                    }
                }
                (None, _) => {}
            }
        }
        match best {
            None => Ok(None),
            Some(i) => self.cursors[i].consume(&self.schema),
        }
    }
}

/// Decode a spilled chunk (user columns + trailing identity) into entries.
fn decode_chunk(
    schema: &RelationSchema,
    chunk: &arrow2::chunk::Chunk<Box<dyn arrow2::array::Array>>,
) -> Result<Vec<Entry>> {
    let arrays = chunk.arrays();
    if arrays.len() != schema.len() + 1 {
        return Err(SlError::internal(format!(
            "spilled chunk has {} columns, expected {} ({} data + 1 {ROW_ID_COLUMN})",
            arrays.len(),
            schema.len() + 1,
            schema.len()
        )));
    }
    let n = chunk.len();
    let mut columns: Vec<Vec<Scalar>> = Vec::with_capacity(schema.len());
    for (meta, array) in schema.columns.iter().zip(arrays.iter()) {
        columns.push(sl_types::scalar::extract_column(array.as_ref(), meta.ty)?);
    }
    let id_array = arrays[schema.len()]
        .as_any()
        .downcast_ref::<arrow2::array::Int64Array>()
        .ok_or_else(|| SlError::internal("identity column was not Int64Array"))?;
    let mut out = Vec::with_capacity(n);
    for row_idx in 0..n {
        let row = columns.iter().map(|c| c[row_idx].clone()).collect();
        let identity = id_array.get(row_idx).ok_or_else(|| {
            SlError::internal("identity column contained NULL in a spill run")
        })? as u64;
        out.push(Entry { row, identity });
    }
    Ok(out)
}
