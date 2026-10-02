//! Query operators: typed expression evaluation and the equi-join.
//!
//! These operators know nothing about recursion: they operate on one frontier
//! row / one edge relation at a time. Fixpoint and cycle policy live in the
//! `exec` layer.

pub mod expr;
pub mod join;
