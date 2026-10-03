//! Logical syntax layer: data model, validation, limits and the shared
//! error contract for the termination certificate checker.

#![forbid(unsafe_code)]

pub mod error;
pub mod limits;
pub mod model;
pub mod validate;

pub use error::{Error, ErrorCategory, ErrorKind};
pub use limits::Limits;
pub use model::{Certificate, Rule, SymbolDecl, SymbolInterpretation, System, Term};
pub use validate::{validate_certificate, validate_system, Signature};
