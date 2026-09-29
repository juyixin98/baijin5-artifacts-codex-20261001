//! Typed batches and comparable values.

pub mod batch;
pub mod builder;
pub mod value;

pub use batch::{Column, TypedBatch};
pub use value::{KeyType, Scalar};
