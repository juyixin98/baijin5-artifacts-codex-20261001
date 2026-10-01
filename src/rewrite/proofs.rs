//! Applicability proofs for the decorrelating rewrite.
//!
//! Each supported form is emitted only when the preconditions below hold. The
//! proofs are short but explicit: they state the nested-loop semantics, the
//! rewritten bulk semantics, and the invariant that makes the two equal,
//! including the NULL and cardinality edge cases the tests pin down.
//!
//! These strings are surfaced in API responses (`rewrite.proofs`) and are
//! also asserted by name in the independent test suite, so a rewrite cannot
//! silently drop a precondition.

use serde::Serialize;

use crate::query::SubForm;

/// One applicability/equivalence argument.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Proof {
    /// Stable identifier asserted in tests.
    pub id: &'static str,
    /// The form this argument applies to.
    pub form: &'static str,
    /// One-line statement.
    pub claim: &'static str,
    /// Why the rewrite is equivalent, including NULL/empty-group handling.
    pub argument: &'static str,
}

const CORRELATION_COMMON: &str =
    "Correlation is a conjunction of inner.col = outer.col equalities. The `=` \
predicate is TRUE only when both sides are non-NULL and equal; it is FALSE or \
UNKNOWN whenever either side is NULL. Therefore inner rows with a NULL \
correlation column match no outer row, and an outer row with a NULL \
correlation column matches no group. Grouping on the inner correlation \
columns after discarding NULL-keyed rows partitions exactly the inner rows \
the nested loop would visit, and the hash lookup reproduces the TRUE \
equalities. Outer rows are scanned in order and never collapsed, so order \
and duplicate multiplicity are preserved.";

pub fn proofs_for(form: SubForm) -> Vec<Proof> {
    let mut v = vec![Proof {
        id: "correlation-key-partition",
        form: form_name(form),
        claim: "Group lookup reproduces the nested-loop correlation equalities.",
        argument: CORRELATION_COMMON,
    }];

    match form {
        SubForm::Exists | SubForm::NotExists => v.push(Proof {
            id: "exists-semi-anti",
            form: form_name(form),
            claim: "EXISTS is a left-semi join; NOT EXISTS is a left-anti join.",
            argument:
                "For an outer row the nested loop returns TRUE for EXISTS iff at least one inner \
row satisfies all local and correlation predicates. The grouped plan yields a \
non-empty group in exactly that case (semi join); NOT EXISTS negates it (anti \
join). An outer NULL correlation key and an empty inner table both yield no \
group, hence EXISTS is FALSE and NOT EXISTS is TRUE, identical to the loop.",
        }),
        SubForm::ScalarAgg => v.push(Proof {
            id: "scalar-agg-group",
            claim: "The scalar aggregate over matched rows equals the group aggregate.",
            form: "scalar_agg",
            argument:
                "The nested loop aggregates exactly the matching inner rows per outer row. By the \
partition proof those rows are precisely the looked-up group. COUNT(col) counts \
non-NULL values and returns 0 on an empty/absent group; SUM(col) ignores NULLs \
and returns NULL when the group has no non-NULL value, which includes the \
empty and absent-group cases. The accumulator is identical in both engines \
and scans rows in the same order, so overflow errors also occur at the same \
outer row. The aggregate always produces exactly one value, so no cardinality \
error is possible for this form.",
        }),
        SubForm::ScalarBare => v.push(Proof {
            id: "scalar-bare-cardinality",
            claim: "A bare scalar subquery is valid only when each group has <= 1 row.",
            form: "scalar_bare",
            argument:
                "The nested loop yields NULL when zero inner rows match, the single projected \
value when exactly one matches, and raises scalar-multiple-rows when more than \
one matches. The grouped plan returns NULL for an absent (or empty) group, the \
group's value when it has one row, and raises the identical error when it has \
more than one. NULL in the projected value is carried through and the outer \
comparison then evaluates to UNKNOWN under 3VL, rejecting the row as the loop \
does.",
        }),
        SubForm::InUnaggregated => v.push(Proof {
            id: "in-null-aware",
            claim: "IN is NULL-aware membership within the matched group.",
            form: "in_unaggregated",
            argument:
                "For a non-NULL outer value, the nested loop returns TRUE if any matching inner \
row projects an equal value, UNKNOWN if no equal is found but a projected NULL \
exists, and FALSE otherwise. The grouped plan performs the same 3VL scan over \
the one matched group (the only place an equal value can occur). A NULL outer \
value yields UNKNOWN immediately. Note this is distinct from NOT IN, which is \
unsupported: under NOT IN a single NULL on either side forces UNKNOWN even \
when an equal/distinct value exists.",
        }),
    }
    v
}

fn form_name(f: SubForm) -> &'static str {
    match f {
        SubForm::Exists => "exists",
        SubForm::NotExists => "not_exists",
        SubForm::ScalarAgg => "scalar_agg",
        SubForm::ScalarBare => "scalar_bare",
        SubForm::InUnaggregated => "in_unaggregated",
    }
}

/// Forms that are recognized but intentionally rejected, with the reason.
pub fn rejected_forms() -> Vec<(&'static str, &'static str)> {
    vec![
        (
            "not_in",
            "NOT IN (subquery) has different NULL semantics from NOT EXISTS: if the subquery \
returns any NULL, NOT IN evaluates to UNKNOWN for all outer rows (the \
NOT-IN trap). It cannot be rewritten to the anti join without an explicit \
null-rejecting predicate, so it is rejected rather than mis-compiled.",
        ),
        (
            "join/set-op/nested-subquery",
            "Joins, UNION/INTERSECT/EXCEPT, subqueries nested inside subqueries, more than one \
subquery predicate, non-equality correlation, and HAVING/ORDER BY/LIMIT are \
outside the restricted fragment and are rejected at validation.",
        ),
    ]
}
