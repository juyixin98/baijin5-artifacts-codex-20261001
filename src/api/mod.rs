//! HTTP boundary (Axum). The operator core has no dependency on this module.

pub mod dto;
pub mod http;

pub use http::{router, AppState};
