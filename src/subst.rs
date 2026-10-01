//! Capture avoiding substitution.
//!
//! The expansion engine substitutes *closed* element literals, but the
//! contract below is deliberately stronger: substituting an arbitrary term is
//! supported and always alpha-renames a binding whenever the replacement term
//! contains a variable that the binding would capture.

use std::collections::BTreeSet;

use crate::syntax::{Formula, Term};

/// Iterator producing names that do not occur anywhere relevant.
pub fn fresh_name(base: &str, used: &BTreeSet<String>) -> String {
    if !used.contains(base) {
        return base.to_string();
    }
    let mut i = 0usize;
    loop {
        let candidate = format!("{}#{}", base, i);
        if !used.contains(&candidate) {
            return candidate;
        }
        i += 1;
    }
}

/// All variable names occurring (free or bound) in a formula.
pub fn all_names_formula(f: &Formula, out: &mut BTreeSet<String>) {
    match f {
        Formula::Bool { .. } => {}
        Formula::Pred { args, .. } => {
            for a in args {
                all_names_term(a, out);
            }
        }
        Formula::Eq { left, right } => {
            all_names_term(left, out);
            all_names_term(right, out);
        }
        Formula::Not { inner } => all_names_formula(inner, out),
        Formula::And { children } | Formula::Or { children } => {
            for c in children {
                all_names_formula(c, out);
            }
        }
        Formula::Impl { left, right } | Formula::Iff { left, right } => {
            all_names_formula(left, out);
            all_names_formula(right, out);
        }
        Formula::Forall { var, inner, .. } | Formula::Exists { var, inner, .. } => {
            out.insert(var.clone());
            all_names_formula(inner, out);
        }
    }
}

pub fn all_names_term(t: &Term, out: &mut BTreeSet<String>) {
    match t {
        Term::Var { name } => {
            out.insert(name.clone());
        }
        Term::Elem { .. } | Term::Const { .. } => {}
        Term::App { args, .. } => {
            for a in args {
                all_names_term(a, out);
            }
        }
    }
}

/// Rename the bound variable of a quantifier, recursively rewriting free
/// occurrences inside its scope. Used internally by substitution.
pub fn rename_bound(f: &Formula, from: &str, to: &str) -> Formula {
    match f {
        Formula::Forall { var, sort, inner } if var == from => Formula::Forall {
            var: to.to_string(),
            sort: sort.clone(),
            inner: Box::new(rename_free(
                inner,
                from,
                &Term::Var {
                    name: to.to_string(),
                },
            )),
        },
        Formula::Exists { var, sort, inner } if var == from => Formula::Exists {
            var: to.to_string(),
            sort: sort.clone(),
            inner: Box::new(rename_free(
                inner,
                from,
                &Term::Var {
                    name: to.to_string(),
                },
            )),
        },
        other => other.clone(),
    }
}

/// Replace free occurrences of `var` in a term.
pub fn substitute_term(t: &Term, var: &str, replacement: &Term) -> Term {
    match t {
        Term::Var { name } if name == var => replacement.clone(),
        Term::Var { .. } | Term::Elem { .. } | Term::Const { .. } => t.clone(),
        Term::App { name, args } => Term::App {
            name: name.clone(),
            args: args
                .iter()
                .map(|a| substitute_term(a, var, replacement))
                .collect(),
        },
    }
}

/// Replace *free* occurrences of `var` in a formula, avoiding capture.
/// Bindings that shadow `var` naturally stop the recursion; a binding whose
/// own name occurs free in `replacement` is alpha-renamed first.
pub fn rename_free(f: &Formula, var: &str, replacement: &Term) -> Formula {
    match f {
        Formula::Bool { .. } => f.clone(),
        Formula::Pred { name, args } => Formula::Pred {
            name: name.clone(),
            args: args
                .iter()
                .map(|a| substitute_term(a, var, replacement))
                .collect(),
        },
        Formula::Eq { left, right } => Formula::Eq {
            left: Box::new(substitute_term(left, var, replacement)),
            right: Box::new(substitute_term(right, var, replacement)),
        },
        Formula::Not { inner } => Formula::Not {
            inner: Box::new(rename_free(inner, var, replacement)),
        },
        Formula::And { children } => Formula::And {
            children: children
                .iter()
                .map(|c| rename_free(c, var, replacement))
                .collect(),
        },
        Formula::Or { children } => Formula::Or {
            children: children
                .iter()
                .map(|c| rename_free(c, var, replacement))
                .collect(),
        },
        Formula::Impl { left, right } => Formula::Impl {
            left: Box::new(rename_free(left, var, replacement)),
            right: Box::new(rename_free(right, var, replacement)),
        },
        Formula::Iff { left, right } => Formula::Iff {
            left: Box::new(rename_free(left, var, replacement)),
            right: Box::new(rename_free(right, var, replacement)),
        },
        Formula::Forall {
            var: bound,
            sort,
            inner,
        } => rename_binding(f, var, replacement, bound, sort, inner, true),
        Formula::Exists {
            var: bound,
            sort,
            inner,
        } => rename_binding(f, var, replacement, bound, sort, inner, false),
    }
}

fn rename_binding(
    original: &Formula,
    target: &str,
    replacement: &Term,
    bound: &str,
    sort: &str,
    inner: &Formula,
    is_forall: bool,
) -> Formula {
    // The binding shadows the target variable; nothing inside is free.
    if bound == target {
        return original.clone();
    }
    let mut rep_names = BTreeSet::new();
    all_names_term(replacement, &mut rep_names);
    if rep_names.contains(bound) {
        // Capture risk: alpha rename the binding before descending.
        let mut used = BTreeSet::new();
        all_names_formula(inner, &mut used);
        all_names_term(replacement, &mut used);
        let fresh = fresh_name(bound, &used);
        let renamed = rename_bound(
            &if is_forall {
                Formula::Forall {
                    var: bound.to_string(),
                    sort: sort.to_string(),
                    inner: Box::new(inner.clone()),
                }
            } else {
                Formula::Exists {
                    var: bound.to_string(),
                    sort: sort.to_string(),
                    inner: Box::new(inner.clone()),
                }
            },
            bound,
            &fresh,
        );
        return rename_free(&renamed, target, replacement);
    }
    let new_inner = rename_free(inner, target, replacement);
    if is_forall {
        Formula::Forall {
            var: bound.to_string(),
            sort: sort.to_string(),
            inner: Box::new(new_inner),
        }
    } else {
        Formula::Exists {
            var: bound.to_string(),
            sort: sort.to_string(),
            inner: Box::new(new_inner),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::syntax::build::*;

    #[test]
    fn substitutes_free_occurrences_only() {
        // ∀y. p(x) ∧ q(y)   [x := z]
        let f = forall(
            "y",
            "S",
            and(vec![pred("p", vec![var("x")]), pred("q", vec![var("y")])]),
        );
        let got = rename_free(&f, "x", &var("z"));
        let want = forall(
            "y",
            "S",
            and(vec![pred("p", vec![var("z")]), pred("q", vec![var("y")])]),
        );
        assert_eq!(got, want);
    }

    #[test]
    fn shadowing_binding_blocks_substitution() {
        // ∀x. p(x)  [x := a] must not touch the bound x.
        let f = forall("x", "S", pred("p", vec![var("x")]));
        let got = rename_free(&f, "x", &elem("a"));
        assert_eq!(got, f);
    }

    #[test]
    fn alpha_renames_to_avoid_capture() {
        // ∃y. x = y   [x := y]  must become ∃y#k. y = y#k (no capture).
        let f = exists("y", "S", eq(var("x"), var("y")));
        let got = rename_free(&f, "x", &var("y"));

        // Free variable computation on the substituted formula is the key
        // semantic check: outer `y` remains free; no new binding captured it.
        let free = got.free_vars();
        assert_eq!(free, vec!["y".to_string()]);

        match got {
            Formula::Exists { var, inner, .. } => {
                assert_ne!(var, "y", "bound variable must have been alpha-renamed");
                assert!(var.starts_with("y#"));
                if let Formula::Eq { left, right } = *inner {
                    assert_eq!(
                        *left,
                        Term::Var {
                            name: "y".to_string()
                        }
                    );
                    assert_eq!(*right, Term::Var { name: var.clone() });
                } else {
                    panic!("expected equality body");
                }
            }
            _ => panic!("expected exists after capture avoiding substitution"),
        }
    }

    #[test]
    fn nested_shadow_renames_only_offending_binder() {
        // ∀y. ∀y. q(x, y)   [x := y]
        // Outer ∀y would capture; it is renamed, inner ∀y shadows both.
        let f = forall(
            "y",
            "S",
            forall("y", "S", pred("q", vec![var("x"), var("y")])),
        );
        let got = rename_free(&f, "x", &var("y"));
        let free = got.free_vars();
        assert_eq!(free, vec!["y".to_string()]);
    }

    #[test]
    fn fresh_name_avoids_used_set() {
        let mut used = BTreeSet::new();
        used.insert("x".to_string());
        used.insert("x#0".to_string());
        assert_eq!(fresh_name("x", &used), "x#1");
    }
}
