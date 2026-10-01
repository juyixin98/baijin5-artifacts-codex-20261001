//! HTTP boundary: request models, validation entry point, service pipeline.

pub mod response;
pub mod service;
pub mod types;
pub mod validate;

pub use service::{error_response, run_pipeline};
pub use types::ExecuteRequest;
