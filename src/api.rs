//! Axum HTTP layer. Errors are serialized with a concrete code and message —
//! never collapsed into a generic success.

use std::sync::Arc;

use axum::{
    extract::State,
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tower::limit::ConcurrencyLimitLayer;
use tracing::{info, warn};
use uuid::Uuid;

use crate::config::Config;
use crate::error::Error;
use crate::query::{run as run_query, Expr};
use crate::state::Catalog;

#[derive(Clone)]
pub struct AppState {
    pub catalog: Arc<std::sync::RwLock<Catalog>>,
    pub config: Arc<Config>,
}

pub fn router(state: AppState) -> Router {
    Router::new()
        .route("/health", get(health))
        .route("/tables", get(list_tables).post(create_table))
        .route("/tables/:name", get(get_table))
        .route("/tables/:name/rows", post(append_rows))
        .route("/tables/:name/delete", post(delete_rows))
        .route("/tables/:name/query", post(query_table))
        // Bound resource use for the in-memory service.
        .layer(ConcurrencyLimitLayer::new(256))
        .with_state(state)
}

async fn health() -> Json<Value> {
    Json(
        json!({ "status": "ok", "service": "tribool-index", "version": env!("CARGO_PKG_VERSION") }),
    )
}

// ---------- request / response DTOs ----------

#[derive(Debug, Deserialize)]
struct CreateTableReq {
    name: String,
    columns: Vec<crate::batch::ColumnSpec>,
    #[serde(default)]
    rows: Vec<serde_json::Map<String, Value>>,
}

#[derive(Debug, Deserialize)]
struct AppendReq {
    rows: Vec<serde_json::Map<String, Value>>,
}

#[derive(Debug, Deserialize)]
struct DeleteReq {
    #[serde(default)]
    ids: Vec<usize>,
}

#[derive(Debug, Deserialize)]
struct QueryReq {
    filter: Expr,
}

#[derive(Debug, Serialize)]
struct ErrorBody {
    ok: bool,
    error: ErrorDetail,
    run_id: String,
}

#[derive(Debug, Serialize)]
struct ErrorDetail {
    code: String,
    message: String,
}

impl IntoResponse for Error {
    fn into_response(self) -> Response {
        let run_id = Uuid::new_v4().to_string();
        warn!(run_id = %run_id, error = %self, "request failed");
        let status =
            StatusCode::from_u16(self.http_status()).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
        let body = ErrorBody {
            ok: false,
            error: ErrorDetail {
                code: self.code().to_string(),
                message: self.message.clone(),
            },
            run_id,
        };
        (status, Json(json!(body))).into_response()
    }
}

async fn list_tables(State(st): State<AppState>) -> Result<Json<Value>, Error> {
    let catalog = st.catalog.read().unwrap();
    Ok(Json(json!({ "ok": true, "tables": catalog.table_names() })))
}

async fn create_table(
    State(st): State<AppState>,
    Json(req): Json<CreateTableReq>,
) -> Result<Json<Value>, Error> {
    if req.name.is_empty() {
        return Err(Error::invalid("table name must not be empty"));
    }
    if req.columns.is_empty() {
        return Err(Error::invalid("table must declare at least one column"));
    }
    if req.rows.len() > st.config.query.max_append_rows {
        return Err(Error::invalid(format!(
            "{} rows exceed max_append_rows {}",
            req.rows.len(),
            st.config.query.max_append_rows
        )));
    }
    let mut catalog = st.catalog.write().unwrap();
    let table = catalog.create_table(&req.name, req.columns, req.rows)?;
    let info = table.read().unwrap().version_info();
    info!(table = %req.name, version = info.version, rows = info.total_rows, "table created");
    Ok(Json(
        json!({ "ok": true, "table": req.name, "version": info }),
    ))
}

async fn get_table(
    State(st): State<AppState>,
    axum::extract::Path(name): axum::extract::Path<String>,
) -> Result<Json<Value>, Error> {
    let catalog = st.catalog.read().unwrap();
    let table = catalog.table(&name)?;
    let t = table.read().unwrap();
    Ok(Json(json!({
        "ok": true,
        "table": t.name,
        "columns": t.spec,
        "version": t.version_info(),
    })))
}

async fn append_rows(
    State(st): State<AppState>,
    axum::extract::Path(name): axum::extract::Path<String>,
    Json(req): Json<AppendReq>,
) -> Result<Json<Value>, Error> {
    if req.rows.len() > st.config.query.max_append_rows {
        return Err(Error::invalid(format!(
            "{} rows exceed max_append_rows {}",
            req.rows.len(),
            st.config.query.max_append_rows
        )));
    }
    let catalog = st.catalog.read().unwrap();
    let table = catalog.table(&name)?;
    let mut t = table.write().unwrap();
    let info = t.append(&req.rows)?;
    info!(table = %name, version = info.version, total = info.total_rows, "rows appended");
    Ok(Json(json!({ "ok": true, "version": info })))
}

async fn delete_rows(
    State(st): State<AppState>,
    axum::extract::Path(name): axum::extract::Path<String>,
    Json(req): Json<DeleteReq>,
) -> Result<Json<Value>, Error> {
    if req.ids.is_empty() {
        return Err(Error::invalid("delete requires at least one row id"));
    }
    let catalog = st.catalog.read().unwrap();
    let table = catalog.table(&name)?;
    let mut t = table.write().unwrap();
    let info = t.delete_rows(&req.ids)?;
    info!(table = %name, version = info.version, deleted = info.deleted_rows, ids = ?req.ids, "versioned delete committed");
    Ok(Json(
        json!({ "ok": true, "version": info, "deleted_ids": req.ids }),
    ))
}

async fn query_table(
    State(st): State<AppState>,
    axum::extract::Path(name): axum::extract::Path<String>,
    Json(req): Json<QueryReq>,
) -> Result<Json<Value>, Error> {
    let nodes = count_nodes(&req.filter);
    if nodes > st.config.query.max_expr_nodes {
        return Err(Error::invalid(format!(
            "filter has {nodes} nodes, exceeding max_expr_nodes {}",
            st.config.query.max_expr_nodes
        )));
    }
    let run_id = Uuid::new_v4().to_string();
    let catalog = st.catalog.read().unwrap();
    let table = catalog.table(&name)?;
    // Hold the read lock across planning so the version can't change mid-query.
    let t = table.read().unwrap();
    let result = run_query(&t, &req.filter, &run_id)?;
    let resp = json!({
        "ok": true,
        "run_id": run_id,
        "table": name,
        "version": result.version,
        "counts": {
            "true": result.tris.count_true(),
            "false": result.tris.count_false(),
            "unknown": result.tris.count_unknown(),
        },
        "matched_ids": result.matched_ids(),
        "trace": if st.config.log.include_trace { json!(result.trace) } else { json!(null) },
    });
    Ok(Json(resp))
}

fn count_nodes(expr: &Expr) -> usize {
    1 + match expr {
        Expr::Cmp { .. } => 0,
        Expr::Not { arg } => count_nodes(arg),
        Expr::And { args } | Expr::Or { args } => args.iter().map(count_nodes).sum::<usize>(),
    }
}
