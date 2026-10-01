//! Finite enumerated model: sorts (domains), constants, functions and predicates.
//!
//! Functions are *partial*: a missing tuple yields a computation failure rather
//! than silently returning a value. Predicates are closed-world, so a missing
//! ground fact evaluates to `false`.

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

use crate::error::{QeError, QeResult};
use crate::syntax::{Formula, Term};

type Tuple = Vec<String>;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Sort {
    pub name: String,
    /// Enumerated element names; order defines expansion order and run replay.
    pub elements: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Constant {
    pub name: String,
    pub sort: String,
    pub value: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Function {
    pub name: String,
    /// `param_sorts.len()` is the arity.
    pub param_sorts: Vec<String>,
    pub result_sort: String,
    /// Map "a,b" -> value; partial functions are allowed.
    #[serde(default)]
    pub table: BTreeMap<String, String>,
}

impl Function {
    pub fn tuple_key(args: &[String]) -> String {
        args.join(",")
    }

    pub fn arity(&self) -> usize {
        self.param_sorts.len()
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Predicate {
    pub name: String,
    pub param_sorts: Vec<String>,
    /// Ground facts that hold; everything else is false (closed world).
    #[serde(default)]
    pub facts: Vec<Tuple>,
}

impl Predicate {
    pub fn arity(&self) -> usize {
        self.param_sorts.len()
    }

    pub fn holds(&self, args: &[String]) -> bool {
        self.facts.iter().any(|fact| fact == args)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct Model {
    #[serde(default)]
    pub sorts: Vec<Sort>,
    #[serde(default)]
    pub constants: Vec<Constant>,
    #[serde(default)]
    pub functions: Vec<Function>,
    #[serde(default)]
    pub predicates: Vec<Predicate>,
}

impl Model {
    pub fn sort(&self, name: &str) -> Option<&Sort> {
        self.sorts.iter().find(|s| s.name == name)
    }

    pub fn constant(&self, name: &str) -> Option<&Constant> {
        self.constants.iter().find(|c| c.name == name)
    }

    pub fn function(&self, name: &str) -> Option<&Function> {
        self.functions.iter().find(|f| f.name == name)
    }

    pub fn predicate(&self, name: &str) -> Option<&Predicate> {
        self.predicates.iter().find(|p| p.name == name)
    }

    pub fn total_elements(&self) -> usize {
        self.sorts.iter().map(|s| s.elements.len()).sum()
    }

    /// Structural validation: unique names, element membership and table sorts.
    pub fn validate(&self) -> QeResult<()> {
        let mut seen_sort = BTreeMap::new();
        for sort in &self.sorts {
            if sort.name.trim().is_empty() {
                return Err(QeError::input(
                    "empty_sort_name",
                    "sort names must be non empty",
                ));
            }
            if seen_sort.insert(sort.name.clone(), ()).is_some() {
                return Err(QeError::input(
                    "duplicate_sort",
                    format!("sort {} declared more than once", sort.name),
                ));
            }
            let mut seen_elem = BTreeMap::new();
            for elem in &sort.elements {
                if seen_elem.insert(elem.clone(), ()).is_some() {
                    return Err(QeError::input(
                        "duplicate_element",
                        format!("element {} duplicated in sort {}", elem, sort.name),
                    ));
                }
            }
        }

        let require_sort = |sort: &str, what: &str| -> QeResult<()> {
            if self.sort(sort).is_none() {
                return Err(QeError::input(
                    "unknown_sort",
                    format!("{} references undeclared sort {}", what, sort),
                ));
            }
            Ok(())
        };
        let require_element = |sort_name: &str, value: &str, what: &str| -> QeResult<()> {
            let sort = self.sort(sort_name).ok_or_else(|| {
                QeError::input("unknown_sort", format!("missing sort {}", sort_name))
            })?;
            if !sort.elements.iter().any(|e| e == value) {
                return Err(QeError::input(
                    "unknown_element",
                    format!(
                        "{} value {} is not an element of sort {}",
                        what, value, sort_name
                    ),
                ));
            }
            Ok(())
        };

        let mut symbol_names: BTreeMap<&str, &str> = BTreeMap::new();
        for c in &self.constants {
            require_sort(&c.sort, &format!("constant {}", c.name))?;
            require_element(&c.sort, &c.value, &format!("constant {}", c.name))?;
            if let Some(prev) = symbol_names.insert(c.name.as_str(), "constant") {
                return Err(symbol_conflict(&c.name, prev, "constant"));
            }
        }
        for f in &self.functions {
            for (i, ps) in f.param_sorts.iter().enumerate() {
                require_sort(ps, &format!("function {} argument {}", f.name, i))?;
            }
            require_sort(&f.result_sort, &format!("function {}", f.name))?;
            for (key, value) in &f.table {
                let parts: Vec<&str> = if key.is_empty() {
                    Vec::new()
                } else {
                    key.split(',').collect()
                };
                if parts.len() != f.arity() {
                    return Err(QeError::input(
                        "bad_function_arity",
                        format!(
                            "function {} table key {:?} has {} components, expected {}",
                            f.name,
                            key,
                            parts.len(),
                            f.arity()
                        ),
                    ));
                }
                for (part, ps) in parts.iter().zip(&f.param_sorts) {
                    require_element(ps, part, &format!("function {} key", f.name))?;
                }
                require_element(
                    &f.result_sort,
                    value,
                    &format!("function {} result", f.name),
                )?;
            }
            if let Some(prev) = symbol_names.insert(f.name.as_str(), "function") {
                return Err(symbol_conflict(&f.name, prev, "function"));
            }
        }
        for p in &self.predicates {
            for (i, ps) in p.param_sorts.iter().enumerate() {
                require_sort(ps, &format!("predicate {} argument {}", p.name, i))?;
            }
            for fact in &p.facts {
                if fact.len() != p.arity() {
                    return Err(QeError::input(
                        "bad_predicate_arity",
                        format!(
                            "predicate {} fact {:?} has {} arguments, expected {}",
                            p.name,
                            fact,
                            fact.len(),
                            p.arity()
                        ),
                    ));
                }
                for (value, ps) in fact.iter().zip(&p.param_sorts) {
                    require_element(ps, value, &format!("predicate {} fact", p.name))?;
                }
            }
            if let Some(prev) = symbol_names.insert(p.name.as_str(), "predicate") {
                return Err(symbol_conflict(&p.name, prev, "predicate"));
            }
        }
        Ok(())
    }

    /// Resolve every unqualified element literal and validate the whole formula.
    /// Returns a formula in which all `Elem` literals carry an explicit sort.
    pub fn check_formula(&self, formula: &Formula) -> QeResult<Formula> {
        let mut env = BTreeMap::new();
        self.resolve_formula(formula, &mut env)
    }

    fn resolve_term(&self, term: &Term, env: &BTreeMap<String, String>) -> QeResult<Term> {
        match term {
            Term::Var { name } => {
                if env.contains_key(name) {
                    Ok(term.clone())
                } else {
                    Err(QeError::input(
                        "unbound_variable",
                        format!("variable {} is not bound by any enclosing quantifier", name),
                    ))
                }
            }
            Term::Elem {
                value,
                sort: Some(sort_name),
            } => {
                let sort = self.sort(sort_name).ok_or_else(|| {
                    QeError::input("unknown_sort", format!("unknown sort {}", sort_name))
                })?;
                if !sort.elements.iter().any(|e| e == value) {
                    return Err(QeError::input(
                        "unknown_element",
                        format!("{} is not an element of sort {}", value, sort_name),
                    ));
                }
                Ok(term.clone())
            }
            Term::Elem { value, sort: None } => {
                let mut hits = Vec::new();
                for sort in &self.sorts {
                    if sort.elements.iter().any(|e| e == value) {
                        hits.push(sort.name.clone());
                    }
                }
                match hits.len() {
                    0 => Err(QeError::input(
                        "unknown_element",
                        format!("element literal {} does not belong to any sort", value),
                    )),
                    1 => Ok(Term::Elem {
                        value: value.clone(),
                        sort: Some(hits.remove(0)),
                    }),
                    _ => Err(QeError::input(
                        "ambiguous_element",
                        format!(
                            "element literal {} occurs in sorts {}; qualify it explicitly",
                            value,
                            hits.join(", ")
                        ),
                    )),
                }
            }
            Term::Const { name } => {
                if self.constant(name).is_none() {
                    return Err(QeError::input(
                        "unknown_symbol",
                        format!("unknown constant {}", name),
                    ));
                }
                Ok(term.clone())
            }
            Term::App { name, args } => {
                let function = self.function(name).ok_or_else(|| {
                    QeError::input("unknown_symbol", format!("unknown function {}", name))
                })?;
                if args.len() != function.arity() {
                    return Err(QeError::input(
                        "arity_mismatch",
                        format!(
                            "function {} expects {} arguments but got {}",
                            name,
                            function.arity(),
                            args.len()
                        ),
                    ));
                }
                let mut resolved = Vec::with_capacity(args.len());
                for (arg, expected) in args.iter().zip(&function.param_sorts) {
                    let rt = self.resolve_term(arg, env)?;
                    self.require_term_sort(&rt, expected, env)?;
                    resolved.push(rt);
                }
                Ok(Term::App {
                    name: name.clone(),
                    args: resolved,
                })
            }
        }
    }

    fn term_sort(&self, term: &Term, env: &BTreeMap<String, String>) -> QeResult<String> {
        match term {
            Term::Var { name } => Ok(env[name].clone()),
            Term::Elem { sort: Some(s), .. } => Ok(s.clone()),
            Term::Elem { sort: None, .. } => Err(QeError::computation(
                "unresolved_elem",
                "unresolved element literal",
            )),
            Term::Const { name } => Ok(self.constant(name).unwrap().sort.clone()),
            Term::App { name, .. } => Ok(self.function(name).unwrap().result_sort.clone()),
        }
    }

    fn require_term_sort(
        &self,
        term: &Term,
        expected: &str,
        env: &BTreeMap<String, String>,
    ) -> QeResult<()> {
        let actual = self.term_sort(term, env)?;
        if actual == expected {
            Ok(())
        } else {
            Err(QeError::input(
                "sort_mismatch",
                format!("term has sort {} but {} was expected", actual, expected),
            ))
        }
    }

    fn resolve_formula(
        &self,
        formula: &Formula,
        env: &mut BTreeMap<String, String>,
    ) -> QeResult<Formula> {
        match formula {
            Formula::Bool { .. } => Ok(formula.clone()),
            Formula::Pred { name, args } => {
                let predicate = self.predicate(name).ok_or_else(|| {
                    QeError::input("unknown_symbol", format!("unknown predicate {}", name))
                })?;
                if args.len() != predicate.arity() {
                    return Err(QeError::input(
                        "arity_mismatch",
                        format!(
                            "predicate {} expects {} arguments but got {}",
                            name,
                            predicate.arity(),
                            args.len()
                        ),
                    ));
                }
                let mut resolved = Vec::with_capacity(args.len());
                for (arg, expected) in args.iter().zip(&predicate.param_sorts) {
                    let rt = self.resolve_term(arg, env)?;
                    self.require_term_sort(&rt, expected, env)?;
                    resolved.push(rt);
                }
                Ok(Formula::Pred {
                    name: name.clone(),
                    args: resolved,
                })
            }
            Formula::Eq { left, right } => {
                let l = self.resolve_term(left, env)?;
                let r = self.resolve_term(right, env)?;
                let ls = self.term_sort(&l, env)?;
                let rs = self.term_sort(&r, env)?;
                if ls != rs {
                    return Err(QeError::input(
                        "sort_mismatch",
                        format!("equality compares sorts {} and {}", ls, rs),
                    ));
                }
                Ok(Formula::Eq {
                    left: Box::new(l),
                    right: Box::new(r),
                })
            }
            Formula::Not { inner } => Ok(Formula::Not {
                inner: Box::new(self.resolve_formula(inner, env)?),
            }),
            Formula::And { children } => Ok(Formula::And {
                children: self.resolve_all(children, env)?,
            }),
            Formula::Or { children } => Ok(Formula::Or {
                children: self.resolve_all(children, env)?,
            }),
            Formula::Impl { left, right } => Ok(Formula::Impl {
                left: Box::new(self.resolve_formula(left, env)?),
                right: Box::new(self.resolve_formula(right, env)?),
            }),
            Formula::Iff { left, right } => Ok(Formula::Iff {
                left: Box::new(self.resolve_formula(left, env)?),
                right: Box::new(self.resolve_formula(right, env)?),
            }),
            Formula::Forall { var, sort, inner } | Formula::Exists { var, sort, inner } => {
                if self.sort(sort).is_none() {
                    return Err(QeError::input(
                        "unknown_sort",
                        format!("quantifier on unknown sort {}", sort),
                    ));
                }
                let previous = env.insert(var.clone(), sort.clone());
                let resolved_inner = self.resolve_formula(inner, env);
                match previous {
                    Some(prev) => {
                        env.insert(var.clone(), prev);
                    }
                    None => {
                        env.remove(var);
                    }
                }
                let ri = resolved_inner?;
                Ok(match formula {
                    Formula::Forall { .. } => Formula::Forall {
                        var: var.clone(),
                        sort: sort.clone(),
                        inner: Box::new(ri),
                    },
                    _ => Formula::Exists {
                        var: var.clone(),
                        sort: sort.clone(),
                        inner: Box::new(ri),
                    },
                })
            }
        }
    }

    fn resolve_all(
        &self,
        children: &[Formula],
        env: &mut BTreeMap<String, String>,
    ) -> QeResult<Vec<Formula>> {
        children
            .iter()
            .map(|c| self.resolve_formula(c, env))
            .collect()
    }
}

fn symbol_conflict(name: &str, first: &str, again: &str) -> QeError {
    QeError::input(
        "duplicate_symbol",
        format!("name {} is declared both as {} and {}", name, first, again),
    )
}
