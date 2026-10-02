//! Logic syntax for the Craig interpolation workbench.
//!
//! This crate owns the propositional [`Formula`] AST, its text parser and
//! semantic evaluation. It deliberately has no dependency on the proving or
//! checking crates so that both sides share only the common language.

pub mod eval;
pub mod formula;
pub mod parser;

pub use eval::{eval_with, evaluate, Assignment};
pub use formula::Formula;
pub use parser::{parse, ParseError};
