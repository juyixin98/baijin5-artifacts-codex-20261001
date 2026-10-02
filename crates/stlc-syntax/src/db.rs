//! Locally nameless (de Bruijn) representation and conversion to/from it.

use std::collections::BTreeSet;

use serde::{Deserialize, Serialize};

use crate::Term;

/// Locally nameless term.
///
/// * [`DbTerm::BVar`] — bound variable, index counted from the nearest binder
///   (0 = nearest). Indices past all enclosing binders are dangling and must
///   never appear in well-formed output.
/// * [`DbTerm::FVar`] — free variable, carried by its surface name.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum DbTerm {
    BVar { index: usize },
    FVar { name: String },
    Abs {
        param_ty: crate::Type,
        body: Box<DbTerm>,
    },
    App { func: Box<DbTerm>, arg: Box<DbTerm> },
    BoolLit { value: bool },
    If {
        cond: Box<DbTerm>,
        then: Box<DbTerm>,
        #[serde(rename = "else")]
        otherwise: Box<DbTerm>,
    },
}

impl DbTerm {
    /// Free-variable names of this term (sorted, deduplicated).
    pub fn free_vars(&self) -> Vec<String> {
        let mut set = BTreeSet::new();
        self.collect_fv(&mut set);
        set.into_iter().collect()
    }

    fn collect_fv(&self, out: &mut BTreeSet<String>) {
        match self {
            DbTerm::BVar { .. } => {}
            DbTerm::FVar { name } => {
                out.insert(name.clone());
            }
            DbTerm::Abs { body, .. } => body.collect_fv(out),
            DbTerm::App { func, arg } => {
                func.collect_fv(out);
                arg.collect_fv(out);
            }
            DbTerm::BoolLit { .. } => {}
            DbTerm::If {
                cond,
                then,
                otherwise,
            } => {
                cond.collect_fv(out);
                then.collect_fv(out);
                otherwise.collect_fv(out);
            }
        }
    }

    /// Check that every bound index resolves within `depth` surrounding binders.
    pub fn closed_at(&self, depth: usize) -> bool {
        match self {
            DbTerm::BVar { index } => *index < depth,
            DbTerm::FVar { .. } | DbTerm::BoolLit { .. } => true,
            DbTerm::Abs { body, .. } => body.closed_at(depth + 1),
            DbTerm::App { func, arg } => func.closed_at(depth) && arg.closed_at(depth),
            DbTerm::If {
                cond,
                then,
                otherwise,
            } => {
                cond.closed_at(depth)
                    && then.closed_at(depth)
                    && otherwise.closed_at(depth)
            }
        }
    }

    /// True when the term contains no dangling bound indices.
    pub fn is_locally_closed(&self) -> bool {
        self.closed_at(0)
    }
}

/// Convert a named term to locally nameless form.
///
/// A name is resolved to the index of its nearest enclosing binder of the
/// same name; otherwise it becomes a free variable.
pub fn to_locally_nameless(term: &Term) -> DbTerm {
    fn go(term: &Term, env: &[String]) -> DbTerm {
        match term {
            Term::Var { name } => match env.iter().rev().position(|b| b == name) {
                Some(index) => DbTerm::BVar { index },
                None => DbTerm::FVar {
                    name: name.clone(),
                },
            },
            Term::Abs {
                param,
                param_ty,
                body,
            } => {
                let mut next = env.to_vec();
                next.push(param.clone());
                DbTerm::Abs {
                    param_ty: param_ty.clone(),
                    body: Box::new(go(body, &next)),
                }
            }
            Term::App { func, arg } => DbTerm::App {
                func: Box::new(go(func, env)),
                arg: Box::new(go(arg, env)),
            },
            Term::BoolLit { value } => DbTerm::BoolLit { value: *value },
            Term::If {
                cond,
                then,
                otherwise,
            } => DbTerm::If {
                cond: Box::new(go(cond, env)),
                then: Box::new(go(then, env)),
                otherwise: Box::new(go(otherwise, env)),
            },
        }
    }
    go(term, &[])
}

/// Convert locally nameless form back to named syntax.
///
/// Fresh binder names avoid the bound/free variables already present so that
/// the generated named term preserves the intended binding structure.
pub fn from_locally_nameless(term: &DbTerm) -> Term {
    fn fresh(avoid: &BTreeSet<String>, hint: usize) -> String {
        let candidates = ["x", "y", "z", "f", "g", "h", "a", "b", "c"];
        if hint < candidates.len() {
            let name = candidates[hint].to_string();
            if !avoid.contains(&name) {
                return name;
            }
        }
        let mut i = 0usize;
        loop {
            let name = format!("x{i}");
            if !avoid.contains(&name) {
                return name;
            }
            i += 1;
        }
    }

    fn go(term: &DbTerm, names: &[String], avoid: &mut BTreeSet<String>) -> Term {
        match term {
            DbTerm::BVar { index } => {
                let name = names
                    .get(names.len() - 1 - *index)
                    .cloned()
                    .unwrap_or_else(|| format!("__dangling_{index}"));
                Term::Var { name }
            }
            DbTerm::FVar { name } => Term::Var {
                name: name.clone(),
            },
            DbTerm::Abs { param_ty, body } => {
                let name = fresh(avoid, names.len());
                avoid.insert(name.clone());
                let mut next = names.to_vec();
                next.push(name.clone());
                let body_t = go(body, &next, avoid);
                Term::Abs {
                    param: name,
                    param_ty: param_ty.clone(),
                    body: Box::new(body_t),
                }
            }
            DbTerm::App { func, arg } => Term::App {
                func: Box::new(go(func, names, avoid)),
                arg: Box::new(go(arg, names, avoid)),
            },
            DbTerm::BoolLit { value } => Term::BoolLit { value: *value },
            DbTerm::If {
                cond,
                then,
                otherwise,
            } => Term::If {
                cond: Box::new(go(cond, names, avoid)),
                then: Box::new(go(then, names, avoid)),
                otherwise: Box::new(go(otherwise, names, avoid)),
            },
        }
    }

    let mut avoid = BTreeSet::new();
    let mut free = BTreeSet::new();
    term.collect_fv(&mut free);
    avoid.extend(free);
    go(term, &[], &mut avoid)
}
