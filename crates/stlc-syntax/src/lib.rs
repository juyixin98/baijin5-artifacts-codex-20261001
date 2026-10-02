//! Logical syntax for simply-typed lambda calculus (STLC).
//!
//! Three representations are kept separate on purpose:
//!
//! * [`Type`] — types (`Bool`, base types, right-associated arrows).
//! * [`Term`] — named surface syntax produced by the parser and JSON input.
//! * [`DbTerm`] — locally nameless representation (de Bruijn indices for bound
//!   variables, names for free variables) used by the reasoning core. This
//!   makes alpha-equivalence syntactic and capture-free substitution mechanical.

pub mod db;
pub mod parse;
pub mod pretty;

use serde::{Deserialize, Serialize};

/// STLC types.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum Type {
    /// The single built-in base type.
    Bool,
    /// An opaque named base type, e.g. `Nat` in the Church-encoding fixtures.
    Base { name: String },
    /// Function type `domain -> codomain`.
    Arrow { domain: Box<Type>, codomain: Box<Type> },
}

/// Named surface terms (what users write and what JSON fixtures contain).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum Term {
    Var {
        name: String,
    },
    Abs {
        param: String,
        #[serde(rename = "type")]
        param_ty: Type,
        body: Box<Term>,
    },
    App {
        func: Box<Term>,
        arg: Box<Term>,
    },
    BoolLit {
        value: bool,
    },
    If {
        cond: Box<Term>,
        then: Box<Term>,
        #[serde(rename = "else")]
        otherwise: Box<Term>,
    },
}

/// Collect the free variable names of a surface term (sorted, deduplicated).
pub fn free_vars(term: &Term) -> Vec<String> {
    let mut names = std::collections::BTreeSet::new();
    collect(term, &mut Vec::new(), &mut names);
    names.into_iter().collect()
}

fn collect(term: &Term, bound: &mut Vec<String>, out: &mut std::collections::BTreeSet<String>) {
    match term {
        Term::Var { name } => {
            if !bound.iter().rev().any(|b| b == name) {
                out.insert(name.clone());
            }
        }
        Term::Abs { param, body, .. } => {
            bound.push(param.clone());
            collect(body, bound, out);
            bound.pop();
        }
        Term::App { func, arg } => {
            collect(func, bound, out);
            collect(arg, bound, out);
        }
        Term::BoolLit { .. } => {}
        Term::If {
            cond,
            then,
            otherwise,
        } => {
            collect(cond, bound, out);
            collect(then, bound, out);
            collect(otherwise, bound, out);
        }
    }
}
