//! Reusable synthetic fixtures.
//!
//! Every scenario is fully local and deterministic. Scenarios back the
//! integration tests, the CLI (`run --scenario`), and self-contained
//! replay traces. Expected answers for the canonical scenarios are
//! **hand-computed literals** declared next to the data; the independent
//! nested-loop reference ([`crate::operator::reference`]) is used as an
//! additional cross-check, never as the sole oracle.

use crate::batch::Batch;
use crate::dto::BatchDto;
use crate::error::JoinResult;
use crate::operator::comparator::Comparator;
use crate::operator::plan::{JoinPlan, Predicate};

/// A named, fully specified join scenario.
pub struct Scenario {
    pub name: &'static str,
    pub description: &'static str,
    pub plan: JoinPlan,
    pub left: Batch,
    pub right: Batch,
}

fn col(name: &str, values: Vec<i64>) -> crate::batch::Column {
    crate::batch::Column::new(name, values.into_iter().map(Some).collect())
}

fn coln(name: &str, values: Vec<Option<i64>>) -> crate::batch::Column {
    crate::batch::Column::new(name, values)
}

fn ids(n: usize, prefix: &str) -> Vec<String> {
    (0..n).map(|i| format!("{prefix}{i}")).collect()
}

fn batch(columns: Vec<crate::batch::Column>, ids: Vec<String>) -> Batch {
    Batch::new(columns).unwrap().with_row_ids(ids)
}

fn pred(l: &str, op: Comparator, r: &str) -> Predicate {
    Predicate::new(l, op, r)
}

/// The four canonical directions over a small anti-diagonal relation.
///
/// `L(x,y)`: `(1,4),(2,3),(3,2),(4,1)` ids `l0..l3`.
/// Two right rows are available; each direction test uses the one named
/// in the expectation.
fn grid_scenario(op: Comparator) -> Scenario {
    let left = batch(
        vec![col("x", vec![1, 2, 3, 4]), col("y", vec![4, 3, 2, 1])],
        ids(4, "l"),
    );
    // ra = (3,4) for ascending senses, rb = (2,1) for descending.
    let right = batch(
        vec![col("x", vec![3, 2]), col("y", vec![4, 1])],
        vec!["ra".to_owned(), "rb".to_owned()],
    );
    let (desc, name) = match op {
        Comparator::Lt => ("strict ascending  L.x<R.x and L.y<R.y", "grid_lt"),
        Comparator::Le => ("non-strict ascending L.x<=R.x and L.y<=R.y", "grid_le"),
        Comparator::Gt => ("strict descending L.x>R.x and L.y>R.y", "grid_gt"),
        Comparator::Ge => ("non-strict descending L.x>=R.x and L.y>=R.y", "grid_ge"),
    };
    Scenario {
        name,
        description: desc,
        plan: JoinPlan::new(pred("x", op, "x"), pred("y", op, "y")),
        left,
        right,
    }
}

/// Hand-computed expected output multiset for the grid scenarios as
/// `(left_id, right_id)` pairs. Verified by hand against the data above.
///
/// Derivation (right row `ra`=(3,4), `rb`=(2,1)):
/// * `<` : x<3 → {l0,l1}; y<4 → {l1,l2,l3}; intersect → **{l1}** with ra.
/// * `<=`: x<=3 → {l0,l1,l2}; y<=4 → all; intersect → **{l0,l1,l2}** with ra.
/// * `>` : x>2 → {l2,l3}; y>1 → {l0,l1,l2}; intersect → **{l2}** with rb.
/// * `>=`: x>=2 → {l1,l2,l3}; y>=1 → all; intersect → **{l1,l2,l3}** with rb.
#[must_use]
pub fn grid_expected(op: Comparator) -> Vec<(&'static str, &'static str)> {
    match op {
        Comparator::Lt => vec![("l1", "ra")],
        Comparator::Le => vec![("l0", "ra"), ("l1", "ra"), ("l2", "ra")],
        Comparator::Gt => vec![("l2", "rb")],
        Comparator::Ge => vec![("l1", "rb"), ("l2", "rb"), ("l3", "rb")],
    }
}

/// All-equal values: strict yields nothing, non-strict the full 3×2
/// Cartesian product. Duplicates carry distinct identities.
pub fn all_equal_scenario(strict: bool) -> Scenario {
    let op = if strict {
        Comparator::Lt
    } else {
        Comparator::Le
    };
    Scenario {
        name: if strict {
            "equal_strict"
        } else {
            "equal_lenient"
        },
        description: "three left and two right rows with identical keys",
        plan: JoinPlan::new(pred("x", op, "x"), pred("y", op, "y")),
        left: batch(
            vec![col("x", vec![5, 5, 5]), col("y", vec![5, 5, 5])],
            vec!["a".into(), "b".into(), "c".into()],
        ),
        right: batch(
            vec![col("x", vec![5, 5]), col("y", vec![5, 5])],
            vec!["p".into(), "q".into()],
        ),
    }
}

/// Hand-computed expectation for [`all_equal_scenario`].
#[must_use]
pub fn all_equal_expected(strict: bool) -> Vec<(&'static str, &'static str)> {
    if strict {
        Vec::new()
    } else {
        vec![
            ("a", "p"),
            ("a", "q"),
            ("b", "p"),
            ("b", "q"),
            ("c", "p"),
            ("c", "q"),
        ]
    }
}

/// Empty-side scenarios (both directions of emptiness).
pub fn empty_scenario(empty_left: bool) -> Scenario {
    let non_empty = batch(
        vec![col("x", vec![1, 2]), col("y", vec![1, 2])],
        ids(2, "n"),
    );
    let empty = Batch::new(vec![col("x", Vec::new()), col("y", Vec::new())]).unwrap();
    let (left, right, name) = if empty_left {
        (empty, non_empty, "empty_left")
    } else {
        (non_empty, empty, "empty_right")
    };
    Scenario {
        name,
        description: "one relation is empty",
        plan: JoinPlan::new(
            pred("x", Comparator::Lt, "x"),
            pred("y", Comparator::Lt, "y"),
        ),
        left,
        right,
    }
}

/// NULL handling: only the fully non-NULL matching row joins.
pub fn null_scenario() -> Scenario {
    Scenario {
        name: "nulls",
        description: "NULL on either key never matches",
        plan: JoinPlan::new(
            pred("x", Comparator::Lt, "x"),
            pred("y", Comparator::Lt, "y"),
        ),
        // l0 NULL x; l1 NULL y; l2 (1,1) the only viable row.
        left: batch(
            vec![
                coln("x", vec![None, Some(1), Some(1)]),
                coln("y", vec![Some(1), None, Some(1)]),
            ],
            ids(3, "n"),
        ),
        right: batch(
            vec![col("x", vec![2]), col("y", vec![2])],
            vec!["r".to_owned()],
        ),
    }
}

/// Hand-computed expectation for [`null_scenario`].
#[must_use]
pub fn null_expected() -> Vec<(&'static str, &'static str)> {
    vec![("n2", "r")]
}

/// Anti-correlated 0..N relation for selectivity accounting.
///
/// Left row `i` has `x=i, y=99-i`. The single right row is
/// `(x=10, y=95)`. With `<` on both:
///
/// * `x<10` selects 10 rows (i = 0..=9),
/// * `y<95` selects i with `99-i<95` → i>4 → i = 5..=99 (95 rows),
/// * intersection is i ∈ {5,6,7,8,9} → **5 pairs**.
///
/// In permutation 2 (y ascending) the ten predicate-1 candidates occupy
/// positions 90..99, and the strict predicate-2 boundary is prefix 95.
/// The bitmap reader emits positions 90..94 (five pairs), then locates
/// the next candidate at position 95 (row i=4, y=95); that one
/// inspection fails the strict `<` boundary and ends the row: **6
/// candidate accesses** for the sole right row, versus 100 nested-loop
/// predicate-1 evaluations.
#[must_use]
pub fn selective_scenario() -> Scenario {
    let n = 100i64;
    let xs: Vec<i64> = (0..n).collect();
    let ys: Vec<i64> = (0..n).rev().collect();
    Scenario {
        name: "selective",
        description: "anti-correlated keys, one highly selective right row",
        plan: JoinPlan::new(
            pred("x", Comparator::Lt, "x"),
            pred("y", Comparator::Lt, "y"),
        ),
        left: batch(vec![col("x", xs), col("y", ys)], ids(n as usize, "s")),
        right: batch(
            vec![col("x", vec![10]), col("y", vec![95])],
            vec!["r".to_owned()],
        ),
    }
}

/// Hand-computed expected ids for [`selective_scenario`].
#[must_use]
pub fn selective_expected() -> Vec<(&'static str, &'static str)> {
    vec![
        ("s5", "r"),
        ("s6", "r"),
        ("s7", "r"),
        ("s8", "r"),
        ("s9", "r"),
    ]
}

/// Exact IEJoin instrumentation expected for [`selective_scenario`].
pub const SELECTIVE_EXPECTED_CANDIDATE_ACCESSES: u64 = 6;
pub const SELECTIVE_EXPECTED_GATE_STEPS: u64 = 10;
pub const SELECTIVE_EXPECTED_PAIRS: u64 = 5;
pub const SELECTIVE_NL_P1_EVALUATIONS: u64 = 100;

/// Registry of every named scenario.
#[must_use]
pub fn all_named() -> Vec<&'static str> {
    vec![
        "grid_lt",
        "grid_le",
        "grid_gt",
        "grid_ge",
        "equal_strict",
        "equal_lenient",
        "empty_left",
        "empty_right",
        "nulls",
        "selective",
    ]
}

/// Look up a scenario by name.
///
/// # Errors
/// `Input` (`unknown_scenario`) for an unknown name.
pub fn by_name(name: &str) -> JoinResult<Scenario> {
    let s = match name {
        "grid_lt" => grid_scenario(Comparator::Lt),
        "grid_le" => grid_scenario(Comparator::Le),
        "grid_gt" => grid_scenario(Comparator::Gt),
        "grid_ge" => grid_scenario(Comparator::Ge),
        "equal_strict" => all_equal_scenario(true),
        "equal_lenient" => all_equal_scenario(false),
        "empty_left" => empty_scenario(true),
        "empty_right" => empty_scenario(false),
        "nulls" => null_scenario(),
        "selective" => selective_scenario(),
        other => {
            return Err(crate::error::JoinError::input(
                "unknown_scenario",
                format!("scenario '{other}' is not registered"),
            ))
        }
    };
    Ok(s)
}

/// Convert a batch to its wire DTO (used by replay specs and the CLI).
#[must_use]
pub fn batch_to_dto(b: &Batch) -> BatchDto {
    BatchDto {
        columns: b
            .columns
            .iter()
            .map(|c| crate::dto::ColumnDto {
                name: c.name.clone(),
                values: c.values.clone(),
            })
            .collect(),
        row_ids: Some(b.row_ids.clone()),
    }
}
