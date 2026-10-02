//! mmu-teach：受限多级页表与 TLB 地址翻译**教学服务**。
//!
//! 所有页表/物理内存/TLB 均为进程内模拟，**禁止也不会操作真实内核页表**。
//!
//! 模块边界：
//! - [`config`]：固定机器参数（页大小、地址位数、保留位、容量上限）；
//! - [`address`]：虚/实地址分解、跨页拆分（资源算法）；
//! - [`pte`]：PTE 编解码、权限、保留位校验（资源算法）；
//! - [`memory`]：物理帧池与分配（资源算法）；
//! - [`tlb`]：ASID 标签 TLB、LRU 与失效协议；
//! - [`pagetable`]：多级页表树、遍历、映射冲突检测（运行模型核心）；
//! - [`mmu`]：翻译 + 权限 + 跨页访问（运行模型）；
//! - [`machine`]：多地址空间编排、失效协议执行、快照持久化（持久状态）；
//! - [`runlog`]：运行编号日志与 JSONL 落盘（诊断）；
//! - [`api`]：Axum HTTP 诊断/操作接口。

pub mod address;
pub mod api;
pub mod config;
pub mod errors;
pub mod machine;
pub mod memory;
pub mod mmu;
pub mod pagetable;
pub mod pte;
pub mod runlog;
pub mod tlb;

pub use machine::Machine;
pub use runlog::RunLog;
