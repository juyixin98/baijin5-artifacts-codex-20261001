//! Query layer: parsed predicate AST, typed binding and 3VL execution.

pub mod executor;
pub mod expr;

pub use executor::{evaluate, next_run_id, Catalog, QueryOutcome};
pub use expr::Expr;
