//! The declarative query shape consumed by the ordering operator.
//!
//! Everything here is plain serializable data so it can arrive over HTTP or be
//! embedded in fixtures. The semantics implemented by the operator:
//!
//! * [`OrderKey`]s are compared lexicographically in list order.
//! * Each key has an independent ascending/descending direction and an
//!   independent NULL placement (`NULLS FIRST` / `NULLS LAST`).
//! * A stable *identity* breaks every remaining tie so output order is fully
//!   deterministic. The identity is the row's original (source, index)
//!   position and is **never** part of the user-visible sort key.
//! * `WITH TIES` expands the retained set by equality on the **user sort key
//!   only**. Rows whose sort key equals the last retained row's key are kept
//!   even if that pushes the result past `LIMIT`; ties among themselves are
//!   emitted in stable identity order.

use serde::{Deserialize, Serialize};

/// Sort direction for one key.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Direction {
    Asc,
    Desc,
}

impl Default for Direction {
    fn default() -> Self {
        Direction::Asc
    }
}

/// Where NULLs sort for one key. SQL default is `FIRST` under `ASC` and `LAST`
/// under `DESC`, but the API requires the client to be explicit (validated in
/// `sl-validate`) or it accepts this field directly when present.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum NullOrder {
    First,
    Last,
}

impl Default for NullOrder {
    fn default() -> Self {
        // SQL: ASC NULLS LAST is common in engines; we pick FIRST (the SQL
        // standard default for ASC) and require explicitness at the edge.
        NullOrder::First
    }
}

/// One ORDER BY entry: a column plus direction and NULL placement.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct OrderKey {
    pub column: String,
    #[serde(default)]
    pub direction: Direction,
    #[serde(default)]
    pub nulls: NullOrder,
}

impl OrderKey {
    pub fn asc(column: impl Into<String>) -> Self {
        Self {
            column: column.into(),
            direction: Direction::Asc,
            nulls: NullOrder::First,
        }
    }

    pub fn desc(column: impl Into<String>) -> Self {
        Self {
            column: column.into(),
            direction: Direction::Desc,
            nulls: NullOrder::Last,
        }
    }

    pub fn nulls_first(mut self) -> Self {
        self.nulls = NullOrder::First;
        self
    }

    pub fn nulls_last(mut self) -> Self {
        self.nulls = NullOrder::Last;
        self
    }
}

/// How to behave when the configured in-memory state budget is too small to
/// hold the working set of a run.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum OverflowPolicy {
    /// Refuse the request with a categorized `budget_exceeded` error.
    #[default]
    Reject,
    /// Spill runs to local Arrow IPC files and do an external merge/select.
    ExternalSelect,
}

/// The complete ordering query.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct OrderQuery {
    /// ORDER BY keys in priority order. Must be non-empty after validation.
    pub order_by: Vec<OrderKey>,
    /// Maximum rows returned *before* ties expansion. `None` means no cap.
    #[serde(default)]
    pub limit: Option<u64>,
    /// Rows skipped after ordering, before applying the limit.
    #[serde(default)]
    pub offset: u64,
    /// When true, keep every row whose user sort key equals the last retained
    /// row's key.
    #[serde(default)]
    pub with_ties: bool,
}

impl Default for OrderQuery {
    fn default() -> Self {
        Self {
            order_by: Vec::new(),
            limit: None,
            offset: 0,
            with_ties: false,
        }
    }
}

impl OrderQuery {
    pub fn new(order_by: Vec<OrderKey>) -> Self {
        Self {
            order_by,
            ..Default::default()
        }
    }

    pub fn limit(mut self, n: u64) -> Self {
        self.limit = Some(n);
        self
    }

    pub fn offset(mut self, n: u64) -> Self {
        self.offset = n;
        self
    }

    pub fn with_ties(mut self, on: bool) -> Self {
        self.with_ties = on;
        self
    }

    /// Number of rows that must survive the selection before the offset cut,
    /// i.e. `OFFSET + LIMIT`. Returns `None` when there is no limit (the whole
    /// suffix matters). Overflow is reported as an [`crate::error::SlError`]
    /// in the `overflow` category rather than silently saturating.
    pub fn post_offset_keep(
        &self,
    ) -> std::result::Result<Option<u64>, crate::error::SlError> {
        match self.limit {
            None => Ok(None),
            Some(lim) => {
                let total = self.offset.checked_add(lim).ok_or_else(|| {
                    crate::error::SlError::new(
                        crate::error::ErrorCategory::Overflow,
                        "offset_limit_overflow",
                        format!(
                            "OFFSET ({}) + LIMIT ({}) overflows u64::MAX",
                            self.offset, lim
                        ),
                        "sl_types::query",
                    )
                })?;
                Ok(Some(total))
            }
        }
    }
}
