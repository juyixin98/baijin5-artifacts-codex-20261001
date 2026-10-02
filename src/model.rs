//! 核心运行模型：块请求、取消请求、设备耗时模型与校验错误类别。
//!
//! 边界声明：
//! - 所有扇区地址都是“逻辑扇区号（LBA）”，范围 `[lba, lba + sectors)`，方向显式为读或写。
//! - `DeviceModel` 是一个**可控的合成机械寻道模型**（寻道 + 传输），用于产生可复现的
//!   服务耗时。它不是、也不能被当作 SSD 的实测延迟。

use serde::{Deserialize, Serialize};

/// 请求方向。合并只允许同方向相邻请求。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Direction {
    Read,
    Write,
}

impl Direction {
    pub fn as_str(&self) -> &'static str {
        match self {
            Direction::Read => "read",
            Direction::Write => "write",
        }
    }
}

/// 一条待调度的块请求（输入规格）。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RequestSpec {
    /// 调用方给定的请求身份，全程贯穿日志、事件与完成记录。
    pub id: String,
    /// 起始逻辑扇区。
    pub lba: u64,
    /// 扇区数，必须 > 0。
    pub sectors: u64,
    pub direction: Direction,
    /// 到达时刻（虚拟毫秒，从 0 起的仿真时钟）。
    pub arrival_ms: u64,
    /// 相对到达时刻的截止期限（毫秒）。仅 deadline 调度器使用；
    /// 缺省时由调度器配置给默认值。
    pub deadline_ms: Option<u64>,
}

/// 一条取消请求：在 `at_ms` 时刻尝试取消 `request_id`。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CancelSpec {
    pub request_id: String,
    pub at_ms: u64,
}

/// 一条轨迹：请求 + 取消。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Trace {
    pub requests: Vec<RequestSpec>,
    #[serde(default)]
    pub cancels: Vec<CancelSpec>,
}

/// 可控的合成设备耗时模型（机械盘式：寻道 + 传输）。
///
/// 服务耗时 = seek_base_ms + |目标磁道 - 当前磁道| * seek_ms_per_track
///          + sectors * transfer_ms_per_sector
/// 其中磁道号 = lba / sectors_per_track。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DeviceModel {
    pub capacity_sectors: u64,
    pub sectors_per_track: u64,
    pub seek_base_ms: f64,
    pub seek_ms_per_track: f64,
    pub transfer_ms_per_sector: f64,
}

impl Default for DeviceModel {
    fn default() -> Self {
        // 默认值是教学用的合成参数，不对应任何真实硬件。
        DeviceModel {
            capacity_sectors: 1_048_576, // 512MiB @ 512B
            sectors_per_track: 256,
            seek_base_ms: 0.5,
            seek_ms_per_track: 0.02,
            transfer_ms_per_sector: 0.01,
        }
    }
}

impl DeviceModel {
    pub fn validate(&self) -> Result<(), ModelError> {
        if self.capacity_sectors == 0 {
            return Err(ModelError::new(
                ErrorCategory::InvalidDeviceModel,
                "capacity_sectors must be > 0",
            ));
        }
        if self.sectors_per_track == 0 {
            return Err(ModelError::new(
                ErrorCategory::InvalidDeviceModel,
                "sectors_per_track must be > 0",
            ));
        }
        if self.seek_base_ms < 0.0
            || self.seek_ms_per_track < 0.0
            || self.transfer_ms_per_sector < 0.0
        {
            return Err(ModelError::new(
                ErrorCategory::InvalidDeviceModel,
                "device time coefficients must be >= 0",
            ));
        }
        Ok(())
    }

    /// 磁头从 `from_lba` 移动到服务 `req` 所需的总耗时（毫秒）。
    pub fn service_time_ms(&self, from_lba: u64, lba: u64, sectors: u64) -> f64 {
        let from_track = from_lba / self.sectors_per_track;
        let to_track = lba / self.sectors_per_track;
        let seek_tracks = from_track.abs_diff(to_track) as f64;
        self.seek_base_ms
            + seek_tracks * self.seek_ms_per_track
            + sectors as f64 * self.transfer_ms_per_sector
    }
}

/// 输入校验错误类别。每个类别对应一种明确的失败语义（见 README）。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ErrorCategory {
    /// 扇区范围越界（lba + sectors 超出设备容量）。
    SectorOutOfRange,
    /// 扇区数为 0。
    EmptyRequest,
    /// 请求 id 重复。
    DuplicateRequestId,
    /// 取消目标不存在。
    UnknownRequest,
    /// 设备模型参数非法。
    InvalidDeviceModel,
    /// 请求体结构非法（缺字段、类型错误等）。
    MalformedRequest,
    /// 找不到指定的运行记录。
    RunNotFound,
    /// 找不到指定的夹具。
    FixtureNotFound,
}

/// 模型层错误：类别 + 可解释消息 + 关联请求身份（如有）。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelError {
    pub category: ErrorCategory,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub request_id: Option<String>,
}

impl ModelError {
    pub fn new(category: ErrorCategory, message: impl Into<String>) -> Self {
        ModelError {
            category,
            message: message.into(),
            request_id: None,
        }
    }

    pub fn for_request(
        category: ErrorCategory,
        message: impl Into<String>,
        request_id: &str,
    ) -> Self {
        ModelError {
            category,
            message: message.into(),
            request_id: Some(request_id.to_string()),
        }
    }
}

/// 校验整条轨迹，返回全部错误（不是遇到第一个就停），便于调用方一次看清。
pub fn validate_trace(trace: &Trace, device: &DeviceModel) -> Vec<ModelError> {
    let mut errors = Vec::new();
    let mut seen = std::collections::HashSet::new();
    for req in &trace.requests {
        if req.sectors == 0 {
            errors.push(ModelError::for_request(
                ErrorCategory::EmptyRequest,
                "sectors must be > 0",
                &req.id,
            ));
            continue;
        }
        let end = req.lba.saturating_add(req.sectors);
        if end > device.capacity_sectors {
            errors.push(ModelError::for_request(
                ErrorCategory::SectorOutOfRange,
                format!(
                    "range [{}, {}) exceeds device capacity {} sectors",
                    req.lba, end, device.capacity_sectors
                ),
                &req.id,
            ));
        }
        if !seen.insert(req.id.clone()) {
            errors.push(ModelError::for_request(
                ErrorCategory::DuplicateRequestId,
                "duplicate request id in trace",
                &req.id,
            ));
        }
    }
    for cancel in &trace.cancels {
        if !seen.contains(&cancel.request_id) {
            errors.push(ModelError::for_request(
                ErrorCategory::UnknownRequest,
                "cancel targets a request id not present in the trace",
                &cancel.request_id,
            ));
        }
    }
    errors
}
