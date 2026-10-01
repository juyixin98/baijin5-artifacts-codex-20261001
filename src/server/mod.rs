//! Axum HTTP entry point.
//!
//! * [`dto`] — wire types and Cell/JSON conversion
//! * [`handlers`] — router, request validation, execution/cancellation/resume

pub mod dto;
pub mod handlers;

pub use handlers::{build_router, AppState, ServerConfig};
