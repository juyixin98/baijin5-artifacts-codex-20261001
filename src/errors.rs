//! 模块间错误契约。
//!
//! 四类错误被**显式区分**（对应需求：输入错误、状态冲突、资源耗尽、计算失败），
//! 页表遍历时的故障再单列 [`FaultKind`]（对应需求：输出故障类型而非 panic）。

use serde::Serialize;

/// 页故障类别——页表遍历成功到达一条 PTE 但无法完成访问时返回。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum FaultKind {
    /// PTE 的 V=0（不存在/非叶子）。
    PageNotPresent,
    /// 叶子页存在但缺少所请求的 R/W/X 权限。
    PermissionDenied,
    /// PTE 中保留位非法（如非对齐的叶子 PPN、必须为 0 的位被置位）。
    ReservedFault,
    /// 遍历中途遇到表项却不是有效指针（V=0）。
    Miss,
}

impl FaultKind {
    pub fn as_str(self) -> &'static str {
        match self {
            FaultKind::PageNotPresent => "page_not_present",
            FaultKind::PermissionDenied => "permission_denied",
            FaultKind::ReservedFault => "reserved_fault",
            FaultKind::Miss => "miss",
        }
    }
}

/// 页故障详情（携带足够的诊断信息，不 panic）。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct PageFault {
    pub kind: FaultKind,
    /// 触发故障的虚拟地址。
    pub vaddr: u64,
    /// ASID。
    pub asid: u16,
    /// 故障发生在第几级遍历（0..=3）。
    pub level: u8,
    /// 该级索引。
    pub index: u16,
    /// 读到的原始 PTE 值。
    pub pte: u64,
    pub reason: String,
}

impl std::fmt::Display for PageFault {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "{} at vaddr={:#x} asid={} level={} index={} pte={:#x}: {}",
            self.kind.as_str(),
            self.vaddr,
            self.asid,
            self.level,
            self.index,
            self.pte,
            self.reason
        )
    }
}

impl std::error::Error for PageFault {}

/// 服务级错误。`kind` 字段即四类错误的稳定机器名。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ServiceError {
    /// 输入错误：参数非法、JSON 无法解析、地址越界等调用方问题。
    Input(String),
    /// 状态冲突：进程已存在/不存在、映射冲突、失效协议被违反等。
    Conflict(String),
    /// 资源耗尽：物理帧池用尽、进程数/TLB 容量触顶。
    Exhausted(String),
    /// 计算失败：内部不一致（不应发生），与前两类输入侧错误区分。
    Compute(String),
}

impl ServiceError {
    pub fn kind(&self) -> &'static str {
        match self {
            ServiceError::Input(_) => "input_error",
            ServiceError::Conflict(_) => "conflict",
            ServiceError::Exhausted(_) => "resource_exhausted",
            ServiceError::Compute(_) => "compute_failure",
        }
    }

    pub fn message(&self) -> &str {
        match self {
            ServiceError::Input(m)
            | ServiceError::Conflict(m)
            | ServiceError::Exhausted(m)
            | ServiceError::Compute(m) => m,
        }
    }
}

impl std::fmt::Display for ServiceError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}: {}", self.kind(), self.message())
    }
}

impl std::error::Error for ServiceError {}

pub type Result<T> = std::result::Result<T, ServiceError>;

/// 对 HTTP 层暴露的稳定错误体。
#[derive(Debug, Serialize)]
pub struct ErrorBody {
    pub error: String,
    pub kind: String,
    pub message: String,
}
