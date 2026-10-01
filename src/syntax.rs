//! Logical syntax: terms and first-order formulas over enumerated domains.
//!
//! Formulas use the following externally stable JSON shape:
//! ```json
//! { "op": "pred", "name": "p", "args": [{"t": "var", "name": "x"}] }
//! ```
//! Term objects carry a discriminator field `"t"`, formula objects carry
//! `"op"`. Element literals may be unqualified (`"elem"`) when the element
//! name is unambiguous across the finite model.

use serde::{Deserialize, Serialize};

/// A term in the term algebra of the model.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "t", rename_all = "snake_case")]
pub enum Term {
    /// A variable reference (free or bound).
    Var { name: String },
    /// An element literal; `sort` is None until type checking resolves it.
    Elem {
        value: String,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        sort: Option<String>,
    },
    /// A nullary function / constant.
    Const { name: String },
    /// An application of a named function symbol.
    App { name: String, args: Vec<Term> },
}

/// A first-order formula.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "op", rename_all = "snake_case")]
pub enum Formula {
    #[serde(rename = "bool")]
    Bool {
        value: bool,
    },
    Pred {
        name: String,
        #[serde(default, skip_serializing_if = "Vec::is_empty")]
        args: Vec<Term>,
    },
    Eq {
        left: Box<Term>,
        right: Box<Term>,
    },
    Not {
        inner: Box<Formula>,
    },
    And {
        #[serde(default, skip_serializing_if = "Vec::is_empty")]
        children: Vec<Formula>,
    },
    Or {
        #[serde(default, skip_serializing_if = "Vec::is_empty")]
        children: Vec<Formula>,
    },
    Impl {
        left: Box<Formula>,
        right: Box<Formula>,
    },
    Iff {
        left: Box<Formula>,
        right: Box<Formula>,
    },
    Forall {
        var: String,
        sort: String,
        inner: Box<Formula>,
    },
    Exists {
        var: String,
        sort: String,
        inner: Box<Formula>,
    },
}

impl Default for Formula {
    fn default() -> Self {
        Formula::Bool { value: false }
    }
}

impl Term {
    /// Free variables occurring in the term, in first-occurrence order.
    pub fn free_vars(&self, out: &mut Vec<String>) {
        match self {
            Term::Var { name } => {
                if !out.contains(name) {
                    out.push(name.clone());
                }
            }
            Term::Elem { .. } => {}
            Term::Const { .. } => {}
            Term::App { args, .. } => {
                for arg in args {
                    arg.free_vars(out);
                }
            }
        }
    }

    /// Every subtree term (including self) - used for size accounting.
    pub fn node_count(&self) -> usize {
        match self {
            Term::Var { .. } | Term::Elem { .. } | Term::Const { .. } => 1,
            Term::App { args, .. } => 1 + args.iter().map(Term::node_count).sum::<usize>(),
        }
    }
}

impl Formula {
    /// Free variables of the formula, where the `bound` set shadows outer uses.
    /// Results are appended to `out` in first-occurrence order, deduplicated.
    pub fn free_vars_in(&self, bound: &mut Vec<String>, out: &mut Vec<String>) {
        match self {
            Formula::Bool { .. } => {}
            Formula::Pred { args, .. } => {
                for arg in args {
                    for v in collect_free(arg) {
                        if !bound.contains(&v) && !out.contains(&v) {
                            out.push(v);
                        }
                    }
                }
            }
            Formula::Eq { left, right } => {
                for term in [left.as_ref(), right.as_ref()] {
                    for v in collect_free(term) {
                        if !bound.contains(&v) && !out.contains(&v) {
                            out.push(v);
                        }
                    }
                }
            }
            Formula::Not { inner } => inner.free_vars_in(bound, out),
            Formula::And { children } | Formula::Or { children } => {
                for child in children {
                    child.free_vars_in(bound, out);
                }
            }
            Formula::Impl { left, right } | Formula::Iff { left, right } => {
                left.free_vars_in(bound, out);
                right.free_vars_in(bound, out);
            }
            Formula::Forall { var, inner, .. } | Formula::Exists { var, inner, .. } => {
                let pushed = !bound.contains(var);
                if pushed {
                    bound.push(var.clone());
                }
                inner.free_vars_in(bound, out);
                if pushed {
                    bound.pop();
                }
            }
        }
    }

    /// Free variables of a standalone (possibly closed) formula.
    pub fn free_vars(&self) -> Vec<String> {
        let mut bound = Vec::new();
        let mut out = Vec::new();
        self.free_vars_in(&mut bound, &mut out);
        out
    }

    /// Total AST nodes (terms and formulas), used by the node cap guard.
    pub fn node_count(&self) -> usize {
        match self {
            Formula::Bool { .. } => 1,
            Formula::Pred { args, .. } => 1 + args.iter().map(Term::node_count).sum::<usize>(),
            Formula::Eq { left, right } => 1 + left.node_count() + right.node_count(),
            Formula::Not { inner } => 1 + inner.node_count(),
            Formula::And { children } | Formula::Or { children } => {
                1 + children.iter().map(Formula::node_count).sum::<usize>()
            }
            Formula::Impl { left, right } | Formula::Iff { left, right } => {
                1 + left.node_count() + right.node_count()
            }
            Formula::Forall { inner, .. } | Formula::Exists { inner, .. } => 1 + inner.node_count(),
        }
    }

    /// Number of quantifier nodes left in the formula.
    pub fn quantifier_count(&self) -> usize {
        match self {
            Formula::Bool { .. } | Formula::Pred { .. } | Formula::Eq { .. } => 0,
            Formula::Not { inner } => inner.quantifier_count(),
            Formula::And { children } | Formula::Or { children } => {
                children.iter().map(Formula::quantifier_count).sum()
            }
            Formula::Impl { left, right } | Formula::Iff { left, right } => {
                left.quantifier_count() + right.quantifier_count()
            }
            Formula::Forall { .. } | Formula::Exists { .. } => 1,
        }
    }

    pub fn is_quantifier_free(&self) -> bool {
        self.quantifier_count() == 0
    }
}

pub fn collect_free(term: &Term) -> Vec<String> {
    let mut out = Vec::new();
    term.free_vars(&mut out);
    out
}

/// Short constructors used heavily by tests and by fixture generation.
pub mod build {
    use super::*;

    pub fn var(name: &str) -> Term {
        Term::Var {
            name: name.to_string(),
        }
    }
    pub fn elem(value: &str) -> Term {
        Term::Elem {
            value: value.to_string(),
            sort: None,
        }
    }
    pub fn elem_typed(value: &str, sort: &str) -> Term {
        Term::Elem {
            value: value.to_string(),
            sort: Some(sort.to_string()),
        }
    }
    pub fn const_(name: &str) -> Term {
        Term::Const {
            name: name.to_string(),
        }
    }
    pub fn app(name: &str, args: Vec<Term>) -> Term {
        Term::App {
            name: name.to_string(),
            args,
        }
    }
    pub fn pred(name: &str, args: Vec<Term>) -> Formula {
        Formula::Pred {
            name: name.to_string(),
            args,
        }
    }
    pub fn bool_(v: bool) -> Formula {
        Formula::Bool { value: v }
    }
    pub fn eq(l: Term, r: Term) -> Formula {
        Formula::Eq {
            left: Box::new(l),
            right: Box::new(r),
        }
    }
    pub fn not(f: Formula) -> Formula {
        Formula::Not { inner: Box::new(f) }
    }
    pub fn and(children: Vec<Formula>) -> Formula {
        Formula::And { children }
    }
    pub fn or(children: Vec<Formula>) -> Formula {
        Formula::Or { children }
    }
    pub fn implies(l: Formula, r: Formula) -> Formula {
        Formula::Impl {
            left: Box::new(l),
            right: Box::new(r),
        }
    }
    pub fn iff(l: Formula, r: Formula) -> Formula {
        Formula::Iff {
            left: Box::new(l),
            right: Box::new(r),
        }
    }
    pub fn forall(var: &str, sort: &str, inner: Formula) -> Formula {
        Formula::Forall {
            var: var.to_string(),
            sort: sort.to_string(),
            inner: Box::new(inner),
        }
    }
    pub fn exists(var: &str, sort: &str, inner: Formula) -> Formula {
        Formula::Exists {
            var: var.to_string(),
            sort: sort.to_string(),
            inner: Box::new(inner),
        }
    }
}
