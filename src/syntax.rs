//! First-order logic syntax: terms, formulas, and capture-avoiding
//! substitution.
//!
//! Terms live in two explicit namespaces: [`Term::Var`] for logical variables
//! and [`Term::Const`] for domain elements. Because the namespaces are
//! separate, substituting a constant can never be captured by a variable
//! binder; substituting a *variable* can, and [`Formula::subst`] renames the
//! offending binder to a fresh name (`base#n`) before proceeding.
//!
//! JSON representation (serde):
//! * term: `{"var": "x"}` or `{"const": "a"}`
//! * formula: internally tagged by `op`, e.g.
//!   `{"op": "forall", "var": "x", "body": ...}`,
//!   `{"op": "and", "args": [...]}`,
//!   `{"op": "atom", "pred": "P", "args": [{"var": "x"}]}`,
//!   `{"op": "eq", "left": ..., "right": ...}`,
//!   `{"op": "true"}`, `{"op": "not", "arg": ...}`.

use crate::error::QeError;
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

#[derive(Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Term {
    Var(String),
    Const(String),
}

impl Term {
    fn collect_free_vars(&self, acc: &mut BTreeSet<String>) {
        if let Term::Var(v) = self {
            acc.insert(v.clone());
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "op", rename_all = "lowercase")]
pub enum Formula {
    True,
    False,
    Atom { pred: String, args: Vec<Term> },
    Eq { left: Term, right: Term },
    Not { arg: Box<Formula> },
    And { args: Vec<Formula> },
    Or { args: Vec<Formula> },
    Implies { left: Box<Formula>, right: Box<Formula> },
    Iff { left: Box<Formula>, right: Box<Formula> },
    Forall { var: String, body: Box<Formula> },
    Exists { var: String, body: Box<Formula> },
}

/// Smart constructors that keep formulas in a lightly normalized form
/// (empty conjunction = true, false annihilates conjunction, etc.).
impl Formula {
    pub fn atom(pred: &str, args: Vec<Term>) -> Formula {
        Formula::Atom {
            pred: pred.to_string(),
            args,
        }
    }

    pub fn eq(left: Term, right: Term) -> Formula {
        Formula::Eq { left, right }
    }

    pub fn not(arg: Formula) -> Formula {
        match arg {
            Formula::True => Formula::False,
            Formula::False => Formula::True,
            other => Formula::Not {
                arg: Box::new(other),
            },
        }
    }

    pub fn and(args: Vec<Formula>) -> Formula {
        let mut flat = Vec::new();
        for a in args {
            match a {
                Formula::False => return Formula::False,
                Formula::True => {}
                Formula::And { args: inner } => flat.extend(inner),
                other => flat.push(other),
            }
        }
        match flat.len() {
            0 => Formula::True,
            1 => flat.pop().unwrap(),
            _ => Formula::And { args: flat },
        }
    }

    pub fn or(args: Vec<Formula>) -> Formula {
        let mut flat = Vec::new();
        for a in args {
            match a {
                Formula::True => return Formula::True,
                Formula::False => {}
                Formula::Or { args: inner } => flat.extend(inner),
                other => flat.push(other),
            }
        }
        match flat.len() {
            0 => Formula::False,
            1 => flat.pop().unwrap(),
            _ => Formula::Or { args: flat },
        }
    }

    pub fn forall(var: &str, body: Formula) -> Formula {
        Formula::Forall {
            var: var.to_string(),
            body: Box::new(body),
        }
    }

    pub fn exists(var: &str, body: Formula) -> Formula {
        Formula::Exists {
            var: var.to_string(),
            body: Box::new(body),
        }
    }
}

impl Formula {
    /// Variables that occur free in this formula.
    pub fn free_vars(&self) -> BTreeSet<String> {
        let mut bound = BTreeSet::new();
        let mut acc = BTreeSet::new();
        self.collect_free(&mut bound, &mut acc);
        acc
    }

    fn collect_free(&self, bound: &mut BTreeSet<String>, acc: &mut BTreeSet<String>) {
        match self {
            Formula::True | Formula::False => {}
            Formula::Atom { args, .. } => {
                for t in args {
                    let mut vars = BTreeSet::new();
                    t.collect_free_vars(&mut vars);
                    for v in vars {
                        if !bound.contains(&v) {
                            acc.insert(v);
                        }
                    }
                }
            }
            Formula::Eq { left, right } => {
                for t in [left, right] {
                    let mut vars = BTreeSet::new();
                    t.collect_free_vars(&mut vars);
                    for v in vars {
                        if !bound.contains(&v) {
                            acc.insert(v);
                        }
                    }
                }
            }
            Formula::Not { arg } => arg.collect_free(bound, acc),
            Formula::And { args } | Formula::Or { args } => {
                for a in args {
                    a.collect_free(bound, acc);
                }
            }
            Formula::Implies { left, right } | Formula::Iff { left, right } => {
                left.collect_free(bound, acc);
                right.collect_free(bound, acc);
            }
            Formula::Forall { var, body } | Formula::Exists { var, body } => {
                let inserted = bound.insert(var.clone());
                body.collect_free(bound, acc);
                if inserted {
                    bound.remove(var);
                }
            }
        }
    }

    /// Every variable name occurring anywhere (free or bound). Used to pick
    /// fresh names that cannot collide with any existing binder.
    pub fn all_vars(&self) -> BTreeSet<String> {
        let mut acc = BTreeSet::new();
        self.collect_all_vars(&mut acc);
        acc
    }

    fn collect_all_vars(&self, acc: &mut BTreeSet<String>) {
        match self {
            Formula::True | Formula::False => {}
            Formula::Atom { args, .. } => {
                for t in args {
                    t.collect_free_vars(acc);
                }
            }
            Formula::Eq { left, right } => {
                left.collect_free_vars(acc);
                right.collect_free_vars(acc);
            }
            Formula::Not { arg } => arg.collect_all_vars(acc),
            Formula::And { args } | Formula::Or { args } => {
                for a in args {
                    a.collect_all_vars(acc);
                }
            }
            Formula::Implies { left, right } | Formula::Iff { left, right } => {
                left.collect_all_vars(acc);
                right.collect_all_vars(acc);
            }
            Formula::Forall { var, body } | Formula::Exists { var, body } => {
                acc.insert(var.clone());
                body.collect_all_vars(acc);
            }
        }
    }

    /// Total number of syntax nodes (connectives, quantifiers, leaves).
    pub fn node_count(&self) -> usize {
        match self {
            Formula::True | Formula::False | Formula::Atom { .. } | Formula::Eq { .. } => 1,
            Formula::Not { arg } => 1 + arg.node_count(),
            Formula::And { args } | Formula::Or { args } => {
                1 + args.iter().map(Formula::node_count).sum::<usize>()
            }
            Formula::Implies { left, right } | Formula::Iff { left, right } => {
                1 + left.node_count() + right.node_count()
            }
            Formula::Forall { body, .. } | Formula::Exists { body, .. } => 1 + body.node_count(),
        }
    }

    pub fn quantifier_count(&self) -> usize {
        match self {
            Formula::True | Formula::False | Formula::Atom { .. } | Formula::Eq { .. } => 0,
            Formula::Not { arg } => arg.quantifier_count(),
            Formula::And { args } | Formula::Or { args } => {
                args.iter().map(Formula::quantifier_count).sum()
            }
            Formula::Implies { left, right } | Formula::Iff { left, right } => {
                left.quantifier_count() + right.quantifier_count()
            }
            Formula::Forall { body, .. } | Formula::Exists { body, .. } => {
                1 + body.quantifier_count()
            }
        }
    }

    pub fn is_quantifier_free(&self) -> bool {
        self.quantifier_count() == 0
    }

    /// Capture-avoiding substitution of `term` for free occurrences of `var`.
    ///
    /// If a binder on the path binds a variable that occurs free in `term`,
    /// the binder is first renamed to a fresh name derived from
    /// [`fresh_var`], so no variable of `term` is captured. Binders that
    /// shadow `var` stop the substitution below them.
    pub fn subst(&self, var: &str, term: &Term) -> Formula {
        match self {
            Formula::True | Formula::False => self.clone(),
            Formula::Atom { pred, args } => Formula::Atom {
                pred: pred.clone(),
                args: args.iter().map(|t| subst_term(t, var, term)).collect(),
            },
            Formula::Eq { left, right } => Formula::Eq {
                left: subst_term(left, var, term),
                right: subst_term(right, var, term),
            },
            Formula::Not { arg } => Formula::Not {
                arg: Box::new(arg.subst(var, term)),
            },
            Formula::And { args } => Formula::And {
                args: args.iter().map(|f| f.subst(var, term)).collect(),
            },
            Formula::Or { args } => Formula::Or {
                args: args.iter().map(|f| f.subst(var, term)).collect(),
            },
            Formula::Implies { left, right } => Formula::Implies {
                left: Box::new(left.subst(var, term)),
                right: Box::new(right.subst(var, term)),
            },
            Formula::Iff { left, right } => Formula::Iff {
                left: Box::new(left.subst(var, term)),
                right: Box::new(right.subst(var, term)),
            },
            Formula::Forall { var: y, body } => subst_quantifier(true, y, body, var, term),
            Formula::Exists { var: y, body } => subst_quantifier(false, y, body, var, term),
        }
    }

    pub fn from_json_str(s: &str) -> Result<Formula, QeError> {
        serde_json::from_str(s)
            .map_err(|e| QeError::invalid_input(format!("formula JSON parse error: {e}")))
    }

    pub fn to_json_string(&self) -> String {
        serde_json::to_string(self).expect("formula serialization is infallible")
    }
}

fn subst_term(target: &Term, var: &str, term: &Term) -> Term {
    match target {
        Term::Var(v) if v == var => term.clone(),
        other => other.clone(),
    }
}

fn subst_quantifier(
    is_forall: bool,
    y: &str,
    body: &Formula,
    var: &str,
    term: &Term,
) -> Formula {
    let rebuild = |y: String, body: Formula| {
        if is_forall {
            Formula::forall(&y, body)
        } else {
            Formula::exists(&y, body)
        }
    };

    // The binder shadows `var`: no free occurrences of `var` below.
    if y == var {
        return rebuild(y.to_string(), body.clone());
    }

    // Would the binder capture a free variable of the substituted term?
    let mut term_vars = BTreeSet::new();
    term.collect_free_vars(&mut term_vars);
    if term_vars.contains(y) {
        let mut avoid = body.all_vars();
        avoid.extend(term_vars.iter().cloned());
        avoid.insert(var.to_string());
        let fresh = fresh_var(y, &avoid);
        let renamed = body.subst(y, &Term::Var(fresh.clone()));
        rebuild(fresh, renamed.subst(var, term))
    } else {
        rebuild(y.to_string(), body.subst(var, term))
    }
}

/// Deterministic fresh-name scheme: `base#1`, `base#2`, ... until the name
/// is outside `avoid`.
pub fn fresh_var(base: &str, avoid: &BTreeSet<String>) -> String {
    let mut i = 1u64;
    loop {
        let candidate = format!("{base}#{i}");
        if !avoid.contains(&candidate) {
            return candidate;
        }
        i += 1;
    }
}

/// A named formula entry as stored in fixture bundles. `expect` maps model
/// names to hand-written reference truth values.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NamedFormula {
    pub name: String,
    #[serde(default)]
    pub expect: std::collections::BTreeMap<String, bool>,
    pub formula: Formula,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FormulaBundle {
    pub formulas: Vec<NamedFormula>,
}

impl FormulaBundle {
    pub fn from_json_str(s: &str) -> Result<FormulaBundle, QeError> {
        serde_json::from_str(s)
            .map_err(|e| QeError::invalid_input(format!("formula bundle JSON parse error: {e}")))
    }
}
