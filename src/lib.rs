//! # mmu-lab：受限多级页表与 TLB 地址翻译教学服务
//!
//! 本 crate 只模拟一台**合成教学机器**的地址翻译，绝不触碰真实内核页表：
//!
//! - 固定参数：32 位 VA/PA、4 KiB 基页、二级页表（10/10/12）、4 MiB 大页、
//!   固定保留位；
//! - 页表修改遵循**显式失效协议**，TLB 带 ASID 隔离与全局位；
//! - 大页/小页覆盖冲突被拒绝；
//! - 翻译失败返回**类型化页故障**（not-present / permission / reserved-bit 等），
//!   不 panic；
//! - 工程错误区分输入错误、状态冲突、资源耗尽、计算失败；
//! - 状态可原子持久化到本地 JSON 文件；
//! - 每次操作带可重放的 `run_id`、关键中间状态与判断理由。
//!
//! 模块边界：
//! [`config`] 固定参数与切分；[`types`] 数据契约；[`error`] 错误/故障契约；
//! [`frames`] 物理帧算法；[`tlb`] TLB；[`mmu`] 页表内存与纯走表；
//! [`store`] 中央状态机（mapping/translate）；[`events`] 诊断；
//! [`persistence`] 文件快照；[`api`] HTTP 诊断接口。

pub mod config;
pub mod error;
pub mod events;
pub mod frames;
pub mod mmu;
pub mod persistence;
pub mod store;
pub mod tlb;
pub mod types;

pub mod api;

pub use error::{DomainError, DomainResult, FaultKind, PageFault};
pub use store::translate::TranslateOutcome;
pub use store::{Lab, LabSnapshot};
