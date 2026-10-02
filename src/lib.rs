//! cow-snapshot-service: copy-on-write snapshots over a fixed page object
//! space. See README.md for the full contract and operational guide.

pub mod api;
pub mod config;
pub mod engine;
pub mod error;
pub mod eventlog;
pub mod page_store;
