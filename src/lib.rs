//! rescheck: streaming independent checker for CNF unsatisfiability
//! resolution proofs.
//!
//! Module layout:
//! - [`syntax`]: logical syntax (literals, clauses, normalization rules)
//! - [`resolve`]: inference core (the propositional resolution rule)
//! - [`proof`]: proof record format (streaming line parser)
//! - [`checker`]: independent streaming checker (verdicts, limits, logging)

pub mod checker;
pub mod proof;
pub mod resolve;
pub mod syntax;

/// Checker version reported in every [`checker::CheckReport`].
pub const CHECKER_VERSION: &str = env!("CARGO_PKG_VERSION");
