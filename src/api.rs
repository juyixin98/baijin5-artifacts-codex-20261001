//! Diagnostic HTTP interface (Axum). Structured JSON only — every success
//! and every error is a machine-readable document.

use std::sync::Arc;

use axum::extract::{FromRequest, Path, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use serde::{Deserialize, Serialize};

use crate::error::BackendError;
use crate::folded::{fold, FoldedLine};
use crate::model::{RunMeta, RunState, SampleInput, SymbolEntry};
use crate::naming::Namer;
use crate::stitch::StitchEvidence;
use crate::store::RunStore;
use crate::tree::{build_tree, CallTree, TreeNode};

pub fn router(store: Arc<RunStore>) -> Router {
    Router::new()
        .route("/health", get(health))
        .route("/runs", get(list_runs).post(create_run))
        .route("/runs/{run_id}/samples", post(ingest))
        .route("/runs/{run_id}/seal", post(seal))
        .route("/runs/{run_id}/tree", get(get_tree))
        .route("/runs/{run_id}/folded", get(get_folded))
        .route("/runs/{run_id}/summary", get(get_summary))
        .route("/runs/{run_id}/audit", get(get_audit))
        .with_state(store)
}

impl IntoResponse for BackendError {
    fn into_response(self) -> Response {
        let status = StatusCode::from_u16(self.status()).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
        let body = serde_json::json!({
            "error": {
                "category": self.category,
                "code": self.code,
                "message": self.message,
            }
        });
        (status, Json(body)).into_response()
    }
}

/// JSON extractor that maps malformed bodies into our input-error contract.
struct ApiJson<T>(T);

impl<S, T> FromRequest<S> for ApiJson<T>
where
    S: Send + Sync,
    T: serde::de::DeserializeOwned,
{
    type Rejection = BackendError;

    async fn from_request(req: axum::extract::Request, state: &S) -> Result<Self, Self::Rejection> {
        Json::<T>::from_request(req, state)
            .await
            .map(|Json(v)| ApiJson(v))
            .map_err(|e| BackendError::input("invalid_json", e.to_string()))
    }
}

async fn health() -> Json<serde_json::Value> {
    Json(serde_json::json!({"status": "ok"}))
}

#[derive(Deserialize)]
struct CreateRunRequest {
    #[serde(default)]
    dropped_samples: u64,
    #[serde(default)]
    drop_reason: Option<String>,
    #[serde(default)]
    symbols: Vec<SymbolEntry>,
}

async fn create_run(
    State(store): State<Arc<RunStore>>,
    ApiJson(req): ApiJson<CreateRunRequest>,
) -> Result<Json<RunMeta>, BackendError> {
    Ok(Json(store.create_run(req.symbols, req.dropped_samples, req.drop_reason)?))
}

async fn list_runs(State(store): State<Arc<RunStore>>) -> Json<serde_json::Value> {
    Json(serde_json::json!({"runs": store.list_runs()}))
}

#[derive(Deserialize)]
struct IngestRequest {
    samples: Vec<SampleInput>,
}

async fn ingest(
    State(store): State<Arc<RunStore>>,
    Path(run_id): Path<String>,
    ApiJson(req): ApiJson<IngestRequest>,
) -> Result<Json<serde_json::Value>, BackendError> {
    let report = store.ingest(&run_id, req.samples)?;
    Ok(Json(serde_json::json!({
        "run_id": run_id,
        "accepted": report.accepted,
        "total_samples": report.total_samples,
        "stitched_samples": report.stitched_count,
        "detached_fragments": report.detached_count,
    })))
}

async fn seal(
    State(store): State<Arc<RunStore>>,
    Path(run_id): Path<String>,
) -> Result<Json<RunMeta>, BackendError> {
    Ok(Json(store.seal(&run_id)?))
}

#[derive(Serialize)]
struct NodeDto {
    name: String,
    module: String,
    addr: u64,
    symbol: Option<String>,
    self_weight: f64,
    inclusive_weight: f64,
    children: Vec<NodeDto>,
}

fn node_dto(node: &TreeNode, namer: &Namer) -> NodeDto {
    let mut children: Vec<NodeDto> = node.children.values().map(|c| node_dto(c, namer)).collect();
    // Deterministic, hottest first.
    children.sort_by(|a, b| {
        b.inclusive_weight
            .partial_cmp(&a.inclusive_weight)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| a.module.cmp(&b.module))
            .then_with(|| a.addr.cmp(&b.addr))
    });
    NodeDto {
        name: namer.name(&node.frame.key),
        module: node.frame.key.module.clone(),
        addr: node.frame.key.addr,
        symbol: node.frame.name.clone(),
        self_weight: node.self_weight,
        inclusive_weight: node.inclusive_weight,
        children,
    }
}

fn tree_and_namer(store: &RunStore, run_id: &str) -> Result<(RunState, CallTree, Namer), BackendError> {
    store.with_run(run_id, |run| {
        let tree = build_tree(&run.stitched)?;
        let namer = Namer::build(tree.all_frames().iter());
        Ok((run.meta.state, tree, namer))
    })
}

async fn get_tree(
    State(store): State<Arc<RunStore>>,
    Path(run_id): Path<String>,
) -> Result<Json<serde_json::Value>, BackendError> {
    let (state, tree, namer) = tree_and_namer(&store, &run_id)?;
    let roots: Vec<NodeDto> = tree.roots.values().map(|r| node_dto(r, &namer)).collect();
    Ok(Json(serde_json::json!({
        "run_id": run_id,
        "state": state,
        "total_weight": tree.total_weight,
        "sample_count": tree.sample_count,
        "roots": roots,
    })))
}

async fn get_folded(
    State(store): State<Arc<RunStore>>,
    Path(run_id): Path<String>,
) -> Result<Json<serde_json::Value>, BackendError> {
    let (state, tree, namer) = tree_and_namer(&store, &run_id)?;
    let lines: Vec<FoldedLine> = fold(&tree, &namer);
    Ok(Json(serde_json::json!({
        "run_id": run_id,
        "state": state,
        "total_weight": tree.total_weight,
        "lines": lines,
    })))
}

async fn get_summary(
    State(store): State<Arc<RunStore>>,
    Path(run_id): Path<String>,
) -> Result<Json<serde_json::Value>, BackendError> {
    let (state, tree, namer) = tree_and_namer(&store, &run_id)?;
    let dropped = store.with_run(&run_id, |run| Ok(run.meta.dropped_samples))?;
    let kept = tree.sample_count as u64;
    let keep_ratio = kept as f64 / (kept + dropped) as f64;
    let path_json = |p: Option<crate::tree::PathSummary>| match p {
        Some(p) => serde_json::json!({
            "frames": p.frames.iter().map(|f| namer.name(&f.key)).collect::<Vec<_>>(),
            "weight": p.weight,
        }),
        None => serde_json::json!({"frames": [], "weight": 0.0}),
    };
    let top_self: Vec<serde_json::Value> = tree
        .flat_profile()
        .into_iter()
        .take(10)
        .map(|e| {
            serde_json::json!({
                "name": namer.name(&e.frame.key),
                "module": e.frame.key.module,
                "addr": e.frame.key.addr,
                "self_weight": e.self_weight,
            })
        })
        .collect();
    let explanation = format!(
        "each kept sample's weight is the number of original stacks it represents; \
         total_weight {total} is the sum over {kept} kept samples; \
         {dropped} samples were reported dropped by the collector, so \
         keep_ratio = kept/(kept+dropped) = {ratio:.6}",
        total = tree.total_weight,
        kept = kept,
        dropped = dropped,
        ratio = keep_ratio,
    );
    Ok(Json(serde_json::json!({
        "run_id": run_id,
        "state": state,
        "sample_count": tree.sample_count,
        "total_weight": tree.total_weight,
        "dropped_samples": dropped,
        "keep_ratio": keep_ratio,
        "weight_explanation": explanation,
        "hot_path": path_json(tree.hot_path()),
        "cold_path": path_json(tree.cold_path()),
        "top_self": top_self,
    })))
}

async fn get_audit(
    State(store): State<Arc<RunStore>>,
    Path(run_id): Path<String>,
) -> Result<Json<serde_json::Value>, BackendError> {
    store.with_run(&run_id, |run| {
        let namer = Namer::build(run.stitched.iter().flat_map(|s| s.frames.iter()));
        let entries: Vec<serde_json::Value> = run
            .stitched
            .iter()
            .map(|s| {
                let rationale = match &s.evidence {
                    StitchEvidence::Direct => "synchronous sample; path used as captured".to_string(),
                    StitchEvidence::Stitched { parent_sample_id, token } => format!(
                        "appended to parent {parent_sample_id}: parent_token {token:?} matched its published token"
                    ),
                    StitchEvidence::Detached { task_id, parent_token } => format!(
                        "parent_token {parent_token:?} matched no sample in this run; \
                         anchored under synthetic async_detached(task={task_id}) root"
                    ),
                };
                serde_json::json!({
                    "sample_id": s.sample_id,
                    "weight": s.weight,
                    "depth": s.frames.len(),
                    "path": s.frames.iter().map(|f| namer.name(&f.key)).collect::<Vec<_>>(),
                    "evidence": s.evidence,
                    "rationale": rationale,
                })
            })
            .collect();
        Ok(Json(serde_json::json!({
            "run_id": run.meta.run_id,
            "state": run.meta.state,
            "entries": entries,
        })))
    })
}
