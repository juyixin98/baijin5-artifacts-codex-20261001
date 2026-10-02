//! Independent reference oracle.
//!
//! Every expected result in the test suite is computed here, **not** by the
//! operators under test. The implementations are deliberately simple and
//! written in a different style than the production operators:
//!
//! * sorting is a plain stable sort over cloned rows (no spill, no chunking),
//! * the join is a nested-loop cartesian scan with an equality filter (the
//!   production operator uses a hash table),
//!
//! so a bug shared by "the same algorithm twice" cannot silently pass. These
//! routines operate on plain `Vec<Vec<Scalar>>` and know nothing about Arrow,
//! cancellation, resources or the operator lifecycle.

use std::cmp::Ordering;

use crate::batch::Scalar;

/// Stable ascending sort, nulls first, on the given column indices.
pub fn sort_rows(rows: &[Vec<Scalar>], keys: &[usize]) -> Vec<Vec<Scalar>> {
    let mut indexed: Vec<(usize, &Vec<Scalar>)> = rows.iter().enumerate().collect();
    indexed.sort_by(|(ia, a), (ib, b)| {
        for &k in keys {
            match cmp_scalar(&a[k], &b[k]) {
                Ordering::Equal => continue,
                o => return o,
            }
        }
        ia.cmp(ib) // stable tiebreak on original position
    });
    indexed.into_iter().map(|(_, r)| r.clone()).collect()
}

/// Nested-loop inner equi-join. `out(l, r)` concatenates a left and right row.
/// Null keys never match. Independent of the production hash join.
pub fn inner_join(
    left: &[Vec<Scalar>],
    right: &[Vec<Scalar>],
    lkeys: &[usize],
    rkeys: &[usize],
) -> Vec<Vec<Scalar>> {
    let mut out = Vec::new();
    for l in left {
        for r in right {
            let mut all_eq = true;
            let mut null_key = false;
            for (&lk, &rk) in lkeys.iter().zip(rkeys.iter()) {
                if l[lk].is_null() || r[rk].is_null() {
                    null_key = true;
                    break;
                }
                if cmp_scalar(&l[lk], &r[rk]) != Ordering::Equal {
                    all_eq = false;
                    break;
                }
            }
            if !null_key && all_eq {
                let mut joined = l.clone();
                joined.extend_from_slice(r);
                out.push(joined);
            }
        }
    }
    out
}

pub fn limit_rows(rows: &[Vec<Scalar>], n: usize) -> Vec<Vec<Scalar>> {
    rows.iter().take(n).cloned().collect()
}

pub fn project_rows(rows: &[Vec<Scalar>], cols: &[usize]) -> Vec<Vec<Scalar>> {
    rows.iter()
        .map(|r| cols.iter().map(|&c| r[c].clone()).collect())
        .collect()
}

/// Nulls-first scalar comparison shared by the oracle (independent copy from
/// the operator's `compare_scalar`).
fn cmp_scalar(a: &Scalar, b: &Scalar) -> Ordering {
    if a.is_null() && b.is_null() {
        return Ordering::Equal;
    }
    if a.is_null() {
        return Ordering::Less;
    }
    if b.is_null() {
        return Ordering::Greater;
    }
    match (a, b) {
        (Scalar::Int(Some(x)), Scalar::Int(Some(y))) => x.cmp(y),
        (Scalar::Utf8(Some(x)), Scalar::Utf8(Some(y))) => x.cmp(y),
        (Scalar::Bool(Some(x)), Scalar::Bool(Some(y))) => x.cmp(y),
        _ => Ordering::Equal,
    }
}
