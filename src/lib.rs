//! ARC 页替换引擎库。
//!
//! 模块职责（各自具有真实职责，见各模块文档）：
//! - [`types`]：页标识、访问类型、结果、统计等共享数据模型
//! - [`config`]：外部 TOML 配置解析与校验
//! - [`storage`]：回写适配器 trait 与文件系统实现（脏页落盘的唯一边界）
//! - [`arc`]：ARC 算法（T1/T2 真实列表 + B1/B2 幽灵目录 + 自适应目标 p）
//! - [`engine`]：ARC 与存储之间的协调（读/写、脏页淘汰、回写失败轨迹）
//! - [`trace`]：本地合成访问轨迹
//! - [`state`]：跨进程的采样/持久状态（p、命中计数，不含页内容）
//! - [`diagnostics`]：带请求标识的决策日志（接受/拒绝/无法判定）
//! - [`api`]：Axum HTTP 诊断与回放接口

pub mod api;
pub mod arc;
pub mod config;
pub mod diagnostics;
pub mod engine;
pub mod state;
pub mod storage;
pub mod trace;
pub mod types;

pub use engine::{Engine, EngineError};
