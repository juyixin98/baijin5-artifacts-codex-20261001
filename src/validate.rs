//! Validation and execution entry point.
//!
//! This is the single door both the HTTP layer and tests use: it turns a
//! [`QueryRequest`] into relations (inline payload and/or catalog fixtures),
//! validates and plans the natural join, runs the Leapfrog engine, optionally
//! cross-checks the independent naive oracle, manages resumption cursors and
//! emits the decision record. It deliberately contains no Axum types so the
//! same entry point is usable from integration tests and the example CLI.

use std::collections::hash_map::DefaultHasher;
use std::hash::{Hash, Hasher};

use serde::{Deserialize, Serialize};

use crate::batch::TypedChunk;
use crate::config::Config;
use crate::diagnostics::{Decision, DecisionRecord, KeyState, Reason, RelationState, RequestId};
use crate::error::{ErrorCode, Result, ServiceError};
use crate::lftj::{EngineStats, JoinEngine, ResumePoint, StopReason};
use crate::naive::{multisets_equal, NaiveOracle, NaiveStats};
use crate::plan::{NullPolicy, Plan};
use crate::schema::{Column, ColumnType, Relation};
use crate::state::{fingerprint_parts, AppState, Catalog};
use crate::value::Cell;

/// Wire format: one inline relation.
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct RelationSpec {
    pub name: String,
    pub columns: Vec<ColumnSpec>,
    /// Rows use JSON values: number/string/boolean or null, typed per column.
    #[serde(default)]
    pub rows: Vec<Vec<serde_json::Value>>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct ColumnSpec {
    pub name: String,
    #[serde(rename = "type")]
    pub typ: String,
}

/// Wire format of a join request.
#[derive(Debug, Clone, Deserialize, Serialize, Default)]
pub struct QueryRequest {
    /// Inline relation definitions.
    #[serde(default)]
    pub relations: Vec<RelationSpec>,
    /// Names of catalog relations to join together with inline ones.
    #[serde(default)]
    pub relation_names: Vec<String>,
    #[serde(default)]
    pub null_policy: Option<String>,
    /// Page size in output rows.
    #[serde(default)]
    pub limit: Option<u64>,
    /// Previously returned opaque cursor.
    #[serde(default)]
    pub cursor: Option<String>,
    /// Optional bound on trie accesses; may yield an `undecidable` verdict.
    #[serde(default)]
    pub access_budget: Option<u64>,
    /// Cross-check the independent naive oracle and include its counters.
    #[serde(default)]
    pub compare_naive: bool,
}

/// Naive-oracle cross-check included in a response when requested.
#[derive(Debug, Clone, Serialize)]
pub struct NaiveComparison {
    pub matches: bool,
    pub naive_row_probes: u64,
    pub naive_intermediate_tuples_materialized: u64,
    pub naive_emitted_rows: u64,
}

/// User-facing outcome of a request that ran.
#[derive(Debug, Clone, Serialize)]
pub struct QueryOutcome {
    pub request_id: String,
    pub decision: Decision,
    pub columns: Vec<ColumnView>,
    pub rows: Vec<Vec<serde_json::Value>>,
    pub row_count: u64,
    pub stats: StatsView,
    pub naive: Option<NaiveComparison>,
    pub next_cursor: Option<String>,
    pub reasons: Vec<Reason>,
}

#[derive(Debug, Clone, Serialize)]
pub struct ColumnView {
    pub name: String,
    #[serde(rename = "type")]
    pub typ: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct StatsView {
    pub emitted_rows: u64,
    pub skipped_rows: u64,
    pub intermediate_tuples_materialized: u64,
    pub trie_seeks: u64,
    pub trie_seek_comparisons: u64,
    pub trie_nexts: u64,
    pub trie_child_opens: u64,
    pub stop_reason: String,
}

impl StatsView {
    fn from_stats(stats: &EngineStats) -> Self {
        StatsView {
            emitted_rows: stats.emitted_rows,
            skipped_rows: stats.skipped_rows,
            intermediate_tuples_materialized: stats.intermediate_tuples_materialized,
            trie_seeks: stats.seeks,
            trie_seek_comparisons: stats.seek_comparisons,
            trie_nexts: stats.nexts,
            trie_child_opens: stats.child_opens,
            stop_reason: stats.stop_reason.as_str().to_string(),
        }
    }
}

/// Payload of a request that ran successfully enough to answer.
#[derive(Debug)]
pub struct RanPayload {
    pub outcome: QueryOutcome,
    pub typed: Box<TypedChunk>,
    pub record: DecisionRecord,
}

/// The two terminal outcomes of validation/execution. Both variants carry a
/// single boxed payload so the enum stays small and size-balanced.
pub enum ValidateDecision {
    /// Request ran; verdict may still be `undecidable` under an access budget.
    Ran(Box<RanPayload>),
    /// Request was rejected before execution.
    Rejected(Box<DecisionRecord>),
}

impl std::fmt::Debug for ValidateDecision {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ValidateDecision::Ran(payload) => f
                .debug_struct("Ran")
                .field("decision", &payload.outcome.decision)
                .field("request_id", &payload.outcome.request_id)
                .field("row_count", &payload.outcome.row_count)
                .field("stop_reason", &payload.outcome.stats.stop_reason)
                .field("record", &payload.record)
                .finish(),
            ValidateDecision::Rejected(record) => f.debug_tuple("Rejected").field(record).finish(),
        }
    }
}

/// Parse raw JSON bytes into a [`QueryRequest`], distinguishing malformed
/// JSON from schema violations.
pub fn parse_request(bytes: &[u8]) -> Result<QueryRequest> {
    let value: serde_json::Value = serde_json::from_slice(bytes).map_err(|e| {
        ServiceError::new(
            ErrorCode::MalformedJson,
            format!("request body is not valid JSON: {e}"),
        )
    })?;
    parse_value(value)
}

/// Validate a parsed JSON value against the request schema.
pub fn parse_value(value: serde_json::Value) -> Result<QueryRequest> {
    serde_json::from_value(value).map_err(|e| {
        ServiceError::new(
            ErrorCode::InvalidRequest,
            format!("request JSON does not match the query schema: {e}"),
        )
    })
}

/// Convert one typed JSON value to a [`Cell`] at the schema boundary.
fn parse_cell(typ: ColumnType, value: serde_json::Value) -> Result<Cell> {
    if value.is_null() {
        return Ok(Cell::Null);
    }
    let err = || {
        ServiceError::new(
            ErrorCode::InvalidRequest,
            format!("cell {value} is not assignable to {}", typ.as_str()),
        )
    };
    let cell = match typ {
        ColumnType::Int => {
            let v = value.as_i64().ok_or_else(err)?;
            Cell::Value(crate::value::Scalar::Int(v))
        }
        ColumnType::Str => {
            let v = value.as_str().ok_or_else(err)?;
            Cell::Value(crate::value::Scalar::Str(v.to_string()))
        }
        ColumnType::Bool => {
            let v = value.as_bool().ok_or_else(err)?;
            Cell::Value(crate::value::Scalar::Bool(v))
        }
    };
    Ok(cell)
}

/// Parse an inline relation definition, validating arity and cell types.
pub fn parse_relation(spec: RelationSpec) -> Result<Relation> {
    if spec.name.is_empty() {
        return Err(ServiceError::request("relation name must be non-empty"));
    }
    if spec.columns.is_empty() {
        return Err(ServiceError::request(format!(
            "relation '{}' must declare at least one column",
            spec.name
        )));
    }
    let mut columns = Vec::with_capacity(spec.columns.len());
    for c in spec.columns {
        if c.name.is_empty() {
            return Err(ServiceError::request(format!(
                "relation '{}' has an empty column name",
                spec.name
            )));
        }
        columns.push(Column::new(c.name, ColumnType::parse(&c.typ)?));
    }

    let mut relation = Relation::new(spec.name, columns);
    let arity = relation.columns.len();
    let mut rows = Vec::with_capacity(spec.rows.len());
    for (i, raw) in spec.rows.into_iter().enumerate() {
        if raw.len() != arity {
            return Err(ServiceError::new(
                ErrorCode::InvalidRequest,
                format!(
                    "relation '{}' row {i} has {} cells, expected {arity}",
                    relation.name,
                    raw.len()
                ),
            ));
        }
        let mut row = Vec::with_capacity(arity);
        for (col, value) in relation.columns.iter().zip(raw) {
            let cell = parse_cell(col.typ, value).map_err(|e| {
                e.with_field(format!(
                    "relations.{}.rows[{i}].{}",
                    relation.name, col.name
                ))
            })?;
            row.push(cell);
        }
        rows.push(row);
    }
    relation.set_rows(rows)?;
    Ok(relation)
}

/// Register inline relation specs into a catalog.
pub fn load_relations(catalog: &mut Catalog, specs: Vec<RelationSpec>) -> Result<()> {
    for spec in specs {
        catalog.register(parse_relation(spec)?)?;
    }
    Ok(())
}

/// Canonical fingerprint of the query inputs guarding cursor reuse.
fn query_fingerprint(relations: &[Relation], null_policy: NullPolicy, compare_naive: bool) -> u64 {
    let mut signatures = Vec::with_capacity(relations.len());
    for rel in relations {
        let mut hasher = DefaultHasher::new();
        rel.name.hash(&mut hasher);
        for col in &rel.columns {
            col.name.hash(&mut hasher);
            col.typ.as_str().hash(&mut hasher);
        }
        rel.rows.hash(&mut hasher);
        signatures.push(format!("{:016x}", hasher.finish()));
    }
    let _ = compare_naive;
    fingerprint_parts(&signatures, null_policy.as_str())
}

fn relation_states(relations: &[Relation]) -> Vec<RelationState> {
    relations
        .iter()
        .map(|r| RelationState {
            name: r.name.clone(),
            rows: r.rows.len() as u64,
            columns: r.column_names(),
        })
        .collect()
}

/// Run the full validation + execution pipeline.
pub fn execute(config: &Config, state: &AppState, request: QueryRequest) -> ValidateDecision {
    let request_id = RequestId::new();

    match run_inner(config, state, request, &request_id) {
        Ok(build) => build,
        Err(error) => {
            let record = DecisionRecord::rejected(
                &request_id,
                KeyState::default(),
                error.code,
                error.to_string(),
            );
            ValidateDecision::Rejected(Box::new(record))
        }
    }
}

fn run_inner(
    config: &Config,
    state: &AppState,
    request: QueryRequest,
    request_id: &RequestId,
) -> Result<ValidateDecision> {
    if request.relations.is_empty() && request.relation_names.is_empty() {
        return Err(ServiceError::request(
            "provide inline 'relations' and/or catalog 'relation_names'",
        ));
    }

    // Resolve catalog references under a read lock, then parse inline data.
    let mut relations: Vec<Relation> = {
        let catalog = state.catalog.read().expect("catalog lock poisoned");
        let mut resolved = Vec::with_capacity(request.relation_names.len());
        for name in &request.relation_names {
            let rel = catalog
                .get(name)
                .ok_or_else(|| {
                    ServiceError::new(
                        ErrorCode::UnknownRelation,
                        format!("catalog has no relation named '{name}'"),
                    )
                })?
                .clone();
            resolved.push(rel);
        }
        resolved
    };
    for spec in request.relations {
        relations.push(parse_relation(spec)?);
    }

    let null_policy = match &request.null_policy {
        Some(raw) => NullPolicy::parse(raw)?,
        None => NullPolicy::default(),
    };

    if let Some(0) = request.limit {
        return Err(ServiceError::request("limit must be >= 1 when provided"));
    }
    if let Some(budget) = request.access_budget {
        if budget == 0 {
            return Err(ServiceError::request(
                "access_budget must be >= 1 when provided",
            ));
        }
        if matches!(config.max_access_budget, Some(max) if budget > max) {
            return Err(ServiceError::request(format!(
                "access_budget {budget} exceeds server maximum {}",
                config.max_access_budget.unwrap_or(0)
            )));
        }
    }

    let plan = Plan::natural_join(relations, null_policy, config.max_relations)?;
    let fingerprint = query_fingerprint(&plan.relations, null_policy, request.compare_naive);

    let resume: Option<ResumePoint> = match &request.cursor {
        Some(token) => {
            let mut store = state.cursors.lock().expect("cursor lock poisoned");
            Some(store.take(token, fingerprint)?)
        }
        None => None,
    };

    let limit = Some(
        request
            .limit
            .unwrap_or(config.default_limit)
            .min(config.max_limit),
    );
    let engine = JoinEngine::build(plan);
    let output = engine.run(limit, resume, request.access_budget);

    // Optional independent cross-check over the *complete* answer. The oracle
    // runs on a clone of the validated plan but shares no code with the engine.
    let naive = if request.compare_naive {
        let reference_plan = engine.plan().clone();
        let oracle = NaiveOracle::new(&reference_plan).enumerate();
        // Compare against the full one-shot result in case paging or the
        // access budget truncated this response.
        let full = engine.run(None, None, None);
        Some(build_comparison(
            &oracle.rows,
            &full.rows,
            &full.stats,
            &oracle.stats,
        ))
    } else {
        None
    };

    let plan_ref = engine.plan();
    let columns: Vec<(String, ColumnType)> = plan_ref.output_columns.clone();
    let join_attributes: Vec<String> = plan_ref.join_attributes.iter().cloned().collect();
    let relations_state = relation_states(&plan_ref.relations);

    let typed = TypedChunk::from_rows(&columns, &output.rows)?;
    let json_rows: Vec<Vec<serde_json::Value>> = output
        .rows
        .iter()
        .map(|r| r.iter().map(cell_to_json).collect())
        .collect();

    // Persist the next frontier, if any.
    let next_cursor = match &output.next {
        Some(frontier) => {
            let mut store = state.cursors.lock().expect("cursor lock poisoned");
            Some(store.put(fingerprint, frontier.clone()))
        }
        None => None,
    };

    let decision = DecisionRecord::decision_for_stop(output.stats.stop_reason);
    let key_state = KeyState {
        relations: relations_state,
        output_arity: columns.len(),
        join_attributes,
        null_policy: null_policy.as_str().to_string(),
        emitted_rows: output.stats.emitted_rows,
        skipped_rows: output.stats.skipped_rows,
        intermediate_tuples_materialized: 0,
        trie_seeks: output.stats.seeks,
        trie_seek_comparisons: output.stats.seek_comparisons,
        trie_nexts: output.stats.nexts,
        trie_child_opens: output.stats.child_opens,
        stop_reason: Some(output.stats.stop_reason.as_str().to_string()),
    };

    let reasons = match output.stats.stop_reason {
        StopReason::Complete => vec![Reason {
            code: "complete".to_string(),
            detail: "join fully enumerated; result is complete".to_string(),
        }],
        StopReason::OutputLimit => vec![Reason {
            code: "output_limit".to_string(),
            detail: format!(
                "stopped after {} output rows; resume with next_cursor",
                output.stats.emitted_rows
            ),
        }],
        StopReason::BudgetExhausted => vec![Reason {
            code: "access_budget_exhausted".to_string(),
            detail: "trie-access budget spent before completeness was provable; \
                     resume with next_cursor (or rerun without a budget)"
                .to_string(),
        }],
    };

    let outcome = QueryOutcome {
        request_id: request_id.0.clone(),
        decision,
        columns: columns
            .iter()
            .map(|(n, t)| ColumnView {
                name: n.clone(),
                typ: t.as_str().to_string(),
            })
            .collect(),
        rows: json_rows,
        row_count: output.stats.emitted_rows,
        stats: StatsView::from_stats(&output.stats),
        naive,
        next_cursor,
        reasons: reasons.clone(),
    };

    let record = match decision {
        Decision::Accepted => DecisionRecord::accepted(request_id, key_state, reasons),
        Decision::Undecidable => DecisionRecord::undecidable(request_id, key_state, reasons),
        Decision::Rejected => unreachable!("engine runs do not reject here"),
    };

    Ok(ValidateDecision::Ran(Box::new(RanPayload {
        outcome,
        typed: Box::new(typed),
        record,
    })))
}

fn build_comparison(
    _oracle_rows: &[Vec<Cell>],
    engine_rows: &[Vec<Cell>],
    engine_stats: &EngineStats,
    naive_stats: &NaiveStats,
) -> NaiveComparison {
    let _ = engine_stats;
    NaiveComparison {
        matches: multisets_equal(engine_rows, _oracle_rows),
        naive_row_probes: naive_stats.row_probes,
        naive_intermediate_tuples_materialized: naive_stats.intermediate_tuples_materialized,
        naive_emitted_rows: naive_stats.emitted_rows,
    }
}

/// Render a cell as a safe JSON value.
pub fn cell_to_json(cell: &Cell) -> serde_json::Value {
    match cell {
        Cell::Value(crate::value::Scalar::Int(i)) => serde_json::Value::from(*i),
        Cell::Value(crate::value::Scalar::Bool(b)) => serde_json::Value::from(*b),
        Cell::Value(crate::value::Scalar::Str(s)) => serde_json::Value::from(s.as_str()),
        Cell::Null => serde_json::Value::Null,
    }
}
