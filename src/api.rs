//! HTTP layer (Axum): request/response DTOs, routing and error mapping.
//!
//! Error responses always carry a machine-readable `error_kind` so a client
//! can distinguish a bad query, an unknown column, a type mismatch and a
//! universe/version conflict. An exception never comes back as `success`.

use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use serde_json::json;

use crate::error::TviError;
use crate::query::executor::{self, Catalog};
use crate::query::Expr;
use crate::state::AppState;

/// Build the application router.
pub fn router(state: AppState) -> Router {
    Router::new()
        .route("/healthz", get(healthz))
        .route("/schema", get(schema))
        .route("/query", post(query))
        .with_state(state)
}

async fn healthz() -> Json<serde_json::Value> {
    Json(json!({ "status": "ok" }))
}

async fn schema(
    axum::extract::State(s): axum::extract::State<AppState>,
) -> Json<serde_json::Value> {
    let cols: Vec<serde_json::Value> = s
        .store
        .table
        .columns
        .iter()
        .map(|c| json!({ "name": c.name, "type": c.logical.arrow_name() }))
        .collect();
    Json(json!({
        "table": s.store.manifest.table,
        "rows": s.store.table.len(),
        "content_version": s.store.versions.index_version(),
        "head_version": s.store.versions.head_version(),
        "live_rows": s.store.versions.live_bitmap().count_ones(),
        "columns": cols,
    }))
}

#[derive(Debug, serde::Deserialize)]
struct QueryRequest {
    #[serde(default)]
    as_of: Option<u64>,
    #[serde(rename = "where")]
    where_expr: Option<serde_json::Value>,
}

async fn query(
    axum::extract::State(s): axum::extract::State<AppState>,
    body: axum::body::Bytes,
) -> Response {
    // Parse JSON ourselves so a malformed/oversized body becomes our own
    // categorised failure envelope instead of an opaque framework error.
    let req: QueryRequest = match serde_json::from_slice(&body) {
        Ok(req) => req,
        Err(e) => {
            return AppError(TviError::InvalidQuery(format!(
                "request body is not valid query JSON: {e}"
            )))
            .into_response();
        }
    };
    match run_query(&s, req) {
        Ok(body) => (StatusCode::OK, Json(body)).into_response(),
        Err(e) => AppError(e).into_response(),
    }
}

fn run_query(s: &AppState, req: QueryRequest) -> Result<serde_json::Value, TviError> {
    let run_id = executor::next_run_id();
    let expr_node = req.where_expr.ok_or_else(|| {
        TviError::InvalidQuery("request body needs a `where` predicate object".into())
    })?;
    let expr = Expr::parse(&expr_node)?;

    let catalog = Catalog::new(s.store.indexes(), &s.store.versions)?;
    let outcome = executor::evaluate(&catalog, &expr, req.as_of, run_id)?;

    let rows: Vec<serde_json::Value> = outcome
        .classifications
        .iter()
        .map(|(row, cls)| {
            json!({
                "row": row,
                "verdict": match cls {
                    'T' => "true",
                    'F' => "false",
                    _ => "unknown",
                },
                "selected": *cls == 'T',
            })
        })
        .collect();

    Ok(json!({
        "success": true,
        "run_id": run_id,
        "expr": expr.describe(),
        "as_of": outcome.as_of,
        "content_version": outcome.index_version,
        "universe": {
            "total": outcome.universe_total,
            "live": outcome.live_total,
            "deleted_rows": outcome.deleted_rows,
        },
        "counts": {
            "true": outcome.true_rows.len(),
            "false": outcome.false_rows.len(),
            "unknown": outcome.unknown_rows.len(),
        },
        "selected_rows": outcome.true_rows,
        "true_rows": outcome.true_rows,
        "false_rows": outcome.false_rows,
        "unknown_rows": outcome.unknown_rows,
        "rows": rows,
    }))
}

/// Error wrapper mapping [`TviError`] to a categorised HTTP response.
struct AppError(TviError);

impl IntoResponse for AppError {
    fn into_response(self) -> Response {
        let (status, kind) = match &self.0 {
            TviError::UnknownColumn(_) => (StatusCode::NOT_FOUND, "unknown_column"),
            TviError::TypeMismatch { .. } | TviError::InvalidLiteral { .. } => {
                (StatusCode::BAD_REQUEST, "type_error")
            }
            TviError::InvalidQuery(_) => (StatusCode::BAD_REQUEST, "invalid_query"),
            TviError::UniverseMismatch { .. } => {
                (StatusCode::CONFLICT, "universe_version_mismatch")
            }
            TviError::Arrow(_) | TviError::Io(_) => {
                (StatusCode::INTERNAL_SERVER_ERROR, "data_error")
            }
        };
        let body = json!({
            "success": false,
            "error_kind": kind,
            "message": self.0.to_string(),
        });
        tracing::warn!(error_kind = kind, error = %self.0, "request failed");
        (status, Json(body)).into_response()
    }
}

/// Body-size limit error is produced by axum's `DefaultBodyLimit`; map it in
/// main via a small middleware-free setup. This helper builds the full router
/// with the limit applied.
pub fn limited_router(state: AppState, max_bytes: usize) -> Router {
    use axum::extract::DefaultBodyLimit;
    router(state).layer(DefaultBodyLimit::max(max_bytes))
}
