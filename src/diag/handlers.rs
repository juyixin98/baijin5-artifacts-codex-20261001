//! Axum handlers: thin translation between HTTP/JSON and the [`Vm`] model.

use super::dto::*;
use crate::error::{ErrorCategory, ModelError};
use crate::model::Vm;
use crate::store::BackingStore;
use axum::extract::{Path, Query, State};
use axum::http::StatusCode;
use axum::Json;
use std::sync::{Arc, Mutex};

pub type Shared<S> = Arc<Mutex<Vm<S>>>;

type ApiResult<T> = Result<Json<T>, (StatusCode, Json<ErrorResp>)>;

/// For endpoints that return 204 No Content on success.
type ApiStatus = Result<StatusCode, (StatusCode, Json<ErrorResp>)>;

fn api_err(e: ModelError) -> (StatusCode, Json<ErrorResp>) {
    let status = match e.category {
        ErrorCategory::InvalidArgument => StatusCode::BAD_REQUEST,
        ErrorCategory::NotFound => StatusCode::NOT_FOUND,
        ErrorCategory::Conflict | ErrorCategory::AccessOutOfRange => StatusCode::CONFLICT,
        ErrorCategory::SyncFailed => StatusCode::INTERNAL_SERVER_ERROR,
        ErrorCategory::StoreUnavailable => StatusCode::SERVICE_UNAVAILABLE,
    };
    (
        status,
        Json(ErrorResp {
            error: ErrorBody {
                category: category_name(&e),
                message: e.message.clone(),
                failed_pages: e.failed_pages.clone(),
            },
        }),
    )
}

pub async fn healthz() -> &'static str {
    "ok"
}

pub async fn version<S: BackingStore>(State(vm): State<Shared<S>>) -> Json<VersionResp> {
    let _ = vm;
    Json(VersionResp {
        name: env!("CARGO_PKG_NAME"),
        version: env!("CARGO_PKG_VERSION"),
        run_id: super::run_id(),
    })
}

pub async fn stats<S: BackingStore>(State(vm): State<Shared<S>>) -> Json<crate::model::Stats> {
    Json(vm.lock().unwrap().stats())
}

pub async fn create_file<S: BackingStore>(
    State(vm): State<Shared<S>>,
    Json(req): Json<CreateFileReq>,
) -> ApiResult<FileSizeResp> {
    let mut vm = vm.lock().unwrap();
    vm.create_file(&req.path, req.size).map_err(api_err)?;
    Ok(Json(FileSizeResp {
        path: req.path,
        size: req.size,
    }))
}

pub async fn open_file<S: BackingStore>(
    State(vm): State<Shared<S>>,
    Json(req): Json<OpenFileReq>,
) -> ApiResult<FileSizeResp> {
    let mut vm = vm.lock().unwrap();
    let size = vm.open_file(&req.path).map_err(api_err)?;
    Ok(Json(FileSizeResp {
        path: req.path,
        size,
    }))
}

pub async fn file_state<S: BackingStore>(
    State(vm): State<Shared<S>>,
    Query(q): Query<FileQuery>,
) -> ApiResult<crate::model::FileState> {
    vm.lock()
        .unwrap()
        .file_state(&q.path)
        .map(Json)
        .map_err(api_err)
}

pub async fn file_content<S: BackingStore>(
    State(vm): State<Shared<S>>,
    Query(q): Query<FileContentQuery>,
) -> ApiResult<ReadResp> {
    let data = vm
        .lock()
        .unwrap()
        .store_image(&q.path, q.offset, q.length)
        .map_err(api_err)?;
    Ok(Json(ReadResp {
        data_hex: hex::encode(data),
    }))
}

/// Direct persistent-image write (external-writer fixture; bypasses cache).
pub async fn file_write<S: BackingStore>(
    State(vm): State<Shared<S>>,
    Json(req): Json<FileWriteReq>,
) -> ApiStatus {
    let data = hex::decode(&req.data_hex).map_err(|e| {
        api_err(ModelError::new(
            ErrorCategory::InvalidArgument,
            format!("bad hex: {e}"),
        ))
    })?;
    vm.lock()
        .unwrap()
        .write_file_direct(&req.path, req.offset, &data)
        .map_err(api_err)?;
    Ok(StatusCode::NO_CONTENT)
}

pub async fn truncate_file<S: BackingStore>(
    State(vm): State<Shared<S>>,
    Json(req): Json<TruncateReq>,
) -> ApiResult<TruncateResp> {
    let report = vm
        .lock()
        .unwrap()
        .truncate(&req.path, req.size)
        .map_err(api_err)?;
    Ok(Json(TruncateResp { report }))
}

pub async fn create_mapping<S: BackingStore>(
    State(vm): State<Shared<S>>,
    Json(req): Json<MapReq>,
) -> ApiResult<MapResp> {
    let id = vm
        .lock()
        .unwrap()
        .map(&req.path, req.offset, req.length, req.kind)
        .map_err(api_err)?;
    Ok(Json(MapResp { mapping_id: id }))
}

pub async fn mapping_state<S: BackingStore>(
    State(vm): State<Shared<S>>,
    Path(id): Path<u64>,
) -> ApiResult<crate::model::MappingState> {
    vm.lock()
        .unwrap()
        .mapping_state(id)
        .map(Json)
        .map_err(api_err)
}

pub async fn mapping_read<S: BackingStore>(
    State(vm): State<Shared<S>>,
    Path(id): Path<u64>,
    Json(req): Json<ReadReq>,
) -> ApiResult<ReadResp> {
    let data = vm
        .lock()
        .unwrap()
        .read(id, req.offset, req.length)
        .map_err(api_err)?;
    Ok(Json(ReadResp {
        data_hex: hex::encode(data),
    }))
}

pub async fn mapping_write<S: BackingStore>(
    State(vm): State<Shared<S>>,
    Path(id): Path<u64>,
    Json(req): Json<WriteReq>,
) -> ApiStatus {
    let data = hex::decode(&req.data_hex).map_err(|e| {
        api_err(ModelError::new(
            ErrorCategory::InvalidArgument,
            format!("bad hex: {e}"),
        ))
    })?;
    vm.lock()
        .unwrap()
        .write(id, req.offset, &data)
        .map_err(api_err)?;
    Ok(StatusCode::NO_CONTENT)
}

pub async fn mapping_sync<S: BackingStore>(
    State(vm): State<Shared<S>>,
    Path(id): Path<u64>,
) -> ApiResult<SyncResp> {
    let outcome = vm.lock().unwrap().sync(id).map_err(api_err)?;
    Ok(Json(SyncResp { outcome }))
}

pub async fn mapping_unmap<S: BackingStore>(
    State(vm): State<Shared<S>>,
    Path(id): Path<u64>,
) -> ApiResult<UnmapResp> {
    let report = vm.lock().unwrap().unmap(id).map_err(api_err)?;
    Ok(Json(UnmapResp { report }))
}
