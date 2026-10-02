//! Proof records for STLC typing derivations.
//!
//! This crate deliberately depends only on the syntax crate: a typing
//! derivation is data, and the independent auditor (`stlc-audit`) can
//! re-verify it without linking against the inference core.

use serde::{Deserialize, Serialize};
use stlc_syntax::db::DbTerm;
use stlc_syntax::Type;

/// Name of the rule that concluded a derivation node.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Rule {
    Var,
    Abs,
    App,
    BoolTrue,
    BoolFalse,
    If,
}

/// A typing hypothesis for a free variable: `name : ty`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Binding {
    pub name: String,
    #[serde(rename = "type")]
    pub ty: Type,
}

/// One node of a typing derivation, in locally nameless form.
///
/// `context` lists the bindings visible at this node. Bound assumptions are
/// rendered with synthetic names `__b<depth>` (nearest binder last), free
/// assumptions carry their declared names.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DerivNode {
    pub rule: Rule,
    pub context: Vec<Binding>,
    pub term: DbTerm,
    #[serde(rename = "type")]
    pub ty: Type,
    pub children: Vec<DerivNode>,
    /// Human-readable justification, e.g. "domain Nat matches argument type".
    pub justification: String,
}

/// Complete typing proof for one term.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TypingProof {
    pub free_signature: Vec<Binding>,
    pub root: DerivNode,
}
