//! # recursive-cte-backend
//!
//! A bounded execution backend for a restricted `WITH RECURSIVE` subset:
//!
//! * seed term + one recursive term (`R JOIN edges ON ...`),
//! * `UNION` (set semantics) and `UNION ALL` (bag semantics),
//! * path-aware cycle marking on a *declared key* (never the whole row),
//! * explicit `max_depth` / `max_rows` bounds returning an `incomplete`
//!   status with a concrete reason,
//! * configurable deterministic traversal order (`bfs` / `dfs`),
//! * typed batches backed by Arrow2, served over Axum.
//!
//! Layers:
//!
//! | Module     | Responsibility |
//! |------------|----------------|
//! | [`plan`]   | wire model + request validation |
//! | [`batch`]  | typed scalars, batches, Arrow2 conversion |
//! | [`operator`]| expression evaluation, equi-join |
//! | [`exec`]   | seed assembly, run state (working table + dedup), fixpoint engine |
//! | [`reference`]| independent explicit-recursion oracle used by tests |
//! | [`api`]    | HTTP validation/execution entry points |
//! | [`config`] | independent process configuration layer |

pub mod api;
pub mod batch;
pub mod config;
pub mod error;
pub mod exec;
pub mod operator;
pub mod plan;
pub mod reference;

pub use error::{EngineError, EngineResult, FailureCategory};
pub use exec::engine::{execute, ENGINE_VERSION};
pub use plan::{RecursiveRequest, RecursiveResponse, RunStatus};

/// Crate-level result for callers that do not care about the concrete error.
pub fn version() -> &'static str {
    ENGINE_VERSION
}
