//! Execution engine: bounded-memory ingest → external sort → streaming
//! grouped finalize, with mid-merge cancellation and resumption.
//!
//! Data flow:
//! ```text
//! typed Batches
//!   │  project (group key, per-aggregation value) records, reserving bytes
//!   ▼
//! in-memory buffer ──(budget full)──► sorted run files on disk
//!   │                                      │
//!   └──────────────► manifest.json ◄────────┘
//!                          │
//!              pass 1 merge: count non-null values per (group, agg)
//!                          │
//!              pass 2 merge: streaming selectors → GroupResult
//! ```
//! The merge passes are read-only.  If cancellation lands during either pass,
//! run files and the complete manifest remain in place, so the caller can
//! resume and obtain an identical result without re-ingesting.

pub mod aggregate;
pub mod budget;
pub mod cancel;
pub mod cells;
pub mod spill;

use std::collections::HashMap;
use std::path::PathBuf;

use crate::batch::{Batch, Column, DataType, Field};
use crate::error::{Error, ErrorKind, Result};
use crate::exec::aggregate::{ContSelector, DiscSelector, ModeSelector, StringAggSelector};
use crate::exec::budget::MemoryBudget;
use crate::exec::cancel::Token;
use crate::exec::cells::Cell;
use crate::exec::spill::{Record, ResumeToken, SpillManager};
use crate::plan::{AggOp, Plan, SortOrder};

pub use budget::RECORD_OVERHEAD_BYTES;
pub use cancel::Token as CancelToken;
pub use spill::Manifest;

/// Engine resource configuration.
#[derive(Debug, Clone)]
pub struct EngineConfig {
    /// Maximum bytes of live record data the ingest buffer may reserve.
    /// Includes the string payload buffer (see [`Cell::approx_bytes`]).
    pub memory_budget_bytes: usize,
    /// Directory under which per-query spill directories are created.
    pub spill_root: PathBuf,
    /// How often (merged records) the merge polls cancellation.
    pub cancel_check_rows: u64,
}

impl Default for EngineConfig {
    fn default() -> Self {
        Self {
            memory_budget_bytes: 64 * 1024 * 1024,
            spill_root: std::env::temp_dir().join("groupagg-spill"),
            cancel_check_rows: spill::CANCEL_CHECK_INTERVAL,
        }
    }
}

/// One output group: key values plus one nullable result per aggregation.
#[derive(Debug, Clone, PartialEq)]
pub struct GroupResult {
    pub key: Vec<Cell>,
    pub values: Vec<Option<Cell>>,
}

/// Everything a completed query returns, including resource diagnostics.
#[derive(Debug, Clone)]
pub struct QueryResult {
    pub groups: Vec<GroupResult>,
    pub stats: ExecStats,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ExecStats {
    pub ingested_rows: u64,
    pub spill_runs: usize,
    pub peak_memory_bytes: usize,
    pub groups_emitted: usize,
}

/// The engine is stateless across queries; per-query state lives on disk under
/// `spill_root/<query_id>`.
#[derive(Debug, Clone)]
pub struct Engine {
    config: EngineConfig,
}

impl Engine {
    pub fn new(config: EngineConfig) -> Self {
        Self { config }
    }

    pub fn config(&self) -> &EngineConfig {
        &self.config
    }

    /// Build the resume token for a query (used after cancellation).
    pub fn resume_token(&self, query_id: &str, plan: &Plan) -> ResumeToken {
        ResumeToken {
            dir: query_id.to_string(),
            plan_hash: plan.plan_hash(),
        }
    }

    /// Public schema/quantile validation entry point used by the API before
    /// executing anything.
    pub fn validate_schema_pub(schema: &[Field], plan: &Plan) -> Result<()> {
        Self::validate_schema(schema, plan)
    }

    /// Read a finalized query's persisted manifest without executing it.
    /// Returns `None` when no spill state exists for `query_id`.
    pub fn manifest_stats(&self, query_id: &str) -> Option<Manifest> {
        let path = self.config.spill_root.join(query_id).join("manifest.json");
        let bytes = std::fs::read(path).ok()?;
        serde_json::from_slice(&bytes).ok()
    }

    /// Validate the plan against the input schema *before* ingesting anything.
    fn validate_schema(schema: &[Field], plan: &Plan) -> Result<()> {
        plan.validate_quantiles()?;

        for name in &plan.group_by {
            let field = schema.iter().find(|f| &f.name == name).ok_or_else(|| {
                Error::invalid_request(format!("group_by column '{name}' is not in the schema"))
            })?;
            if !matches!(field.data_type, DataType::Utf8 | DataType::Int64) {
                return Err(Error::new(
                    ErrorKind::Unsupported,
                    format!(
                        "grouping on {} column '{name}' is not supported",
                        field.data_type.as_str()
                    ),
                ));
            }
        }
        for agg in &plan.aggregations {
            let field = schema.iter().find(|f| f.name == agg.input).ok_or_else(|| {
                Error::invalid_request(format!(
                    "aggregation '{}' references unknown column '{}'",
                    agg.alias, agg.input
                ))
            })?;
            if !agg.op.accepts(field.data_type) {
                return Err(Error::invalid_request(format!(
                    "operator {} cannot consume {} column '{}'",
                    agg.op.name(),
                    field.data_type.as_str(),
                    agg.input
                )));
            }
        }
        let mut seen = std::collections::HashSet::new();
        for agg in &plan.aggregations {
            if !seen.insert(agg.alias.as_str()) {
                return Err(Error::invalid_request(format!(
                    "duplicate aggregation alias '{}'",
                    agg.alias
                )));
            }
        }
        Ok(())
    }

    /// Ingest batches, spill to `spill_root/<query_id>`, and finalize.
    pub fn execute(
        &self,
        query_id: &str,
        schema: &[Field],
        batches: &[Batch],
        plan: &Plan,
        cancel: &Token,
    ) -> Result<QueryResult> {
        let token = self.prepare(query_id, schema, batches, plan, cancel)?;
        self.resume(&token, plan, cancel)
    }

    /// Phase 1: validate, ingest and durably checkpoint the sorted runs.
    /// Returns a token that phase 2 ([`Engine::resume`]) consumes.  Splitting
    /// the two phases lets callers cancel (or simulate a crash) after ingest
    /// and deterministically restart only the merge.
    pub fn prepare(
        &self,
        query_id: &str,
        schema: &[Field],
        batches: &[Batch],
        plan: &Plan,
        cancel: &Token,
    ) -> Result<ResumeToken> {
        Self::validate_schema(schema, plan)?;

        let dir = self.config.spill_root.join(query_id);
        if dir.exists() {
            return Err(Error::new(
                ErrorKind::InvalidRequest,
                format!("query id '{query_id}' already has spill state; resume or use a new id"),
            ));
        }
        let budget = MemoryBudget::new(self.config.memory_budget_bytes);
        let mut mgr = SpillManager::create(dir, plan.plan_hash(), plan.group_by.len())?;
        self.ingest(schema, batches, plan, &budget, &mut mgr, cancel)?;
        mgr.observe_peak(budget.peak());
        mgr.checkpoint(true)?;
        Ok(mgr.resume_token())
    }

    /// Resume a query whose merge phase was cancelled.  Runs files and the
    /// complete manifest must already exist.
    pub fn resume(&self, token: &ResumeToken, plan: &Plan, cancel: &Token) -> Result<QueryResult> {
        plan.validate_quantiles()?;
        if token.plan_hash != plan.plan_hash() {
            return Err(Error::new(
                ErrorKind::InvalidResume,
                "resume plan does not match the plan hash bound to the spill state",
            ));
        }
        let (mgr, manifest) = SpillManager::resume(&self.config.spill_root, token)?;
        if manifest.key_arity != plan.group_by.len() {
            return Err(Error::new(
                ErrorKind::InvalidResume,
                "resumed plan has a different group_by arity than the spilled state",
            ));
        }
        self.finalize(&mgr, plan, cancel)
    }

    fn ingest(
        &self,
        schema: &[Field],
        batches: &[Batch],
        plan: &Plan,
        budget: &MemoryBudget,
        mgr: &mut SpillManager,
        cancel: &Token,
    ) -> Result<()> {
        let key_indexes: Vec<usize> = plan
            .group_by
            .iter()
            .map(|name| {
                schema.iter().position(|f| &f.name == name).ok_or_else(|| {
                    Error::invalid_request(format!("group column '{name}' vanished from schema"))
                })
            })
            .collect::<Result<_>>()?;
        let agg_columns: Vec<usize> = plan
            .aggregations
            .iter()
            .map(|a| {
                schema
                    .iter()
                    .position(|f| f.name == a.input)
                    .ok_or_else(|| {
                        Error::invalid_request(format!("aggregation column '{}' vanished", a.input))
                    })
            })
            .collect::<Result<_>>()?;

        let mut buffer: Vec<Record> = Vec::new();

        for batch in batches {
            cancel.check()?;
            for row in 0..batch.row_count() {
                let key: Vec<Cell> = key_indexes
                    .iter()
                    .map(|&i| Cell::from_column(&batch.columns()[i], row))
                    .collect();
                // One stable sequence per source row, shared by every
                // aggregation record projected from it.
                let seq = mgr.next_sequence();
                for (agg_idx, &col_idx) in agg_columns.iter().enumerate() {
                    let col: &Column = &batch.columns()[col_idx];
                    let value = Cell::from_column(col, row);
                    let desc = matches!(
                        plan.aggregations[agg_idx].op,
                        AggOp::StringAgg {
                            order: SortOrder::Desc,
                            ..
                        }
                    );
                    let record = Record {
                        key: key.clone(),
                        agg: agg_idx as u32,
                        value,
                        desc,
                        seq,
                    };
                    let bytes = record.approx_bytes();
                    reserve_or_spill(budget, mgr, &mut buffer, record, bytes, cancel)?;
                }
            }
        }

        if !buffer.is_empty() {
            let (meta, released) = mgr.write_run(std::mem::take(&mut buffer), cancel)?;
            budget.shrink(released);
            mgr.record_run(meta);
        }
        Ok(())
    }

    fn finalize(&self, mgr: &SpillManager, plan: &Plan, cancel: &Token) -> Result<QueryResult> {
        // Pass 1: count non-null values per (group, agg).
        let counts = self.count_nonnull(mgr, cancel)?;

        // Pass 2: stream groups through the typed selectors.
        let mut merge = mgr.open_merge(self.config.cancel_check_rows)?;
        let mut groups: Vec<GroupResult> = Vec::new();
        let mut current_key: Option<Vec<Cell>> = None;
        let mut selectors: Vec<Box<dyn Selector>> = Vec::new();

        while let Some(record) = merge.next(cancel)? {
            if current_key.as_ref() != Some(&record.key) {
                if let Some(key) = current_key.take() {
                    groups.push(finish_group(key, &mut selectors));
                }
                current_key = Some(record.key.clone());
                selectors = build_selectors(plan, &record.key, &counts)?;
            }
            let sel = &mut selectors[record.agg as usize];
            sel.push(&record.value);
        }
        if let Some(key) = current_key {
            groups.push(finish_group(key, &mut selectors));
        }
        // SQL scalar aggregation: no GROUP BY over zero rows still emits one
        // row of NULLs.
        if groups.is_empty() && plan.group_by.is_empty() {
            let mut null_selectors = build_selectors(plan, &[], &counts)?;
            groups.push(finish_group(Vec::new(), &mut null_selectors));
        }
        let groups_emitted = groups.len();

        Ok(QueryResult {
            groups,
            stats: ExecStats {
                ingested_rows: mgr.ingested_rows(),
                spill_runs: mgr.run_count(),
                // The merge keeps no raw-value group buffer; the high-water
                // mark recorded during ingest is the relevant figure.
                peak_memory_bytes: mgr.peak_memory_bytes(),
                groups_emitted,
            },
        })
    }

    fn count_nonnull(
        &self,
        mgr: &SpillManager,
        cancel: &Token,
    ) -> Result<HashMap<Vec<Cell>, Vec<u64>>> {
        let mut merge = mgr.open_merge(self.config.cancel_check_rows)?;
        // Bounded by distinct (group × agg) — the result cardinality — never
        // by the number of raw rows.  Raw value data stays on disk.
        let mut counts: HashMap<Vec<Cell>, Vec<u64>> = HashMap::new();
        while let Some(record) = merge.next(cancel)? {
            let entry = counts.entry(record.key).or_default();
            if entry.len() <= record.agg as usize {
                entry.resize(record.agg as usize + 1, 0);
            }
            if !record.value.is_null() {
                entry[record.agg as usize] += 1;
            }
        }
        Ok(counts)
    }
}

fn reserve_or_spill(
    budget: &MemoryBudget,
    mgr: &mut SpillManager,
    buffer: &mut Vec<Record>,
    record: Record,
    bytes: usize,
    cancel: &Token,
) -> Result<()> {
    match budget.try_grow(bytes) {
        Ok(()) => {
            buffer.push(record);
            Ok(())
        }
        Err(e) if e.kind == ErrorKind::BudgetExceeded => {
            if buffer.is_empty() {
                // A single record exceeds the whole budget; no run size can help.
                return Err(e);
            }
            let (meta, released) = mgr.write_run(std::mem::take(buffer), cancel)?;
            budget.shrink(released);
            mgr.record_run(meta);
            budget.try_grow(bytes)?;
            buffer.push(record);
            Ok(())
        }
        Err(e) => Err(e),
    }
}

trait Selector {
    fn push(&mut self, value: &Cell);
    fn finish(self: Box<Self>) -> Option<Cell>;
}

impl Selector for ContSelector {
    fn push(&mut self, value: &Cell) {
        ContSelector::push(self, value)
    }
    fn finish(self: Box<Self>) -> Option<Cell> {
        ContSelector::finish(*self)
    }
}

impl Selector for DiscSelector {
    fn push(&mut self, value: &Cell) {
        DiscSelector::push(self, value)
    }
    fn finish(self: Box<Self>) -> Option<Cell> {
        DiscSelector::finish(*self)
    }
}

impl Selector for ModeSelector {
    fn push(&mut self, value: &Cell) {
        ModeSelector::push(self, value)
    }
    fn finish(self: Box<Self>) -> Option<Cell> {
        ModeSelector::finish(*self)
    }
}

impl Selector for StringAggSelector {
    fn push(&mut self, value: &Cell) {
        StringAggSelector::push(self, value)
    }
    fn finish(self: Box<Self>) -> Option<Cell> {
        StringAggSelector::finish(*self)
    }
}

fn build_selectors(
    plan: &Plan,
    key: &[Cell],
    counts: &HashMap<Vec<Cell>, Vec<u64>>,
) -> Result<Vec<Box<dyn Selector>>> {
    let group_counts = counts.get(key);
    plan.aggregations
        .iter()
        .enumerate()
        .map(|(i, agg)| {
            let n = group_counts.and_then(|v| v.get(i).copied()).unwrap_or(0);
            let selector: Box<dyn Selector> = match agg.op {
                AggOp::PercentileCont { q } => Box::new(ContSelector::new(n as usize, q)),
                AggOp::PercentileDisc { q } => Box::new(DiscSelector::new(n as usize, q)),
                AggOp::Mode => Box::new(ModeSelector::new()),
                AggOp::StringAgg { ref delimiter, .. } => {
                    Box::new(StringAggSelector::new(delimiter.clone()))
                }
            };
            Ok(selector)
        })
        .collect()
}

fn finish_group(key: Vec<Cell>, selectors: &mut Vec<Box<dyn Selector>>) -> GroupResult {
    let values = std::mem::take(selectors)
        .into_iter()
        .map(|s| s.finish())
        .collect();
    GroupResult { key, values }
}
