//! Set operators: UNION / INTERSECT / EXCEPT, DISTINCT & ALL.
//!
//! Execution model (see `exec.rs`):
//! rows are canonicalised to [`RowKey`]s, hash-partitioned recursively, and
//! aggregated into multiplicity tables. Partitions stay resident while they
//! fit the byte budget; on pressure a partition is *spilled* to a checksummed
//! frame and re-partitioned at the next level with an independent hash. Equal
//! rows are always compared byte-wise (never by hash alone), so hash-bucket
//! collisions can never merge distinct rows.
//!
//! Multiset semantics:
//! | op          | DISTINCT              | ALL                        |
//! |-------------|-----------------------|----------------------------|
//! | UNION       | present in L or R → 1 | L + R                      |
//! | INTERSECT   | present in both → 1   | min(L, R)                  |
//! | EXCEPT      | in L, not in R → 1    | L − R (floored at 0)       |

mod counts;
mod exec;

pub use exec::{SetOpOutput, SetOpStats, execute};

use serde::{Deserialize, Serialize};

use crate::batch::TypedBatch;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SetOp {
    Union,
    Intersect,
    Except,
}

impl SetOp {
    pub fn parse(s: &str) -> Result<Self, String> {
        match s.trim().to_ascii_uppercase().as_str() {
            "UNION" => Ok(Self::Union),
            "INTERSECT" => Ok(Self::Intersect),
            "EXCEPT" | "MINUS" => Ok(Self::Except),
            other => Err(format!("unknown set operator '{other}'")),
        }
    }
    pub fn sql(self) -> &'static str {
        match self {
            Self::Union => "UNION",
            Self::Intersect => "INTERSECT",
            Self::Except => "EXCEPT",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Qualifier {
    Distinct,
    All,
}

impl Qualifier {
    pub fn sql(self) -> &'static str {
        match self {
            Self::Distinct => "DISTINCT",
            Self::All => "ALL",
        }
    }
}

/// How the resource policy is applied:
/// * [`InMemory`](ExecutionMode::InMemory): spilling forbidden; budget breach
///   fails with `resource_exhausted/memory_budget`.
/// * [`Auto`](ExecutionMode::Auto): resident while possible, spill on pressure.
/// * [`External`](ExecutionMode::External): level-0 partitions are spilled
///   unconditionally (used to verify the external-memory code path).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ExecutionMode {
    InMemory,
    Auto,
    External,
}

pub struct Query {
    pub op: SetOp,
    pub qualifier: Qualifier,
    pub left: Vec<TypedBatch>,
    pub right: Vec<TypedBatch>,
}

impl Query {
    pub fn new(
        op: SetOp,
        qualifier: Qualifier,
        left: Vec<TypedBatch>,
        right: Vec<TypedBatch>,
    ) -> Self {
        Self {
            op,
            qualifier,
            left,
            right,
        }
    }
}
