//! Diagnostic HTTP interface (Axum).
//!
//! Endpoints:
//! - `GET  /healthz`                    liveness
//! - `POST /v1/snapshots/ingest`        ingest a snapshot dir or a root of
//!                                      numbered snapshot dirs; body `{"path": "..."}`
//! - `GET  /v1/processes`               known process identities and state
//! - `GET  /v1/processes/{pid}/deltas`  interval deltas for all generations of a pid
//! - `GET  /v1/tree?seq=N`              process tree at sequence N (default: latest)
//! - `GET  /v1/diagnostics`             diagnostic ring buffer (redacted)

use crate::engine::{Engine, IngestReport};
use crate::snapshot;
use crate::store::Store;
use axum::extract::{Path as AxumPath, Query, State};
use axum::http::StatusCode;
use axum::response::Json;
use axum::routing::{get, post};
use axum::Router;
use serde::Deserialize;
use serde_json::{json, Value};
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

pub struct AppState {
    pub engine: Mutex<Engine>,
    pub store: Store,
}

pub fn router(state: Arc<AppState>) -> Router {
    Router::new()
        .route("/healthz", get(healthz))
        .route("/v1/snapshots/ingest", post(ingest))
        .route("/v1/processes", get(processes))
        .route("/v1/processes/{pid}/deltas", get(deltas))
        .route("/v1/tree", get(tree))
        .route("/v1/diagnostics", get(diagnostics))
        .with_state(state)
}

type ApiResult = Result<Json<Value>, (StatusCode, Json<Value>)>;

fn bad_request(message: String) -> (StatusCode, Json<Value>) {
    (
        StatusCode::BAD_REQUEST,
        Json(json!({"error": message})),
    )
}

async fn healthz() -> Json<Value> {
    Json(json!({"status": "ok"}))
}

#[derive(Debug, Deserialize)]
struct IngestRequest {
    path: String,
}

async fn ingest(State(st): State<Arc<AppState>>, Json(req): Json<IngestRequest>) -> ApiResult {
    let path = PathBuf::from(&req.path);
    // A directory containing proc/ is a single snapshot; otherwise treat it
    // as a root of numbered snapshot directories.
    let snapshots = if path.join("proc").is_dir() {
        vec![snapshot::load_snapshot(&path).map_err(|e| bad_request(e.to_string()))?]
    } else {
        snapshot::list_snapshots(&path)
            .map_err(|e| bad_request(e.to_string()))?
            .into_iter()
            .map(|(_, p)| snapshot::load_snapshot(&p).map_err(|e| bad_request(e.to_string())))
            .collect::<Result<Vec<_>, _>>()?
    };
    if snapshots.is_empty() {
        return Err(bad_request(format!(
            "no snapshots found under {}",
            req.path
        )));
    }

    let mut reports: Vec<IngestReport> = Vec::new();
    {
        let mut engine = st.engine.lock().expect("engine mutex poisoned");
        for snap in &snapshots {
            reports.push(engine.ingest(snap));
        }
        if let Err(e) = st.store.save(&engine.state) {
            return Err((
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": e.to_string()})),
            ));
        }
    }
    Ok(Json(json!({"reports": reports})))
}

async fn processes(State(st): State<Arc<AppState>>) -> Json<Value> {
    let engine = st.engine.lock().expect("engine mutex poisoned");
    let list: Vec<Value> = engine
        .state
        .procs
        .values()
        .map(|ps| {
            json!({
                "identity": ps.identity,
                "comm_hash": ps.comm_hash,
                "spawned_seq": ps.spawned_seq,
                "exit": ps.exit,
                "last_cpu_jiffies": ps.last_cpu,
                "last_rss_pages": ps.last_rss,
                "missed_reads": ps.missed_reads,
                "parent_history": ps.parent_history,
            })
        })
        .collect();
    Json(json!({"processes": list}))
}

async fn deltas(
    State(st): State<Arc<AppState>>,
    AxumPath(pid): AxumPath<u32>,
) -> Json<Value> {
    let engine = st.engine.lock().expect("engine mutex poisoned");
    let list: Vec<&crate::delta::IntervalDelta> = engine
        .state
        .deltas
        .iter()
        .filter(|d| d.identity.pid == pid)
        .collect();
    Json(json!({"pid": pid, "deltas": list}))
}

async fn tree(
    State(st): State<Arc<AppState>>,
    Query(params): Query<HashMap<String, String>>,
) -> ApiResult {
    let engine = st.engine.lock().expect("engine mutex poisoned");
    let seq = match params.get("seq") {
        Some(raw) => raw
            .parse::<u64>()
            .map_err(|_| bad_request(format!("invalid seq: {raw}")))?,
        None => engine.state.last_seq.unwrap_or(0),
    };
    let nodes: Vec<_> = engine.tree_at(seq).into_values().collect();
    Ok(Json(json!({"seq": seq, "nodes": nodes})))
}

async fn diagnostics(State(st): State<Arc<AppState>>) -> Json<Value> {
    let engine = st.engine.lock().expect("engine mutex poisoned");
    Json(json!({"diagnostics": engine.state.diags}))
}
