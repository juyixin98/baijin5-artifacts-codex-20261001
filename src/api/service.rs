//! Service orchestration: the single pipeline every request follows.
//!
//! resolve inputs -> resource limits -> validate/compile -> decode cursor ->
//! execute -> assemble response + redacted diagnostics.
use serde::Serialize;

use crate::arrow_io;
use crate::domain::LogicalType;
use crate::error::JoinError;
use crate::join::{decode_after, execute, EmittedRow};
use crate::query::{compile, resolve_inputs, JoinRequest, Plan};
use crate::resource::{resolve_request_id, AppState, Decision, Diagnostics};

#[derive(Debug, Clone, Serialize)]
pub struct ColumnOut {
    pub name: String,
    #[serde(rename = "type")]
    pub ty: String,
}

#[derive(Debug, Serialize)]
pub struct JoinResponse {
    pub request_id: String,
    pub decision: Decision,
    pub columns: Vec<ColumnOut>,
    pub rows: Vec<RowOut>,
    pub row_count: usize,
    pub truncated: bool,
    pub next_cursor: Option<String>,
    pub counters: crate::trie::Counters,
    pub diagnostics: Diagnostics,
}

#[derive(Debug, Serialize)]
pub struct RowOut {
    pub values: Vec<crate::domain::Datum>,
    pub multiplicity: u128,
}

#[derive(Debug, Serialize)]
pub struct ErrorResponse {
    pub request_id: String,
    pub decision: Decision,
    pub error_category: String,
    pub error_code: String,
    pub message: String,
    pub diagnostics: Diagnostics,
}

pub struct Prepared {
    pub plan: Plan,
    pub columns: Vec<(String, LogicalType)>,
}

/// Validate + compile. Shared by JSON and Arrow endpoints.
pub fn prepare(state: &AppState, req: &JoinRequest) -> Result<Prepared, JoinError> {
    let catalog = state
        .catalog
        .try_read()
        .expect("catalog lock is never held across an await");
    let inputs = resolve_inputs(req, catalog.entries())?;
    let sizes: Vec<(String, usize)> = inputs
        .iter()
        .map(|r| (r.name.clone(), r.rows.len()))
        .collect();
    state.check_resource_limits(&sizes)?;
    let plan = compile(inputs, req)?;
    let columns = plan
        .select
        .iter()
        .map(|attr| {
            let idx = plan
                .output_attributes
                .iter()
                .position(|a| a == attr)
                .expect("select validated");
            (attr.clone(), plan.output_types[idx])
        })
        .collect();
    Ok(Prepared { plan, columns })
}

fn to_row_out(rows: &[EmittedRow]) -> Vec<RowOut> {
    rows.iter()
        .map(|r| RowOut {
            values: r.values.clone(),
            multiplicity: r.multiplicity,
        })
        .collect()
}

fn accepted_diagnostics(
    request_id: &str,
    plan: &Plan,
    rows_len: usize,
    truncated: bool,
    counters: &crate::trie::Counters,
) -> Diagnostics {
    Diagnostics {
        request_id: request_id.to_string(),
        decision: if truncated {
            Decision::Undecidable
        } else {
            Decision::Accepted
        },
        error_category: None,
        error_code: None,
        reason: None,
        relation_count: plan.relations.len(),
        relation_names: plan.relations.iter().map(|r| r.name.clone()).collect(),
        join_variables: plan.join_vars.clone(),
        output_attributes: plan.select.clone(),
        null_policy: plan.null_policy.as_str().to_string(),
        input_rows: plan.relations.iter().map(|r| r.input_rows).collect(),
        distinct_rows: plan.relations.iter().map(|r| r.distinct_rows).collect(),
        null_join_rows_dropped: plan
            .relations
            .iter()
            .map(|r| r.null_join_rows_dropped)
            .collect(),
        counters: Some(counters.clone()),
        page_rows: rows_len,
        truncated,
        resumable: truncated,
        redaction: Diagnostics::redaction_note(),
    }
}

/// Full pipeline returning a JSON-ready response.
#[allow(clippy::result_large_err)] // diagnostics are intentionally rich and redacted
pub fn run_json(state: &AppState, body: JoinRequest) -> Result<JoinResponse, ErrorResponse> {
    let request_id = resolve_request_id(body.request_id.clone());
    let relation_names: Vec<String> = body
        .relations
        .iter()
        .map(|r| r.name.clone())
        .chain(body.fixtures.iter().cloned())
        .collect();

    let run = (|| -> Result<JoinResponse, JoinError> {
        let Prepared { plan, columns } = prepare(state, &body)?;
        let limit = body.effective_limit();
        let after = match &body.cursor {
            None => None,
            Some(token) => {
                let types: Vec<LogicalType> = columns.iter().map(|(_, t)| *t).collect();
                Some(decode_after(token, &types)?)
            }
        };
        let out = execute(&plan, limit, after);
        let diagnostics = accepted_diagnostics(
            &request_id,
            &plan,
            out.rows.len(),
            out.truncated,
            &out.counters,
        );
        Ok(JoinResponse {
            request_id: request_id.clone(),
            decision: diagnostics.decision,
            columns: columns
                .iter()
                .map(|(n, t)| ColumnOut {
                    name: n.clone(),
                    ty: t.as_str().to_string(),
                })
                .collect(),
            rows: to_row_out(&out.rows),
            row_count: out.rows.len(),
            truncated: out.truncated,
            next_cursor: out.next_cursor,
            counters: out.counters,
            diagnostics,
        })
    })();

    run.map_err(|err| {
        let decision = Diagnostics::decision_for(&err);
        let diagnostics = Diagnostics::rejected(&request_id, &err, relation_names);
        tracing::warn!(
            request_id = %request_id,
            decision = ?decision,
            error_code = err.code.as_str(),
            "join request not accepted"
        );
        ErrorResponse {
            request_id,
            decision,
            error_category: err.category().to_string(),
            error_code: err.code.as_str().to_string(),
            message: err.message,
            diagnostics,
        }
    })
}

/// Full pipeline returning Arrow IPC bytes plus side-band metadata.
#[allow(clippy::result_large_err)] // diagnostics are intentionally rich and redacted
pub fn run_arrow(
    state: &AppState,
    body: JoinRequest,
) -> Result<(Vec<u8>, JoinResponseMetadata), ErrorResponse> {
    let request_id = resolve_request_id(body.request_id.clone());
    let Prepared { plan, columns } = prepare(state, &body).map_err(|err| {
        let names = body
            .relations
            .iter()
            .map(|r| r.name.clone())
            .chain(body.fixtures.iter().cloned())
            .collect();
        ErrorResponse {
            request_id: request_id.clone(),
            decision: Diagnostics::decision_for(&err),
            error_category: err.category().to_string(),
            error_code: err.code.as_str().to_string(),
            message: err.message.clone(),
            diagnostics: Diagnostics::rejected(&request_id, &err, names),
        }
    })?;
    let limit = body.effective_limit();
    let after = match &body.cursor {
        None => None,
        Some(token) => {
            let types: Vec<LogicalType> = columns.iter().map(|(_, t)| *t).collect();
            Some(decode_after(token, &types).map_err(|err| ErrorResponse {
                request_id: request_id.clone(),
                decision: Diagnostics::decision_for(&err),
                error_category: err.category().to_string(),
                error_code: err.code.as_str().to_string(),
                message: err.message.clone(),
                diagnostics: Diagnostics::rejected(
                    &request_id,
                    &err,
                    plan.relations.iter().map(|r| r.name.clone()).collect(),
                ),
            })?)
        }
    };
    let out = execute(&plan, limit, after);
    let bytes = arrow_io::encode_ipc(&out.rows, &columns).map_err(|err| ErrorResponse {
        request_id: request_id.clone(),
        decision: Diagnostics::decision_for(&err),
        error_category: err.category().to_string(),
        error_code: err.code.as_str().to_string(),
        message: err.message.clone(),
        diagnostics: Diagnostics::rejected(
            &request_id,
            &err,
            plan.relations.iter().map(|r| r.name.clone()).collect(),
        ),
    })?;
    let meta = JoinResponseMetadata {
        request_id,
        truncated: out.truncated,
        next_cursor: out.next_cursor,
        counters: out.counters,
        row_count: out.rows.len(),
    };
    Ok((bytes, meta))
}

#[derive(Debug, Serialize)]
pub struct JoinResponseMetadata {
    pub request_id: String,
    pub truncated: bool,
    pub next_cursor: Option<String>,
    pub counters: crate::trie::Counters,
    pub row_count: usize,
}
