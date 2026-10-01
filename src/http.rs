//! Axum HTTP layer. Thin adapter: parsing, status-code mapping, and request
//! correlation ids. All semantics live in [`crate::service::QueryService`].

use axum::extract::{Json as AxumJson, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::Router;
use serde_json::json;
use tracing::warn;
use uuid::Uuid;

use crate::ast::{LoadFixturesRequest, QueryFailure, QueryRequest, StepTrace};
use crate::error::FailureCategory;
use crate::service::{engine_version, QueryService};

#[derive(Clone)]
pub struct HttpState {
    pub service: QueryService,
}

pub fn router(service: QueryService) -> Router {
    Router::new()
        .route("/health", get(health))
        .route("/", get(index))
        .route("/v1/fixtures", post(load_fixtures))
        .route("/v1/query", post(query))
        .with_state(HttpState { service })
}

async fn health() -> AxumJson<serde_json::Value> {
    AxumJson(json!({
        "status": "ok",
        "engine_version": engine_version(),
    }))
}

async fn index() -> AxumJson<serde_json::Value> {
    AxumJson(json!({
        "service": "subquery-decorrelation",
        "engine_version": engine_version(),
        "endpoints": {
            "health": "GET /health",
            "load_fixtures": "POST /v1/fixtures",
            "query": "POST /v1/query",
        },
        "supported_ops": ["exists", "not_exists", "scalar", "scalar_aggregate:count_star|sum"],
        "rejected_ops": ["not_in", "in", "any", "all"],
    }))
}

async fn load_fixtures(
    State(state): State<HttpState>,
    body: Result<AxumJson<LoadFixturesRequest>, axum::extract::rejection::JsonRejection>,
) -> Response {
    let AxumJson(req) = match body {
        Ok(parsed) => parsed,
        Err(rej) => return parse_failure(rej),
    };
    match state.service.load_fixtures(&req) {
        Ok(report) => (StatusCode::OK, AxumJson(report)).into_response(),
        Err(err) => simple_failure(err),
    }
}

async fn query(
    State(state): State<HttpState>,
    body: Result<AxumJson<QueryRequest>, axum::extract::rejection::JsonRejection>,
) -> Response {
    let AxumJson(req) = match body {
        Ok(parsed) => parsed,
        Err(rej) => return parse_failure(rej),
    };
    match state.service.run_query(req) {
        Ok(success) => (StatusCode::OK, AxumJson(success)).into_response(),
        Err(failure) => failure_response(*failure),
    }
}

fn status_for(category: FailureCategory) -> StatusCode {
    match category {
        FailureCategory::Execution => StatusCode::INTERNAL_SERVER_ERROR,
        // The query compiled but its form/data is unsupported or invalid, or a
        // runtime cardinality rule was violated: 422 Unprocessable Entity.
        FailureCategory::UnsupportedForm
        | FailureCategory::Validation
        | FailureCategory::Schema
        | FailureCategory::CardinalityViolation => StatusCode::UNPROCESSABLE_ENTITY,
    }
}

fn failure_response(failure: QueryFailure) -> Response {
    let category = parse_category(failure.failure.category);
    warn!(
        request_id = %failure.request_id,
        category = failure.failure.category,
        "request failed"
    );
    (status_for(category), AxumJson(failure)).into_response()
}

fn simple_failure(err: crate::error::EngineError) -> Response {
    let category = err.category();
    let request_id = Uuid::new_v4().to_string();
    warn!(%request_id, category = category.code(), error = %err, "request failed");
    let body = json!({
        "request_id": request_id,
        "engine_version": engine_version(),
        "failure": err.to_failure(),
        "trace": Vec::<StepTrace>::new(),
        "uncertainties": [],
    });
    (status_for(category), AxumJson(body)).into_response()
}

fn parse_failure(rej: axum::extract::rejection::JsonRejection) -> Response {
    let request_id = Uuid::new_v4().to_string();
    warn!(%request_id, error = %rej, "request body could not be parsed");
    let body = json!({
        "request_id": request_id,
        "engine_version": engine_version(),
        "failure": {
            "category": FailureCategory::Validation.code(),
            "message": rej.body_text(),
            "location": Some("request_body"),
            "unsupported_form": null,
            "reason": Some("JSON syntax/shape error or an unknown field (the DTO rejects unknown fields)"),
            "remediation": Some("correct the request body to match the documented schema"),
        },
        "trace": [],
        "uncertainties": [],
    });
    (StatusCode::BAD_REQUEST, AxumJson(body)).into_response()
}

fn parse_category(code: &str) -> FailureCategory {
    match code {
        "unsupported_form" => FailureCategory::UnsupportedForm,
        "validation_error" => FailureCategory::Validation,
        "schema_error" => FailureCategory::Schema,
        "scalar_cardinality_violation" => FailureCategory::CardinalityViolation,
        _ => FailureCategory::Execution,
    }
}
