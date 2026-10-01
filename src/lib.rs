//! # pctl
//!
//! Backend operators for grouped **exact percentiles**, **mode**, and
//! **ordered string aggregation** over typed Arrow2 batches, with a bounded
//! external-sort execution engine.
//!
//! Module responsibilities:
//!
//! | module | responsibility |
//! |---|---|
//! | [`spec`] | wire request/response shapes and the validated plan |
//! | [`batch`] | JSON -> typed Arrow2 columns, validated once at the boundary |
//! | [`operator`] | pure percentile/mode/string-agg math over sorted keys |
//! | [`resources`] | memory budget (incl. string bytes), spill quota, cancellation |
//! | [`exec`] | external sort, k-way merge, bounded group registry, resume |
//! | [`validate`] | single execution-front validation gate |
//! | [`diagnostics`] | request ids, redaction, structured decision logs |
//! | [`api`] | Axum HTTP surface |

pub mod api;
pub mod batch;
pub mod config;
pub mod diagnostics;
pub mod error;
pub mod exec;
pub mod operator;
pub mod resources;
pub mod spec;
pub mod validate;

pub use api::build_router;
pub use config::Config;
pub use error::{ErrorKind, PctlError, Result};
pub use exec::execute_request;
pub use resources::CancellationToken;
