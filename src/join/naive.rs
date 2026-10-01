//! Independent naive reference oracle — **not** used by the engine.
//!
//! This deliberately implements the thing the Leapfrog backend must never do:
//! a left-deep full enumeration that materializes the running cross product
//! after every binary join and only applies the remaining predicates later.
//! It exists so tests can (a) cross-check exact multiset results and (b)
//! demonstrate the intermediate-result blowup that LFJ avoids.
//!
//! It shares no code with [`crate::join::engine`]: it operates on raw rows and
//! hash maps, so an agreement is evidence rather than self-congratulation.
use std::collections::{BTreeMap, HashMap};

use crate::domain::{Datum, Multiplicity, NullPolicy};
use crate::error::{ErrorCode, JoinError, JoinResult};
use crate::query::RelationInput;

#[derive(Debug, Clone)]
pub struct NaiveRow {
    pub values: Vec<Datum>,
    pub multiplicity: u128,
}

#[derive(Debug, Default)]
pub struct NaiveStats {
    /// Size of the running intermediate after each binary join step.
    pub intermediate_sizes: Vec<usize>,
    /// Largest running intermediate held at any step.
    pub max_intermediate: usize,
    /// Nested-loop key probes performed (sum of |left| * |right|).
    pub probes: u128,
}

pub struct NaiveOutput {
    /// Projected rows in the requested attribute order, sorted, multiplicities
    /// aggregated.
    pub rows: Vec<NaiveRow>,
    pub stats: NaiveStats,
}

struct Bag {
    attrs: Vec<String>,
    tuples: Vec<(HashMap<String, Datum>, Multiplicity)>,
}

fn dedup(rel: &RelationInput) -> Bag {
    let attrs: Vec<String> = rel.schema.iter().map(|c| c.name.clone()).collect();
    let mut counts: BTreeMap<Vec<Datum>, Multiplicity> = BTreeMap::new();
    for row in &rel.rows {
        *counts.entry(row.clone()).or_insert(0) += 1;
    }
    let tuples = counts
        .into_iter()
        .map(|(vals, m)| {
            let map = attrs.iter().cloned().zip(vals).collect::<HashMap<_, _>>();
            (map, m)
        })
        .collect();
    Bag { attrs, tuples }
}

fn shared_attrs(left_attrs: &[String], right: &Bag) -> Vec<String> {
    right
        .attrs
        .iter()
        .filter(|a| left_attrs.contains(a))
        .cloned()
        .collect()
}

/// SQL equality: NULL never joins NULL.
fn keys_match(l: &HashMap<String, Datum>, r: &HashMap<String, Datum>, keys: &[String]) -> bool {
    keys.iter().all(|k| {
        let (a, b) = (&l[k], &r[k]);
        !matches!(a, Datum::Null) && !matches!(b, Datum::Null) && a == b
    })
}

/// Run the naive join. `select` is the output attribute order.
pub fn naive_join(
    inputs: &[RelationInput],
    select: &[String],
    null_policy: NullPolicy,
) -> JoinResult<NaiveOutput> {
    assert!(!inputs.is_empty(), "naive oracle needs >= 1 relation");
    let join_attrs: Vec<String> = {
        let mut seen: HashMap<String, usize> = HashMap::new();
        for rel in inputs {
            for c in &rel.schema {
                *seen.entry(c.name.clone()).or_insert(0) += 1;
            }
        }
        seen.into_iter()
            .filter(|(_, n)| *n >= 2)
            .map(|(a, _)| a)
            .collect()
    };

    let mut bags: Vec<Bag> = inputs.iter().map(dedup).collect();

    // Apply NULL policy per relation. SQL equality means NULL never joins NULL.
    for (ri, bag) in bags.iter_mut().enumerate() {
        let local_keys: Vec<String> = join_attrs
            .iter()
            .filter(|k| bag.attrs.contains(k))
            .cloned()
            .collect();
        if null_policy == NullPolicy::Reject {
            for (m, _) in &bag.tuples {
                if local_keys.iter().any(|k| matches!(m[k], Datum::Null)) {
                    return Err(JoinError::new(
                        ErrorCode::NullInJoinKey,
                        format!("relation '{}' has NULL on a join key", inputs[ri].name),
                    ));
                }
            }
        } else {
            bag.tuples
                .retain(|(m, _)| !local_keys.iter().any(|k| matches!(m[k], Datum::Null)));
        }
    }

    // Left-deep full enumeration. The running bag is the (possibly huge)
    // intermediate product; later relations only filter it afterward.
    let mut stats = NaiveStats::default();
    let mut acc = bags.remove(0);
    while !bags.is_empty() {
        let right = bags.remove(0);
        let keys = shared_attrs(&acc.attrs, &right);
        let mut merged_attrs = acc.attrs.clone();
        for a in &right.attrs {
            if !merged_attrs.contains(a) {
                merged_attrs.push(a.clone());
            }
        }
        let mut merged: Vec<(HashMap<String, Datum>, Multiplicity)> =
            Vec::with_capacity(acc.tuples.len() * right.tuples.len().max(1));
        stats.probes = stats
            .probes
            .saturating_add((acc.tuples.len() as u128) * (right.tuples.len() as u128));
        for (l, lm) in &acc.tuples {
            for (r, rm) in &right.tuples {
                if keys_match(l, r, &keys) {
                    let mut m = l.clone();
                    for (k, v) in r {
                        m.entry(k.clone()).or_insert_with(|| v.clone());
                    }
                    merged.push((m, (*lm).saturating_mul(*rm)));
                }
            }
        }
        acc = Bag {
            attrs: merged_attrs,
            tuples: merged,
        };
        stats.intermediate_sizes.push(acc.tuples.len());
        stats.max_intermediate = stats.max_intermediate.max(acc.tuples.len());
    }

    // Projection + multiset aggregation in the requested order.
    let mut grouped: BTreeMap<Vec<Datum>, u128> = BTreeMap::new();
    for (m, mult) in &acc.tuples {
        let key = select.iter().map(|a| m[a].clone()).collect::<Vec<_>>();
        *grouped.entry(key).or_insert(0u128) += u128::from(*mult);
    }
    let rows = grouped
        .into_iter()
        .map(|(values, multiplicity)| NaiveRow {
            values,
            multiplicity,
        })
        .collect();
    Ok(NaiveOutput { rows, stats })
}
