//! Hash group table for the inner relation.
//!
//! The inner batch is filtered by its own (non-correlated) local predicates,
//! then grouped by the inner-side columns of the correlation equalities.
//!
//! Two SQL rules are baked in here and are load-bearing for correctness:
//!
//! 1. Correlation uses `=` three-valued semantics. An inner row whose
//!    correlation key contains any NULL can match **no** outer row (that
//!    equality is UNKNOWN), so such rows are excluded when grouping.
//! 2. An outer row whose correlation key contains any NULL matches **no**
//!    group — including a group of NULL-keyed inner rows. Such a lookup
//!    returns "no match", exactly like a nested-loop scan finding zero rows.
//!
//! Group aggregates are computed lazily on first lookup so that groups no
//! outer row ever reaches cannot raise errors (matching nested-loop
//! evaluation order).

use std::collections::HashMap;

use crate::batch::{Batch, KeyBuf, Scalar};
use crate::error::QResult;
use crate::operators::{eval_comparison, is_true};
use crate::validator::{RInnerTerm, RSubquery};

/// Rows of one group, stored as inner-row indices.
#[derive(Debug, Default)]
pub struct GroupData {
    pub rows: Vec<usize>,
}

impl GroupData {
    pub fn len(&self) -> usize {
        self.rows.len()
    }

    pub fn is_empty(&self) -> bool {
        self.rows.is_empty()
    }
}

/// Pre-grouped inner relation for one subquery.
#[derive(Debug)]
pub struct GroupTable<'a> {
    sub: &'a RSubquery,
    inner: &'a Batch,
    /// Indices into `sub.terms` for the correlation terms.
    corr_terms: Vec<usize>,
    groups: HashMap<KeyBuf, GroupData>,
}

impl<'a> GroupTable<'a> {
    pub fn build(sub: &'a RSubquery) -> QResult<Self> {
        let inner = sub.inner_batch.as_ref();
        let mut corr_terms = Vec::new();
        let local_terms: Vec<usize> = sub
            .terms
            .iter()
            .enumerate()
            .filter_map(|(i, t)| match t {
                RInnerTerm::Corr { .. } => {
                    corr_terms.push(i);
                    None
                }
                RInnerTerm::Local(_) => Some(i),
            })
            .collect();

        let mut groups: HashMap<KeyBuf, GroupData> = HashMap::new();
        'rows: for row in 0..inner.row_count() {
            // Inner-local predicates: all must be TRUE under 3VL.
            for &ti in &local_terms {
                if let RInnerTerm::Local(cmp) = &sub.terms[ti] {
                    if !is_true(eval_comparison(cmp, inner, row)?) {
                        continue 'rows;
                    }
                }
            }
            // Correlation key: any NULL on the inner side can never match.
            let mut key = Vec::with_capacity(corr_terms.len());
            for &ti in &corr_terms {
                if let RInnerTerm::Corr { inner: ic, .. } = &sub.terms[ti] {
                    let v = inner.get(ic.index, row);
                    if v.is_null() {
                        continue 'rows;
                    }
                    key.push(v.clone());
                }
            }
            groups.entry(KeyBuf(key)).or_default().rows.push(row);
        }

        Ok(Self {
            sub,
            inner,
            corr_terms,
            groups,
        })
    }

    /// Number of distinct (non-NULL) correlation keys.
    pub fn group_count(&self) -> usize {
        self.groups.len()
    }

    /// Look up the group an outer row correlates to.
    ///
    /// Returns `None` when the outer key contains NULL or no inner group
    /// exists for it — behaviorally identical for every supported form.
    pub fn lookup(&self, outer: &Batch, outer_row: usize) -> QResult<Option<(&GroupData, KeyBuf)>> {
        let mut key = Vec::with_capacity(self.corr_terms.len());
        for &ti in &self.corr_terms {
            if let RInnerTerm::Corr { outer: oc, .. } = &self.sub.terms[ti] {
                let v = outer.get(oc.index, outer_row);
                if v.is_null() {
                    return Ok(None);
                }
                key.push(v.clone());
            }
        }
        let kb = KeyBuf(key);
        Ok(self.groups.get_key_value(&kb).map(|(k, g)| (g, k.clone())))
    }

    pub fn inner(&self) -> &'a Batch {
        self.inner
    }

    /// Value of the subquery projection column in an inner row (IN / bare scalar).
    pub fn project_value(&self, inner_row: usize) -> Scalar {
        match &self.sub.project {
            Some(c) => self.inner.get(c.index, inner_row).clone(),
            None => Scalar::Null,
        }
    }
}
