//! Inference core: the propositional resolution rule.
//!
//! Given clauses `A v p` and `B v ~p`, resolution on pivot variable `p`
//! derives `A v B`. The rule is only legal when the pivot occurs with
//! opposite polarities in the two parents and the resolvent is not a
//! tautology (no *other* variable appears in both polarities across the
//! parents).

use crate::syntax::{Clause, Literal};
use std::fmt;

/// Why a resolution step is not a legal inference.
#[derive(Clone, PartialEq, Eq, Debug)]
pub enum ResolveError {
    /// The pivot variable does not occur positively in the left parent and
    /// negatively in the right parent (in either assignment of sides).
    PivotMissing { pivot: u32 },
    /// Eliminating the pivot leaves a tautological resolvent: some other
    /// variable occurs in both polarities across the parents.
    TautologicalResolvent { pivot: u32, var: u32 },
}

impl fmt::Display for ResolveError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ResolveError::PivotMissing { pivot } => write!(
                f,
                "pivot {pivot} does not occur with opposite signs in the parents"
            ),
            ResolveError::TautologicalResolvent { pivot, var } => write!(
                f,
                "resolving on pivot {pivot} leaves variable {var} in both polarities (tautology)"
            ),
        }
    }
}

/// Resolve `left` and `right` on `pivot`, returning the normalized resolvent.
///
/// The side on which the pivot occurs positively is irrelevant; both
/// orientations are accepted.
pub fn resolve(left: &Clause, right: &Clause, pivot: u32) -> Result<Clause, ResolveError> {
    let pos = Literal::new(pivot, true);
    let neg = Literal::new(pivot, false);

    let (keep_pos_side, keep_neg_side) = if left.contains(pos) && right.contains(neg) {
        (left, right)
    } else if left.contains(neg) && right.contains(pos) {
        (right, left)
    } else {
        return Err(ResolveError::PivotMissing { pivot });
    };

    // Union of both parents minus the two pivot literals. Inputs are sorted
    // and duplicate-free, so a merge keeps the result normalized.
    let mut lits: Vec<Literal> = Vec::with_capacity(left.len() + right.len());
    let push = |lit: Literal, lits: &mut Vec<Literal>| {
        if lit.var != pivot && !lits.contains(&lit) {
            lits.push(lit);
        }
    };
    for &lit in keep_pos_side.lits() {
        push(lit, &mut lits);
    }
    for &lit in keep_neg_side.lits() {
        push(lit, &mut lits);
    }
    lits.sort();

    // Legality: the resolvent must not be tautological, i.e. the pivot must
    // be the *only* variable the parents shared with opposite signs.
    for w in lits.windows(2) {
        if w[0].var == w[1].var {
            return Err(ResolveError::TautologicalResolvent {
                pivot,
                var: w[0].var,
            });
        }
    }

    // Safe: constructed sorted and duplicate-free above.
    Ok(Clause::from_raw(lits).expect("resolvent is normalized by construction"))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn clause(lits: &[i64]) -> Clause {
        Clause::from_raw(lits.iter().map(|&v| Literal::from_dimacs(v).unwrap()).collect())
            .unwrap()
    }

    #[test]
    fn resolves_to_empty_clause() {
        let a = clause(&[1]);
        let b = clause(&[-1]);
        let r = resolve(&a, &b, 1).unwrap();
        assert!(r.is_empty());
    }

    #[test]
    fn merges_parents_without_pivot() {
        let a = clause(&[1, 3]);
        let b = clause(&[-1, 2]);
        let r = resolve(&a, &b, 1).unwrap();
        assert_eq!(r, clause(&[2, 3]));
    }

    #[test]
    fn accepts_either_orientation() {
        let a = clause(&[-1, 2]);
        let b = clause(&[1, 3]);
        let r = resolve(&a, &b, 1).unwrap();
        assert_eq!(r, clause(&[2, 3]));
    }

    #[test]
    fn rejects_missing_pivot() {
        let a = clause(&[1]);
        let b = clause(&[2]);
        assert_eq!(
            resolve(&a, &b, 1),
            Err(ResolveError::PivotMissing { pivot: 1 })
        );
    }

    #[test]
    fn rejects_tautological_resolvent() {
        // (x v y) and (~x v ~y): resolving on x leaves y v ~y.
        let a = clause(&[1, 2]);
        let b = clause(&[-1, -2]);
        assert_eq!(
            resolve(&a, &b, 1),
            Err(ResolveError::TautologicalResolvent { pivot: 1, var: 2 })
        );
    }
}
