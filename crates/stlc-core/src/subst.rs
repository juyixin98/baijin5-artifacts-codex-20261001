//! Capture-avoiding substitution and de Bruijn shifting (TAPL §6).

use stlc_syntax::db::DbTerm;

/// Shift all free (relative to cutoff `c`) bound indices by `by`.
/// Negative shifts are expressed via signed `by`; callers guarantee the
/// affected indices stay non-negative.
pub fn shift(term: &DbTerm, by: i64) -> DbTerm {
    fn walk(term: &DbTerm, c: usize, by: i64) -> DbTerm {
        match term {
            DbTerm::BVar { index } => {
                if *index >= c {
                    let shifted = (*index as i64) + by;
                    debug_assert!(shifted >= 0, "negative index produced by shift");
                    DbTerm::BVar {
                        index: shifted as usize,
                    }
                } else {
                    term.clone()
                }
            }
            DbTerm::FVar { .. } | DbTerm::BoolLit { .. } => term.clone(),
            DbTerm::Abs { param_ty, body } => DbTerm::Abs {
                param_ty: param_ty.clone(),
                body: Box::new(walk(body, c + 1, by)),
            },
            DbTerm::App { func, arg } => DbTerm::App {
                func: Box::new(walk(func, c, by)),
                arg: Box::new(walk(arg, c, by)),
            },
            DbTerm::If {
                cond,
                then,
                otherwise,
            } => DbTerm::If {
                cond: Box::new(walk(cond, c, by)),
                then: Box::new(walk(then, c, by)),
                otherwise: Box::new(walk(otherwise, c, by)),
            },
        }
    }
    walk(term, 0, by)
}

/// Substitute free occurrence of de Bruijn index 0 by `replacement`.
/// (`[term -> term]` in TAPL's j-is-for-index notation.)
pub fn substitute(target_index: usize, replacement: &DbTerm, term: &DbTerm) -> DbTerm {
    fn walk(
        target: usize,
        replacement: &DbTerm,
        term: &DbTerm,
        cutoff: usize,
    ) -> DbTerm {
        match term {
            DbTerm::BVar { index } => {
                if *index == target + cutoff {
                    shift(replacement, cutoff as i64)
                } else {
                    term.clone()
                }
            }
            DbTerm::FVar { .. } | DbTerm::BoolLit { .. } => term.clone(),
            DbTerm::Abs { param_ty, body } => DbTerm::Abs {
                param_ty: param_ty.clone(),
                body: Box::new(walk(target, replacement, body, cutoff + 1)),
            },
            DbTerm::App { func, arg } => DbTerm::App {
                func: Box::new(walk(target, replacement, func, cutoff)),
                arg: Box::new(walk(target, replacement, arg, cutoff)),
            },
            DbTerm::If {
                cond,
                then,
                otherwise,
            } => DbTerm::If {
                cond: Box::new(walk(target, replacement, cond, cutoff)),
                then: Box::new(walk(target, replacement, then, cutoff)),
                otherwise: Box::new(walk(target, replacement, otherwise, cutoff)),
            },
        }
    }
    walk(target_index, replacement, term, 0)
}

/// Beta contract one redex `(lam. body) arg`.
///
/// TAPL's `substTop`: the replacement is shifted up past the consumed binder
/// before substitution (`↑¹(arg)`), then the whole result is shifted down
/// (`↑⁻¹`) as the binder disappears. Skipping the initial shift captures
/// free variables of the argument under binders in the body.
pub fn beta_contract(body: &DbTerm, arg: &DbTerm) -> DbTerm {
    let raised = shift(arg, 1);
    let substituted = substitute(0, &raised, body);
    shift(&substituted, -1)
}
