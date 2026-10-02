//! Service layer: JSON contract, request orchestration and local transport.

pub mod api;
pub mod config;
pub mod handler;
pub mod server;

pub use api::{InterpolateRequest, InterpolateStatus, ServiceResponse};
pub use config::ServiceConfig;
pub use handler::handle_interpolate;
