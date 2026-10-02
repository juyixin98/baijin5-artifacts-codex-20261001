//! iosched-compare：本地块请求的 deadline 与 SCAN 调度比较服务。
//!
//! 模块划分：
//! - `model`     —— 运行模型：请求/取消/轨迹/设备耗时模型与输入校验
//! - `scheduler` —— 资源算法：deadline 与 SCAN 调度器（含合并）
//! - `engine`    —— 离散事件仿真引擎（可控虚拟时钟）
//! - `state`     —— 持久化：运行报告 + 事件日志落盘
//! - `api`       —— 诊断接口（Axum）
//! - `config`    —— 服务配置

pub mod api;
pub mod config;
pub mod engine;
pub mod model;
pub mod scheduler;
pub mod state;
