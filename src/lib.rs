//! Independent streaming checker for propositional CNF resolution proofs.
//!
//! Crate layout:
//! - [`syntax`]: literals and clauses (the logical syntax).
//! - [`resolution`]: the binary resolution inference rule (the core).
//!
//! Additional modules (proof records, streaming independent checker, config
//! and reporting) are added incrementally.

pub mod resolution;
pub mod syntax;
