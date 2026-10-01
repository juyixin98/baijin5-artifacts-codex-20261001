//! Quantifier elimination and model checking for first-order logic over
//! finite enumerated domains.
//!
//! Module boundaries:
//! * [`syntax`] - term/formula AST and free variable analysis;
//! * [`model`] - finite model, declaration validation and type checking;
//! * [`subst`] - capture avoiding substitution and alpha renaming;
//! * [`expand`] - bounded quantifier expansion (the elimination core);
//! * [`evaluator`] - direct recursive reference semantics;
//! * [`proof`] - run ids, proof records and step loggers;
//! * [`checker`] - independent proof/verdict checking;
//! * [`service`] - request/response contracts and pipeline orchestration;
//! * [`error`] - disjoint error class contract.

pub mod checker;
pub mod error;
pub mod evaluator;
pub mod expand;
pub mod model;
pub mod proof;
pub mod service;
pub mod subst;
pub mod syntax;
