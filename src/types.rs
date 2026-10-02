//! 跨模块共享的数据类型：权限、PTE、翻译请求/结果。

use crate::config::{
    split_vpn, PTE_D, PTE_G, PTE_PPN_SHIFT, PTE_PS, PTE_R, PTE_RESERVED_MASK, PTE_V, PTE_W, PTE_X,
};
use serde::{Deserialize, Serialize};

/// 访问类型（权限检查的粒度）。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum AccessKind {
    /// 取指
    Fetch,
    /// 读（含加载）
    Read,
    /// 写（含存储）
    Write,
}

impl AccessKind {
    pub fn as_str(self) -> &'static str {
        match self {
            AccessKind::Fetch => "fetch",
            AccessKind::Read => "read",
            AccessKind::Write => "write",
        }
    }
}

/// 叶子映射的权限位。三者可任意组合，但映射必须至少授予一种权限
/// （R=W=X=0 的叶子在本模型中按故障处理，类 RISC-V 保留编码）。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct Permissions {
    pub read: bool,
    pub write: bool,
    pub execute: bool,
}

impl Permissions {
    pub const fn new(read: bool, write: bool, execute: bool) -> Self {
        Self {
            read,
            write,
            execute,
        }
    }
    pub const fn rwx() -> Self {
        Self::new(true, true, true)
    }
    pub const fn ro() -> Self {
        Self::new(true, false, true)
    }
    pub const fn none() -> Self {
        Self::new(false, false, false)
    }

    /// 判断该权限集合是否允许指定访问。
    pub fn allows(self, kind: AccessKind) -> bool {
        match kind {
            AccessKind::Read => self.read,
            AccessKind::Write => self.write,
            AccessKind::Fetch => self.execute,
        }
    }

    pub fn bits(self) -> u64 {
        (if self.read { PTE_R } else { 0 })
            | (if self.write { PTE_W } else { 0 })
            | (if self.execute { PTE_X } else { 0 })
    }
}

/// 叶子页大小。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum PageSize {
    /// 4 KiB 基页
    #[serde(rename = "4K")]
    Small,
    /// 4 MiB 大页
    #[serde(rename = "4M")]
    Large,
}

impl PageSize {
    pub fn size_bytes(self) -> u64 {
        match self {
            PageSize::Small => crate::config::PAGE_SIZE,
            PageSize::Large => crate::config::LARGE_PAGE_SIZE,
        }
    }

    /// 映射要求的物理基址对齐。
    pub fn alignment(self) -> u64 {
        self.size_bytes()
    }

    pub fn as_str(self) -> &'static str {
        match self {
            PageSize::Small => "4K",
            PageSize::Large => "4M",
        }
    }
}

/// 一条页表项的解析视图（内部模型与诊断共用）。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Pte {
    pub raw: u64,
}

impl Pte {
    pub const fn zero() -> Self {
        Self { raw: 0 }
    }

    pub const fn from_raw(raw: u64) -> Self {
        Self { raw }
    }

    pub fn valid(self) -> bool {
        self.raw & PTE_V != 0
    }
    pub fn is_leaf(self) -> bool {
        // V=1 且 R/W/X 至少一位为 1
        self.valid() && self.raw & (PTE_R | PTE_W | PTE_X) != 0
    }
    pub fn is_branch(self) -> bool {
        // V=1 且 R/W/X 全 0：指向下一级页表
        self.valid() && self.raw & (PTE_R | PTE_W | PTE_X) == 0
    }
    pub fn large(self) -> bool {
        self.raw & PTE_PS != 0
    }
    pub fn dirty(self) -> bool {
        self.raw & PTE_D != 0
    }
    pub fn global(self) -> bool {
        self.raw & PTE_G != 0
    }
    pub fn permissions(self) -> Permissions {
        Permissions::new(
            self.raw & PTE_R != 0,
            self.raw & PTE_W != 0,
            self.raw & PTE_X != 0,
        )
    }
    pub fn ppn(self) -> u64 {
        (self.raw >> PTE_PPN_SHIFT) & ((1 << 20) - 1)
    }
    /// 该 PTE 编码的物理基址（PPN << 12）。
    pub fn phys_base(self) -> u64 {
        self.ppn() << PTE_PPN_SHIFT
    }
    pub fn has_reserved_bits(self) -> bool {
        self.raw & PTE_RESERVED_MASK != 0
    }

    /// 组装叶子 PTE。`large` 控制 PS 位。
    pub fn leaf(phys: u64, perms: Permissions, large: bool, dirty: bool, global: bool) -> Self {
        let mut raw = PTE_V | perms.bits();
        if large {
            raw |= PTE_PS;
        }
        if dirty {
            raw |= PTE_D;
        }
        if global {
            raw |= PTE_G;
        }
        raw |= (phys >> PTE_PPN_SHIFT) << PTE_PPN_SHIFT;
        Self { raw }
    }

    /// 组装指向下一级页表的分支 PTE（R/W/X=0，承载次表物理地址）。
    pub fn branch(next_table_phys: u64) -> Self {
        Self {
            raw: PTE_V | (next_table_phys & !0xFFF),
        }
    }
}

/// 一次翻译请求。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TranslateRequest {
    /// 地址空间标识符（>=1）。
    pub asid: u16,
    /// 虚拟地址。
    pub va: u64,
    /// 访问类型。
    pub access: AccessKind,
    /// 访问宽度（字节，1..=4MiB）。跨页时逐段验证权限。
    #[serde(default = "default_len")]
    pub len: u64,
    /// 是否强制走页表（绕过 TLB 查找，但结果仍会回填）。
    #[serde(default)]
    pub fetch_pte: bool,
}

fn default_len() -> u64 {
    1
}

/// 跨页访问被拆分后的单段结果。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Segment {
    /// 段内首字节虚拟地址。
    pub va: u64,
    /// 翻译得到的物理地址。
    pub pa: u64,
    /// 本段字节数。
    pub bytes: u64,
    /// 该段落在大页还是小页。
    pub page: PageSize,
    pub readable: bool,
    pub writable: bool,
    pub executable: bool,
    /// 翻译来源：TLB 命中或页表漫游。
    pub source: String,
    /// TLB 命中但缓存快照与当前 PTE 不一致（过期条目）。
    pub stale: bool,
}

/// 翻译成功的完整结果。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TranslateResult {
    pub run_id: String,
    pub asid: u16,
    pub va: u64,
    pub len: u64,
    pub access: AccessKind,
    pub pa: u64,
    pub page: PageSize,
    pub readable: bool,
    pub writable: bool,
    pub executable: bool,
    /// 是否命中 TLB。
    pub tlb_hit: bool,
    /// 来自 TLB 的段数。
    pub tlb_segments: usize,
    /// 来自页表漫游的段数。
    pub walk_segments: usize,
    /// 缓存快照与当前 PTE 不一致的过期段数。
    pub stale_segments: usize,
    /// 本次翻译回填 TLB 时被替换出的旧条目（可能多条：跨页逐段回填）。
    pub evictions: Vec<TlbEviction>,
    /// 页表途径层级（诊断用），例如 ["L1","L2"]。
    pub walk_levels: Vec<String>,
    /// 跨页时的逐段翻译；单页访问只含一段。
    pub segments: Vec<Segment>,
}

/// TLB 淘汰记录（教学诊断）。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TlbEviction {
    pub asid: u16,
    pub vpn_tag: u64,
    pub page: PageSize,
    pub reason: String,
}

/// 建映射请求。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct MapRequest {
    pub asid: u16,
    pub va: u64,
    /// 期望物理基址（须按页大小对齐）；缺省时由帧池确定性分配。
    #[serde(default)]
    pub pa: Option<u64>,
    pub page: PageSize,
    pub permissions: Permissions,
    #[serde(default)]
    pub global: bool,
    /// 创建后是否按协议失效相关 TLB（默认 true）。
    /// 置 false 仅用于构造“页表已改但 TLB 未刷新”的过期夹具。
    #[serde(default = "default_true")]
    pub invalidate: bool,
}

fn default_true() -> bool {
    true
}

/// 大页/小页冲突检测时记录的重叠映射描述。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct OverlapInfo {
    pub existing_va: u64,
    pub existing_pa: u64,
    pub existing_page: PageSize,
    pub requested_va: u64,
    pub requested_page: PageSize,
}

/// 翻译走页表时记录的中间状态（诊断）。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WalkStep {
    pub level: String,
    pub index: usize,
    pub pte_addr: u64,
    pub pte_raw: u64,
    pub note: String,
}

/// 拆分跨页访问为对齐的页区间（纯函数，供核心与测试共用）。
pub fn split_access(va: u64, len: u64, page_size: u64) -> Vec<(u64, u64)> {
    let mut out = Vec::new();
    let mut cur = va;
    let mut remaining = len;
    while remaining > 0 {
        let boundary = (cur / page_size + 1) * page_size;
        let chunk = (boundary - cur).min(remaining);
        out.push((cur, chunk));
        cur += chunk;
        remaining -= chunk;
    }
    out
}

/// 取虚拟地址的 VPN 标签（小页：va>>12；大页：va>>22）。
pub fn vpn_tag(va: u64, page: PageSize) -> u64 {
    match page {
        PageSize::Small => va >> crate::config::PAGE_BITS,
        PageSize::Large => va >> (crate::config::PAGE_BITS + crate::config::BITS_PER_LEVEL),
    }
}

/// 便捷：切分虚拟地址（重新导出，避免诊断模块直接依赖 config 内部）。
pub fn va_split(va: u64) -> crate::config::VpnSplit {
    split_vpn(va)
}
