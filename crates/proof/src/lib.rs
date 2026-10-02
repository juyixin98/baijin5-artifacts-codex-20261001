//! Proof records for propositional resolution refutations.
//!
//! This crate is the shared boundary between the proving engine and the
//! independent checker: the engine writes a [`Proof`], the checker reads it
//! back without using any engine code.

pub mod literal;
pub mod proof;

pub use literal::{Clause, Literal};
pub use proof::{
    Proof, ProofBuilder, ProofNode, ProofValidationError, Side,
};
