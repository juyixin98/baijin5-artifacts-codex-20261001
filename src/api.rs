//! Validation entry point and request orchestration.
//!
//! This is the thin, explicit pipeline every request goes through:
//!
//! ```text
//! build catalog -> validate/resolve -> compile group plan ->
//! execute naive (reference) -> execute rewrite (decorrelated) ->
//! cross-check equivalence -> encode via Arrow2
//! ```
//!
//! It is deliberately server-independent: [`run_pipeline`] takes a decoded
//! request and returns a structured outcome, so the same code path is used by
//! the HTTP handler and by tests.

use std::time::Instant;

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::arrow_io;
use crate::batch::Batch;
use crate::error::{ErrorKind, QError};
use crate::naive;
use crate::query::Query;
use crate::resource::{Catalog, FixtureDoc};
use crate::rewrite;
use crate::rewrite::proofs::Proof;
use crate::state::{AppState, RequestCtx};
use crate::validator::{self, ResolvedQuery};

/// Selects which executor(s) run.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RunMode {
    /// Only the decorrelated executor.
    Rewrite,
    /// Only the nested-loop reference.
    Naive,
    /// Both, with an equality assertion (default and the recommended mode).
    #[default]
    Crosscheck,
}

/// On-the-wire request body.
#[derive(Debug, Clone, Deserialize)]
pub struct RawRequest {
    pub query: Query,
    #[serde(default)]
    pub fixtures: FixtureDoc,
    #[serde(default)]
    pub options: ReqOptions,
}

#[derive(Debug, Clone, Default, Deserialize)]
pub struct ReqOptions {
    #[serde(default)]
    pub mode: Option<RunMode>,
}

#[derive(Debug, Clone, Serialize)]
pub struct StepTrace {
    pub step: String,
    pub detail: Value,
    pub elapsed_ms: u128,
}

#[derive(Debug, Clone, Serialize)]
pub struct ErrorReport {
    pub kind: String,
    pub message: String,
    pub at_step: String,
    pub engine: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct CrosscheckReport {
    #[serde(rename = "match")]
    pub match_: bool,
    pub naive_row_count: usize,
    pub rewrite_row_count: usize,
    pub compared_on: Vec<String>,
}

/// The full structured outcome (also serialized as the HTTP body).
#[derive(Debug, Clone, Serialize)]
pub struct Outcome {
    pub request_id: String,
    pub version: String,
    pub location: String,
    pub status: &'static str,
    pub mode: RunMode,
    pub form: Option<String>,
    pub steps: Vec<StepTrace>,
    pub plan: Option<PlanInfo>,
    pub result: Option<ResultPayload>,
    pub crosscheck: Option<CrosscheckReport>,
    pub errors: Vec<ErrorReport>,
    pub uncertainties: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct PlanInfo {
    pub algorithm: String,
    pub group_count: usize,
    pub inner_rows: usize,
    pub proofs: Vec<Proof>,
}

#[derive(Debug, Clone, Serialize)]
pub struct ResultPayload {
    pub columns: Vec<String>,
    pub arrow_types: Vec<String>,
    pub row_count: usize,
    pub multiplicity_preserved: bool,
    pub rows: Vec<Vec<Value>>,
}

fn step(step: &str, detail: Value, started: Instant) -> StepTrace {
    StepTrace {
        step: step.to_string(),
        detail,
        elapsed_ms: started.elapsed().as_millis(),
    }
}

/// Run the full pipeline for one decoded request.
pub fn run_pipeline(raw: RawRequest, state: &AppState, ctx: &RequestCtx) -> (Outcome, u16) {
    let mode = raw.options.mode.unwrap_or_default();
    let mut steps: Vec<StepTrace> = Vec::new();
    let mut uncertainties: Vec<String> = Vec::new();

    // 1. Catalog from local synthetic fixtures.
    let catalog = match Catalog::from_fixtures(raw.fixtures.relations) {
        Ok(c) => {
            steps.push(step(
                "build_catalog",
                json!({ "relations": c.names() }),
                Instant::now(),
            ));
            c
        }
        Err(e) => {
            return fail(
                ctx,
                state,
                mode,
                steps,
                uncertainties,
                e,
                "build_catalog",
                None,
            )
        }
    };

    // 2. Validate + name-resolve + support gate.
    let resolved = match validator::validate(&raw.query, &catalog) {
        Ok(r) => {
            steps.push(step(
                "validate",
                json!({
                    "form": form_name(r.form),
                    "outer_relation": r.outer_relation,
                    "select": r.select.iter().map(|s| s.name.clone()).collect::<Vec<_>>(),
                }),
                Instant::now(),
            ));
            r
        }
        Err(e) => return fail(ctx, state, mode, steps, uncertainties, e, "validate", None),
    };

    // 3. Compile the decorrelated plan (build the inner group table).
    let plan_started = Instant::now();
    let group_count = match plan_group_count(&resolved) {
        Ok(gc) => gc,
        Err(e) => {
            return fail(
                ctx,
                state,
                mode,
                steps,
                uncertainties,
                e,
                "compile_plan",
                None,
            )
        }
    };
    steps.push(step(
        "compile_plan",
        json!({
            "algorithm": "group_by_correlation_keys_then_outer_join",
            "inner_relation": sub_of(&resolved).relation,
            "inner_rows": sub_of(&resolved).inner_batch.row_count(),
            "group_count": group_count,
        }),
        plan_started,
    ));

    let proofs = rewrite::proofs::proofs_for(resolved.form);
    let plan = PlanInfo {
        algorithm: "group_by_correlation_keys_then_outer_join".to_string(),
        group_count,
        inner_rows: sub_of(&resolved).inner_batch.row_count(),
        proofs,
    };

    // 4. Execute with the requested engine(s).
    let naive_out = if matches!(mode, RunMode::Naive | RunMode::Crosscheck) {
        let t = Instant::now();
        let out = naive::execute(&resolved);
        steps.push(step(
            "execute_naive",
            json!({ "engine": "row_at_a_time_nested_loop", "ok": out.is_ok() }),
            t,
        ));
        Some(out)
    } else {
        None
    };

    let rewrite_out = if matches!(mode, RunMode::Rewrite | RunMode::Crosscheck) {
        let t = Instant::now();
        let out = rewrite::execute(&resolved);
        steps.push(step(
            "execute_rewrite",
            json!({ "engine": "decorrelated_group_join", "ok": out.is_ok() }),
            t,
        ));
        Some(out)
    } else {
        None
    };

    // 5. Cross-check the two engines, including error category.
    let chosen: Result<Batch, QError> = match (naive_out, rewrite_out) {
        (Some(n), Some(r)) => {
            let t = Instant::now();
            let cc = compare_engines(&n, &r);
            steps.push(step(
                "crosscheck",
                json!({
                    "match": cc.0,
                    "naive_rows": cc.1,
                    "rewrite_rows": cc.2,
                }),
                t,
            ));
            match (n, r) {
                (Ok(b), Ok(_)) => {
                    if !cc.0 {
                        let e = QError::internal(
                            "reference and decorrelated results differ (see crosscheck step)",
                        );
                        return fail(
                            ctx,
                            state,
                            mode,
                            steps,
                            uncertainties,
                            e,
                            "crosscheck",
                            None,
                        );
                    }
                    Ok(b)
                }
                (Err(ne), Err(re)) => {
                    if ne.kind != re.kind {
                        let e = QError::internal(format!(
                            "engines raised different error categories: {} vs {}",
                            ne.kind.as_str(),
                            re.kind.as_str()
                        ));
                        return fail(
                            ctx,
                            state,
                            mode,
                            steps,
                            uncertainties,
                            e,
                            "crosscheck",
                            None,
                        );
                    }
                    // Same category: report the reference error, attributed to both.
                    return fail_both(
                        ctx,
                        state,
                        mode,
                        steps,
                        uncertainties,
                        ne,
                        resolved.form,
                        Some(plan),
                    );
                }
                (Err(e), Ok(_)) => {
                    return fail(
                        ctx,
                        state,
                        mode,
                        steps,
                        uncertainties,
                        QError::internal(format!(
                            "reference engine failed with {} but decorrelated engine succeeded",
                            e.kind.as_str()
                        )),
                        "crosscheck",
                        None,
                    );
                }
                (Ok(_), Err(e)) => {
                    return fail(
                        ctx,
                        state,
                        mode,
                        steps,
                        uncertainties,
                        QError::internal(format!(
                            "decorrelated engine failed with {} but reference engine succeeded",
                            e.kind.as_str()
                        )),
                        "crosscheck",
                        None,
                    );
                }
            }
        }
        (Some(n), None) => n,
        (None, Some(r)) => r,
        (None, None) => Err(QError::internal("no engine was selected")),
    };

    // 6. Encode the surviving batch through Arrow2.
    let batch = match chosen {
        Ok(b) => b,
        Err(e) => {
            let engine = match mode {
                RunMode::Naive => Some("naive"),
                RunMode::Rewrite => Some("rewrite"),
                RunMode::Crosscheck => None,
            };
            return fail(ctx, state, mode, steps, uncertainties, e, "execute", engine);
        }
    };
    let encoded = match arrow_io::encode_result(&batch) {
        Ok(e) => e,
        Err(e) => {
            return fail(
                ctx,
                state,
                mode,
                steps,
                uncertainties,
                e,
                "arrow_encode",
                None,
            )
        }
    };
    steps.push(step(
        "arrow_encode",
        json!({ "arrow_types": encoded.arrow_types, "rows": encoded.rows.len() }),
        Instant::now(),
    ));

    let result = ResultPayload {
        columns: batch
            .schema()
            .fields
            .iter()
            .map(|f| f.name.to_string())
            .collect(),
        arrow_types: encoded.arrow_types,
        row_count: batch.row_count(),
        multiplicity_preserved: true,
        rows: encoded.rows,
    };

    let crosscheck = if mode == RunMode::Crosscheck {
        Some(CrosscheckReport {
            match_: true,
            naive_row_count: batch.row_count(),
            rewrite_row_count: batch.row_count(),
            compared_on: vec![
                "schema".to_string(),
                "row_count".to_string(),
                "ordered_rows".to_string(),
                "null_placement".to_string(),
                "duplicate_multiplicity".to_string(),
                "error_category".to_string(),
            ],
        })
    } else {
        // Single-engine runs are intentionally marked as not independently
        // verified within this request: the result relies on that one engine.
        uncertainties.push(
            "result was produced by a single engine and was NOT cross-checked against the \
             row-at-a-time reference in this request (set options.mode=\"crosscheck\" to verify)"
                .to_string(),
        );
        None
    };

    let out = Outcome {
        request_id: ctx.request_id.clone(),
        version: state.version.to_string(),
        location: state.location.clone(),
        status: "ok",
        mode,
        form: Some(form_name(resolved.form).to_string()),
        steps,
        plan: Some(plan),
        result: Some(result),
        crosscheck,
        errors: Vec::new(),
        uncertainties,
    };
    (out, 200)
}

fn sub_of(r: &ResolvedQuery) -> &crate::validator::RSubquery {
    match &r.terms[r.subquery_at] {
        crate::validator::ROuterTerm::Exists { sub, .. }
        | crate::validator::ROuterTerm::ScalarSub { sub, .. }
        | crate::validator::ROuterTerm::InSub { sub, .. } => sub,
        crate::validator::ROuterTerm::Local(_) => unreachable!("subquery index points at local"),
    }
}

fn plan_group_count(r: &ResolvedQuery) -> Result<usize, QError> {
    use crate::operators::groups::GroupTable;
    Ok(GroupTable::build(sub_of(r))?.group_count())
}

fn compare_engines(n: &Result<Batch, QError>, r: &Result<Batch, QError>) -> (bool, usize, usize) {
    match (n, r) {
        (Ok(a), Ok(b)) => (batches_equal(a, b), a.row_count(), b.row_count()),
        (Err(ne), Err(re)) => (ne.kind == re.kind, 0, 0),
        _ => (false, 0, 0),
    }
}

/// Order-sensitive logical equality including NULL placement and duplicates.
pub fn batches_equal(a: &Batch, b: &Batch) -> bool {
    if a.row_count() != b.row_count() {
        return false;
    }
    let fa = a.schema().fields.clone();
    let fb = b.schema().fields.clone();
    if fa.len() != fb.len()
        || fa
            .iter()
            .zip(&fb)
            .any(|(x, y)| x.name != y.name || x.dtype != y.dtype)
    {
        return false;
    }
    for row in 0..a.row_count() {
        for col in 0..fa.len() {
            if a.get(col, row) != b.get(col, row) {
                return false;
            }
        }
    }
    true
}

#[allow(clippy::too_many_arguments)]
fn fail(
    ctx: &RequestCtx,
    state: &AppState,
    mode: RunMode,
    steps: Vec<StepTrace>,
    uncertainties: Vec<String>,
    err: QError,
    at_step: &str,
    engine: Option<&str>,
) -> (Outcome, u16) {
    let report = ErrorReport {
        kind: err.kind.as_str().to_string(),
        message: err.message.clone(),
        at_step: at_step.to_string(),
        engine: engine.map(|s| s.to_string()),
    };
    let out = Outcome {
        request_id: ctx.request_id.clone(),
        version: state.version.to_string(),
        location: state.location.clone(),
        status: "error",
        mode,
        form: None,
        steps,
        plan: None,
        result: None,
        crosscheck: None,
        errors: vec![report],
        uncertainties,
    };
    (out, http_status(err.kind))
}

#[allow(clippy::too_many_arguments)]
fn fail_both(
    ctx: &RequestCtx,
    state: &AppState,
    mode: RunMode,
    steps: Vec<StepTrace>,
    uncertainties: Vec<String>,
    err: QError,
    form: crate::query::SubForm,
    plan: Option<PlanInfo>,
) -> (Outcome, u16) {
    let report = ErrorReport {
        kind: err.kind.as_str().to_string(),
        message: err.message.clone(),
        at_step: "execute".to_string(),
        engine: Some("naive+rewrite".to_string()),
    };
    let out = Outcome {
        request_id: ctx.request_id.clone(),
        version: state.version.to_string(),
        location: state.location.clone(),
        status: "error",
        mode,
        form: Some(form_name(form).to_string()),
        steps,
        plan,
        result: None,
        crosscheck: Some(CrosscheckReport {
            match_: true,
            naive_row_count: 0,
            rewrite_row_count: 0,
            compared_on: vec!["error_category".to_string()],
        }),
        errors: vec![report],
        uncertainties,
    };
    (out, http_status(err.kind))
}

fn http_status(k: ErrorKind) -> u16 {
    match k {
        ErrorKind::InvalidRequest
        | ErrorKind::UnknownReference
        | ErrorKind::TypeMismatch
        | ErrorKind::InvalidLiteral
        | ErrorKind::UnsupportedForm
        | ErrorKind::CorrelationMismatch
        | ErrorKind::MisplacedAggregate => 400,
        ErrorKind::ScalarMultipleRows | ErrorKind::NumericOverflow => 422,
        ErrorKind::MalformedBatch | ErrorKind::Internal | ErrorKind::Transport => 500,
    }
}

fn form_name(f: crate::query::SubForm) -> &'static str {
    match f {
        crate::query::SubForm::Exists => "exists",
        crate::query::SubForm::NotExists => "not_exists",
        crate::query::SubForm::ScalarAgg => "scalar_agg",
        crate::query::SubForm::ScalarBare => "scalar_bare",
        crate::query::SubForm::InUnaggregated => "in_unaggregated",
    }
}
