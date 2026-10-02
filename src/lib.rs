//! stackstats — offline stack-sample call-tree and folded-stack statistics
//! backend. Structured data in, structured data out.
//!
//! Module boundaries:
//! - [`model`]: run model — samples, frames, symbols, lifecycle state
//! - [`symbols`]: address-identity symbol resolution
//! - [`naming`]: display-name disambiguation (never touches identity)
//! - [`stitch`]: async fragment stitching with correlation evidence
//! - [`tree`]: call-tree resource algorithm (self vs inclusive, hot/cold)
//! - [`folded`]: folded-stack emission
//! - [`store`]: filesystem persistence and sampling-state lifecycle
//! - [`api`]: Axum diagnostic interface
//! - [`error`]: four-category error contract shared across modules

pub mod api;
pub mod config;
pub mod error;
pub mod folded;
pub mod model;
pub mod naming;
pub mod stitch;
pub mod store;
pub mod symbols;
pub mod tree;
