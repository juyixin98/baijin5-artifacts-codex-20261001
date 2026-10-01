//! Differential cross-check runner.
//!
//! For every case the engine result is compared, row by row, with the
//! independent scalar [`oracle`]. The runner also verifies the live/deleted
//! sets agree and that each live row is classified exactly once. Failures are
//! collected (not panicked on mid-run) and rendered with the case id, run id,
//! versions, progress and the exact row where engine and oracle diverge.

use std::path::Path;

use serde_json::Value;

use crate::query::executor::{self, Catalog};
use crate::query::Expr;
use crate::state::TableStore;
use crate::verify::oracle::{OracleFixture, OT};

/// Kind of discrepancy, asserted on explicitly by the integration tests.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum FailureKind {
    /// Engine and oracle disagree on which rows are live.
    LiveSetMismatch,
    /// Both agreed the row is live, but the 3VL verdict differed.
    VerdictMismatch,
    /// Engine returned an error the oracle said was valid input.
    UnexpectedEngineError,
    /// Engine accepted input the oracle rejects.
    AcceptedInvalidInput,
    /// Engine accepted input it should reject with a typed error.
    MissingExpectedError,
    /// Counts did not partition the live universe exactly once.
    PartitionBroken,
}

impl FailureKind {
    pub fn as_str(&self) -> &'static str {
        match self {
            FailureKind::LiveSetMismatch => "live_set_mismatch",
            FailureKind::VerdictMismatch => "verdict_mismatch",
            FailureKind::UnexpectedEngineError => "unexpected_engine_error",
            FailureKind::AcceptedInvalidInput => "accepted_invalid_input",
            FailureKind::MissingExpectedError => "missing_expected_error",
            FailureKind::PartitionBroken => "partition_broken",
        }
    }
}

/// A single scenario.
pub struct Case {
    pub id: String,
    pub expr: Value,
    pub as_of: Option<u64>,
    /// `Some(kind)` means the case must fail with that error category.
    pub expect_error: Option<&'static str>,
}

impl Case {
    /// A case expected to succeed and match the oracle.
    pub fn ok(id: impl Into<String>, expr: Value, as_of: Option<u64>) -> Self {
        Case {
            id: id.into(),
            expr,
            as_of,
            expect_error: None,
        }
    }

    /// A case expected to be rejected categorised as `kind`.
    pub fn error(
        id: impl Into<String>,
        expr: Value,
        as_of: Option<u64>,
        kind: &'static str,
    ) -> Self {
        Case {
            id: id.into(),
            expr,
            as_of,
            expect_error: Some(kind),
        }
    }
}

/// One row-level disagreement.
#[derive(Clone, Debug)]
pub struct RowFailure {
    pub row: usize,
    pub engine: String,
    pub oracle: String,
}

/// One case-level finding.
#[derive(Clone, Debug)]
pub struct CaseFailure {
    pub case_id: String,
    pub kind: FailureKind,
    pub detail: String,
    pub rows: Vec<RowFailure>,
}

/// Everything learned from one case, pass or fail (used in log rendering).
#[derive(Debug)]
pub struct CaseReport {
    pub case_id: String,
    pub run_id: u64,
    pub as_of: u64,
    pub head: u64,
    pub content: u64,
    pub live: Vec<usize>,
    pub deleted: Vec<usize>,
    pub engine_rows: String,
    pub oracle_rows: String,
    pub failures: Vec<CaseFailure>,
}

impl CaseReport {
    /// Whether the case passed.
    pub fn passed(&self) -> bool {
        self.failures.is_empty()
    }

    /// Multi-line, input-attributable rendering for test output and logs.
    pub fn render(&self) -> String {
        let mut s = format!(
            "[run {} case {}] as_of={} content=v{} head=v{} live={:?} deleted={:?}\n  engine: {}\n  oracle: {}",
            self.run_id,
            self.case_id,
            self.as_of,
            self.content,
            self.head,
            self.live,
            self.deleted,
            self.engine_rows,
            self.oracle_rows
        );
        for f in &self.failures {
            s.push_str(&format!("\n  FAIL[{}] {}", f.kind.as_str(), f.detail));
            for r in &f.rows {
                s.push_str(&format!(
                    "\n        row {}: engine={} oracle={}",
                    r.row, r.engine, r.oracle
                ));
            }
        }
        s
    }
}

/// Aggregate suite report.
#[derive(Debug, Default)]
pub struct SuiteReport {
    pub cases: Vec<CaseReport>,
}

impl SuiteReport {
    /// All failure records across the suite.
    pub fn failures(&self) -> Vec<&CaseFailure> {
        self.cases.iter().flat_map(|c| c.failures.iter()).collect()
    }

    /// Render every failing case, for panic messages.
    pub fn render_failures(&self) -> String {
        self.cases
            .iter()
            .filter(|c| !c.passed())
            .map(|c| c.render())
            .collect::<Vec<_>>()
            .join("\n")
    }
}

/// Run one case through both implementations and reconcile.
pub fn run_case(store: &TableStore, oracle: &OracleFixture, case: &Case) -> CaseReport {
    let run_id = executor::next_run_id();
    let head = store.versions.head_version();
    let content = store.versions.index_version();
    let n = store.table.len();
    let as_of = case.as_of.unwrap_or(head);

    tracing::info!(
        run_id,
        case = %case.id,
        as_of,
        head,
        content,
        expr = %case.expr,
        "differential case start"
    );

    // Oracle verdict first: it is the reference, including error expectations.
    let mut oracle_live = Vec::new();
    let mut oracle_deleted = Vec::new();
    let mut oracle_verdicts: Vec<OT> = Vec::new();
    let mut oracle_error: Option<String> = None;
    for row in 0..n {
        if !oracle.is_live_at(row, as_of) {
            oracle_deleted.push(row);
            continue;
        }
        oracle_live.push(row);
        match oracle.eval_row(&case.expr, row) {
            Ok(t) => oracle_verdicts.push(t),
            Err(e) => {
                oracle_error = Some(e);
                break;
            }
        }
    }

    // Engine path.
    let parsed = Expr::parse(&case.expr);
    let engine_outcome = parsed.and_then(|expr| {
        let catalog = Catalog::new(store.indexes(), &store.versions)?;
        executor::evaluate(&catalog, &expr, Some(as_of), run_id)
    });

    let mut failures = Vec::new();

    match (&engine_outcome, oracle_error.as_deref(), case.expect_error) {
        (Err(ee), None, None) => failures.push(CaseFailure {
            case_id: case.id.clone(),
            kind: FailureKind::UnexpectedEngineError,
            detail: format!("engine errored on oracle-valid input: {ee}"),
            rows: vec![],
        }),
        (Ok(_), Some(oe), None) => failures.push(CaseFailure {
            case_id: case.id.clone(),
            kind: FailureKind::AcceptedInvalidInput,
            detail: format!("engine accepted input the oracle rejects: {oe}"),
            rows: vec![],
        }),
        // Both reject: they agree the input is invalid; nothing to reconcile.
        (Err(_), Some(_), None) => {}
        (Err(ee), _, Some(expected_kind)) => {
            let actual_kind = error_kind(ee);
            if actual_kind != expected_kind {
                failures.push(CaseFailure {
                    case_id: case.id.clone(),
                    kind: FailureKind::MissingExpectedError,
                    detail: format!("expected error kind {expected_kind}, got {actual_kind}: {ee}"),
                    rows: vec![],
                });
            }
        }
        (Ok(_), _, Some(expected_kind)) => failures.push(CaseFailure {
            case_id: case.id.clone(),
            kind: FailureKind::MissingExpectedError,
            detail: format!("expected {expected_kind} error but the query succeeded"),
            rows: vec![],
        }),
        (Ok(outcome), None, None) => {
            reconcile_success(
                case,
                outcome,
                &oracle_live,
                &oracle_deleted,
                &oracle_verdicts,
                &mut failures,
            );
        }
    }

    // Build display rows even on failure: align over live order.
    let mut engine_rows = String::new();
    if let Ok(o) = &engine_outcome {
        let map: std::collections::HashMap<usize, char> =
            o.classifications.iter().copied().collect();
        engine_rows = oracle_live
            .iter()
            .map(|r| map.get(r).copied().unwrap_or('D'))
            .collect();
    }
    let oracle_rows: String = oracle_verdicts
        .iter()
        .map(|t| match t {
            OT::True => 'T',
            OT::False => 'F',
            OT::Unknown => 'U',
        })
        .collect();

    let report = CaseReport {
        case_id: case.id.clone(),
        run_id,
        as_of,
        head,
        content,
        live: oracle_live,
        deleted: oracle_deleted,
        engine_rows,
        oracle_rows,
        failures,
    };
    if report.passed() {
        tracing::info!(
            run_id,
            case = %case.id,
            live_n = report.live.len(),
            engine = %report.engine_rows,
            "differential case PASS: engine matches scalar oracle row by row"
        );
    } else {
        tracing::error!(
            run_id,
            case = %case.id,
            "\n{}",
            report.render()
        );
    }
    report
}

fn reconcile_success(
    case: &Case,
    outcome: &crate::query::QueryOutcome,
    oracle_live: &[usize],
    oracle_deleted: &[usize],
    oracle_verdicts: &[OT],
    failures: &mut Vec<CaseFailure>,
) {
    // 1. Live-set agreement.
    if outcome.deleted_rows != oracle_deleted {
        failures.push(CaseFailure {
            case_id: case.id.clone(),
            kind: FailureKind::LiveSetMismatch,
            detail: format!(
                "engine deleted {:?}, oracle deleted {oracle_deleted:?}",
                outcome.deleted_rows
            ),
            rows: vec![],
        });
    }
    if outcome.classifications.len() != oracle_live.len() {
        failures.push(CaseFailure {
            case_id: case.id.clone(),
            kind: FailureKind::LiveSetMismatch,
            detail: format!(
                "engine classified {} live rows, oracle {}",
                outcome.classifications.len(),
                oracle_live.len()
            ),
            rows: vec![],
        });
        return;
    }

    // 2. Exact-once partition: counts sum to live total and the classes are a
    //    permutation of the classification list.
    let classified =
        outcome.true_rows.len() + outcome.false_rows.len() + outcome.unknown_rows.len();
    if classified != outcome.live_total {
        failures.push(CaseFailure {
            case_id: case.id.clone(),
            kind: FailureKind::PartitionBroken,
            detail: format!(
                "T+F+U = {classified} but live_total = {} (a row is double- or un-classified)",
                outcome.live_total
            ),
            rows: vec![],
        });
    }

    // 3. Per-row verdict agreement.
    let map: std::collections::HashMap<usize, char> =
        outcome.classifications.iter().copied().collect();
    let mut row_failures = Vec::new();
    for (i, &row) in oracle_live.iter().enumerate() {
        let engine_ch = map.get(&row).copied().unwrap_or('D');
        let engine_v = ch_to_name(engine_ch);
        let oracle_v = match oracle_verdicts[i] {
            OT::True => "true",
            OT::False => "false",
            OT::Unknown => "unknown",
        };
        if engine_v != oracle_v {
            row_failures.push(RowFailure {
                row,
                engine: engine_v.into(),
                oracle: oracle_v.into(),
            });
        }
    }
    if !row_failures.is_empty() {
        failures.push(CaseFailure {
            case_id: case.id.clone(),
            kind: FailureKind::VerdictMismatch,
            detail: format!(
                "{} of {} live rows disagree with the scalar oracle",
                row_failures.len(),
                oracle_live.len()
            ),
            rows: row_failures,
        });
    }
}

fn ch_to_name(ch: char) -> &'static str {
    match ch {
        'T' => "true",
        'F' => "false",
        'U' => "unknown",
        _ => "deleted",
    }
}

fn error_kind(e: &crate::error::TviError) -> &'static str {
    use crate::error::TviError::*;
    match e {
        UnknownColumn(_) => "unknown_column",
        TypeMismatch { .. } | InvalidLiteral { .. } => "type_error",
        InvalidQuery(_) => "invalid_query",
        UniverseMismatch { .. } => "universe_version_mismatch",
        Arrow(_) | Io(_) => "data_error",
    }
}

/// Run a full suite and collect the report.
pub fn run_suite(store: &TableStore, oracle: &OracleFixture, cases: &[Case]) -> SuiteReport {
    let total = cases.len();
    tracing::info!(
        total,
        content = store.versions.index_version(),
        head = store.versions.head_version(),
        "differential suite start"
    );
    let mut report = SuiteReport::default();
    for (progress, case) in cases.iter().enumerate() {
        tracing::info!(progress = progress + 1, total, case = %case.id, "suite progress");
        report.cases.push(run_case(store, oracle, case));
    }
    let passed = report.cases.iter().filter(|c| c.passed()).count();
    tracing::info!(passed, total, "differential suite finished");
    report
}

/// Load the engine store and the independent oracle for one fixture.
pub fn load_both_paths(
    manifest_path: &Path,
    fixture_dir: &Path,
) -> (std::sync::Arc<TableStore>, OracleFixture) {
    let cfg_text = std::fs::read_to_string(manifest_path).expect("read manifest");
    let manifest: crate::state::Manifest =
        toml::from_str(&cfg_text).expect("manifest parses for engine");
    let store = TableStore::from_manifest(manifest, fixture_dir).expect("engine fixture loads");
    let oracle = OracleFixture::load(fixture_dir, manifest_path).expect("oracle fixture loads");
    (store, oracle)
}
