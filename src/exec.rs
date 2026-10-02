//! Plan building and execution wiring.
//!
//! Turns a serializable [`QueryRequest`] into a composed operator tree, runs it
//! under a [`Control`] (cancellation token + optional deadline), and returns the
//! pulled batches together with resource/lifecycle [`RunStats`] and the
//! replayable diagnostic log.
//!
//! Operator order is fixed and explicit: `Scan → Sort? → Projection? → Limit?`.
//! The hash join is constructed programmatically in [`crate::validate`] and the
//! demo because it has two inputs, which a flat one-source request cannot name.

use std::path::PathBuf;
use std::sync::Arc;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};

use crate::batch::{Batch, Scalar};
use crate::cancel::{CancellationToken, Control};
use crate::diag::{RunDiag, TerminalKind};
use crate::error::{ErrorKind, QueryError, QueryResult};
use crate::fixture;
use crate::operator::Operator;
use crate::operators::{Limit, Projection, Scan, Sort};
use crate::resource::ResourceTracker;

/// Which synthetic source to scan.
#[derive(Debug, Clone, Deserialize, Serialize, PartialEq, Eq)]
#[serde(tag = "table", rename_all = "lowercase")]
pub enum Source {
    Users,
    Orders { rows: usize },
}

/// A flat, single-source query request.
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct QueryRequest {
    #[serde(default = "default_source")]
    pub source: Source,
    #[serde(default = "default_batch_size")]
    pub batch_size: usize,
    #[serde(default)]
    pub sort_keys: Vec<String>,
    /// Logical-byte budget before the sort spills. `None` → a small default
    /// that forces spilling on the larger orders fixture.
    pub sort_memory_budget_bytes: Option<u64>,
    #[serde(default)]
    pub project: Vec<String>,
    pub limit: Option<usize>,
    /// Wall-clock budget; absent means no deadline (cancellation still works).
    pub timeout_ms: Option<u64>,
}

fn default_source() -> Source {
    Source::Users
}
fn default_batch_size() -> usize {
    4
}

impl Default for QueryRequest {
    fn default() -> Self {
        Self {
            source: default_source(),
            batch_size: default_batch_size(),
            sort_keys: Vec::new(),
            sort_memory_budget_bytes: None,
            project: Vec::new(),
            limit: None,
            timeout_ms: None,
        }
    }
}

/// Description of the built plan (for diagnostics / JSON).
#[derive(Debug, Clone, Serialize)]
pub struct QueryPlan {
    pub stages: Vec<String>,
}

/// Post-run resource and lifecycle counters.
#[derive(Debug, Clone, Serialize)]
pub struct RunStats {
    pub rows_out: usize,
    pub batches_out: usize,
    pub runs_spilled: u64,
    pub spilled_bytes: u64,
    pub peak_open_files: u64,
    pub open_files_after_close: u64,
    pub buffered_bytes_after_close: u64,
    pub elapsed_ms: u128,
    pub terminal: String,
    pub error_kind: Option<String>,
}

/// Everything one execution produces. Batches are owned and stay readable even
/// when `error` is set.
pub struct RunOutput {
    pub run_id: String,
    pub batches: Vec<Batch>,
    pub error: Option<QueryError>,
    pub stats: RunStats,
    pub log: String,
    pub plan: QueryPlan,
}

/// Default spill root under the OS temp directory.
pub fn default_spill_root() -> PathBuf {
    std::env::temp_dir().join("pull-query-spill")
}

/// The small default budget deliberately forces multi-run spills on the orders
/// fixture, so the default demo exercises disk spilling.
const DEFAULT_SORT_BUDGET: u64 = 480;

/// Build the operator tree for a request. Returns the root and the plan summary.
pub fn build(
    req: &QueryRequest,
    tracker: Arc<ResourceTracker>,
    spill_root: PathBuf,
) -> QueryResult<(Box<dyn Operator>, QueryPlan)> {
    let mut stages = Vec::new();

    let (schema, rows) = match &req.source {
        Source::Users => (fixture::users_schema(), fixture::users_rows()),
        Source::Orders { rows } => (fixture::orders_schema(), fixture::orders_rows(*rows)),
    };
    let batch_size = req.batch_size.max(1);
    let batches = fixture::batches(schema.clone(), &rows, batch_size)?;
    let mut root: Box<dyn Operator> = Box::new(Scan::new("scan", schema.clone(), batches));
    stages.push(format!("scan({})", req.source_name()));

    if !req.sort_keys.is_empty() {
        let budget = req.sort_memory_budget_bytes.unwrap_or(DEFAULT_SORT_BUDGET);
        let sort = Sort::new(
            "sort",
            root,
            &req.sort_keys,
            budget,
            tracker.clone(),
            spill_root,
        )?
        .with_out_batch_rows(batch_size.max(8));
        stages.push(format!("sort(keys={:?}, budget={budget}B)", req.sort_keys));
        root = Box::new(sort);
    }

    if !req.project.is_empty() {
        let proj = Projection::new("project", root, &req.project)?;
        stages.push(format!("project({:?})", req.project));
        root = Box::new(proj);
    }

    if let Some(n) = req.limit {
        stages.push(format!("limit({n})"));
        root = Box::new(Limit::new("limit", root, n));
    }

    Ok((root, QueryPlan { stages }))
}

impl Source {
    pub fn name(&self) -> &'static str {
        match self {
            Source::Users => "users",
            Source::Orders { .. } => "orders",
        }
    }
}
impl QueryRequest {
    pub fn source_name(&self) -> &'static str {
        self.source.name()
    }
}

/// Execute a request to completion with fresh diagnostics. The returned
/// [`RunOutput`] always has close performed exactly once.
pub fn execute(req: &QueryRequest, token: CancellationToken) -> QueryResult<RunOutput> {
    let diag = RunDiag::new();
    let deadline = req
        .timeout_ms
        .map(|ms| Instant::now() + Duration::from_millis(ms));
    let ctrl = Control::new(token, deadline, diag.clone());

    let tracker = ResourceTracker::new();
    let started = Instant::now();
    let (mut root, plan) = build(req, tracker.clone(), default_spill_root())?;

    let mut batches = Vec::new();
    let mut error: Option<QueryError> = None;
    loop {
        match root.next(&ctrl) {
            Ok(Some(b)) => batches.push(b),
            Ok(None) => break,
            Err(e) => {
                error = Some(e);
                break;
            }
        }
    }
    root.shutdown(Some(&ctrl));
    let elapsed_ms = started.elapsed().as_millis();

    let terminal = match &error {
        None => {
            diag.terminate(TerminalKind::Completed, "stream exhausted");
            "completed".to_string()
        }
        Some(e) => {
            diag.terminate(
                TerminalKind::Failed(e.kind()),
                format!("{}: {}", e.kind(), e.message()),
            );
            format!("failed:{}", e.kind())
        }
    };

    let rows_out = batches.iter().map(|b| b.num_rows()).sum();
    let stats = RunStats {
        rows_out,
        batches_out: batches.len(),
        runs_spilled: tracker.spill_files_created(),
        spilled_bytes: tracker.spilled_bytes(),
        peak_open_files: tracker.peak_open_files(),
        open_files_after_close: tracker.open_files(),
        buffered_bytes_after_close: tracker.buffered_bytes(),
        elapsed_ms,
        terminal,
        error_kind: error.as_ref().map(|e| e.kind().as_str().to_string()),
    };

    Ok(RunOutput {
        run_id: diag.run_id().to_string(),
        batches,
        error,
        stats,
        log: diag.render(),
        plan,
    })
}

/// Render pulled batches as a plain text table (for CLI demos / logs).
pub fn render_table(batches: &[Batch]) -> String {
    if batches.is_empty() {
        return "<no rows>\n".to_string();
    }
    let schema = batches[0].schema().clone();
    let header: Vec<String> = schema
        .fields()
        .iter()
        .map(|(n, t)| format!("{n}:{}", t.as_str()))
        .collect();
    let mut out = String::new();
    out.push_str(&header.join("\t"));
    out.push('\n');
    for b in batches {
        let cols = (0..schema.len())
            .map(|c| b.column_scalars(c))
            .collect::<QueryResult<Vec<_>>>()
            .unwrap_or_default();
        for r in 0..b.num_rows() {
            let cells = cols
                .iter()
                .map(|col| scalar_text(&col[r]))
                .collect::<Vec<_>>();
            out.push_str(&cells.join("\t"));
            out.push('\n');
        }
    }
    out
}

pub fn scalar_text(s: &Scalar) -> String {
    match s {
        Scalar::Int(Some(v)) => v.to_string(),
        Scalar::Int(None) => "∅".to_string(),
        Scalar::Utf8(Some(v)) => v.clone(),
        Scalar::Utf8(None) => "∅".to_string(),
        Scalar::Bool(Some(v)) => v.to_string(),
        Scalar::Bool(None) => "∅".to_string(),
    }
}

/// Convert batches to JSON-serialisable rows (objects keyed by column name).
pub fn rows_to_json(batches: &[Batch]) -> QueryResult<Vec<serde_json::Value>> {
    let mut out = Vec::new();
    for b in batches {
        let schema = b.schema().clone();
        let cols = (0..schema.len())
            .map(|c| b.column_scalars(c))
            .collect::<QueryResult<Vec<_>>>()?;
        for r in 0..b.num_rows() {
            let mut obj = serde_json::Map::new();
            for (name, col) in schema.fields().iter().zip(&cols) {
                obj.insert(name.0.clone(), scalar_json(&col[r]));
            }
            out.push(serde_json::Value::Object(obj));
        }
    }
    Ok(out)
}

pub fn scalar_json(s: &Scalar) -> serde_json::Value {
    match s {
        Scalar::Int(Some(v)) => serde_json::json!(v),
        Scalar::Int(None) => serde_json::Value::Null,
        Scalar::Utf8(Some(v)) => serde_json::json!(v),
        Scalar::Utf8(None) => serde_json::Value::Null,
        Scalar::Bool(Some(v)) => serde_json::json!(v),
        Scalar::Bool(None) => serde_json::Value::Null,
    }
}

/// True if an error kind is a control signal.
pub fn is_control_kind(k: ErrorKind) -> bool {
    matches!(k, ErrorKind::Timeout | ErrorKind::Cancelled)
}
