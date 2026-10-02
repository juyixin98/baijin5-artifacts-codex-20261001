//! Independent verifier for propositional Craig interpolants.
//!
//! Dependency policy: the crate depends only on the shared syntax and proof
//! record types. It contains its own truth tables and its own replay of the
//! symmetric annotation rules, intentionally duplicating (rather than
//! importing) the engine's interpolant logic.

pub mod truth_table;
pub mod verify;

pub use truth_table::{check_implication, is_satisfiable, is_valid, Validity};
pub use verify::{
    replay_proof, verify_full, verify_semantic, VerificationFailure, VerificationReport,
};
