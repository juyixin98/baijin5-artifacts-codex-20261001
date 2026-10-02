//! Request/response DTOs for the diagnostic HTTP interface.

use crate::error::ModelError;
use crate::model::{MapKind, SyncOutcome, TruncateReport, UnmapReport};
use serde::{Deserialize, Serialize};

#[derive(Debug, Deserialize)]
pub struct CreateFileReq {
    pub path: String,
    pub size: u64,
}

#[derive(Debug, Deserialize)]
pub struct OpenFileReq {
    pub path: String,
}

#[derive(Debug, Serialize)]
pub struct FileSizeResp {
    pub path: String,
    pub size: u64,
}

#[derive(Debug, Deserialize)]
pub struct MapReq {
    pub path: String,
    pub offset: u64,
    pub length: u64,
    pub kind: MapKind,
}

#[derive(Debug, Serialize)]
pub struct MapResp {
    pub mapping_id: u64,
}

#[derive(Debug, Deserialize)]
pub struct ReadReq {
    pub offset: u64,
    pub length: usize,
}

#[derive(Debug, Serialize)]
pub struct ReadResp {
    pub data_hex: String,
}

#[derive(Debug, Deserialize)]
pub struct WriteReq {
    pub offset: u64,
    /// Hex-encoded payload (e.g. "deadbeef").
    pub data_hex: String,
}

#[derive(Debug, Deserialize)]
pub struct TruncateReq {
    pub path: String,
    pub size: u64,
}

#[derive(Debug, Deserialize)]
pub struct FileQuery {
    pub path: String,
}

#[derive(Debug, Deserialize)]
pub struct FileContentQuery {
    pub path: String,
    pub offset: u64,
    pub length: usize,
}

#[derive(Debug, Deserialize)]
pub struct FileWriteReq {
    pub path: String,
    pub offset: u64,
    pub data_hex: String,
}

#[derive(Debug, Serialize)]
pub struct SyncResp {
    #[serde(flatten)]
    pub outcome: SyncOutcome,
}

#[derive(Debug, Serialize)]
pub struct TruncateResp {
    #[serde(flatten)]
    pub report: TruncateReport,
}

#[derive(Debug, Serialize)]
pub struct UnmapResp {
    #[serde(flatten)]
    pub report: UnmapReport,
}

#[derive(Debug, Serialize)]
pub struct VersionResp {
    pub name: &'static str,
    pub version: &'static str,
    pub run_id: String,
}

#[derive(Debug, Serialize)]
pub struct ErrorResp {
    pub error: ErrorBody,
}

#[derive(Debug, Serialize)]
pub struct ErrorBody {
    pub category: &'static str,
    pub message: String,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub failed_pages: Vec<u64>,
}

pub fn category_name(e: &ModelError) -> &'static str {
    use crate::error::ErrorCategory::*;
    match e.category {
        AccessOutOfRange => "access_out_of_range",
        InvalidArgument => "invalid_argument",
        NotFound => "not_found",
        Conflict => "conflict",
        SyncFailed => "sync_failed",
        StoreUnavailable => "store_unavailable",
    }
}
