//! JSON data-transfer objects — the wire contract of the HTTP API and
//! CLI. These are the only serde-facing shapes; the operator works on
//! the native types in [`crate::batch`] / [`crate::operator`].

use serde::{Deserialize, Serialize};

use crate::batch::{Batch, Column};
use crate::error::{JoinError, JoinResult};
use crate::operator::comparator::Comparator;
use crate::operator::plan::{JoinPlan, Predicate};
use crate::resource::Budget;

/// One nullable int64 column on the wire.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct ColumnDto {
    pub name: String,
    /// JSON `null` encodes SQL NULL.
    #[serde(default)]
    pub values: Vec<Option<i64>>,
}

/// A relation: columns plus optional stable per-row identities.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct BatchDto {
    pub columns: Vec<ColumnDto>,
    /// Omit to default to positional ids `"0".."n-1"`. When supplied,
    /// ids must be unique within the side; duplicate *values* are fine.
    #[serde(default)]
    pub row_ids: Option<Vec<String>>,
}

impl BatchDto {
    /// Validate and convert to the native [`Batch`].
    ///
    /// # Errors
    /// `Input` for length mismatches, duplicate names/ids or empty schema.
    pub fn into_batch(self) -> JoinResult<Batch> {
        if self.columns.is_empty() {
            return Err(JoinError::input(
                "empty_schema",
                "batch must contain at least one column",
            ));
        }
        let mut names = std::collections::HashSet::new();
        for c in &self.columns {
            if !names.insert(c.name.as_str()) {
                return Err(JoinError::input(
                    "duplicate_column_name",
                    format!("duplicate column '{}' in batch", c.name),
                ));
            }
        }
        let cols = self
            .columns
            .into_iter()
            .map(|c| Column::new(c.name, c.values))
            .collect();
        let mut batch = Batch::new(cols)?;
        if let Some(ids) = self.row_ids {
            if ids.len() != batch.row_count() {
                return Err(JoinError::input(
                    "row_id_count_mismatch",
                    format!("{} rows but {} row ids", batch.row_count(), ids.len()),
                ));
            }
            batch = batch.with_row_ids(ids);
            batch.validate_unique_row_ids()?;
        }
        Ok(batch)
    }
}

/// Wire form of one predicate (`op` is the textual comparator).
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct PredicateDto {
    pub left_column: String,
    pub op: String,
    pub right_column: String,
}

impl PredicateDto {
    /// # Errors
    /// `Input` for an unknown comparator symbol.
    pub fn into_predicate(self) -> JoinResult<Predicate> {
        Ok(Predicate::new(
            self.left_column,
            Comparator::parse(&self.op)?,
            self.right_column,
        ))
    }
}

/// One-shot join request.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct JoinRequest {
    pub left: BatchDto,
    pub right: BatchDto,
    pub predicates: Vec<PredicateDto>,
    #[serde(default)]
    pub budget: Option<Budget>,
    /// When true, the response embeds key intermediate trace events.
    #[serde(default)]
    pub include_trace: bool,
}

impl JoinRequest {
    /// Parse into native, fully validated parts.
    ///
    /// # Errors
    /// `Input` for any malformed field or wrong predicate count.
    pub fn parse(self) -> JoinResult<ParsedJoin> {
        if self.predicates.len() != 2 {
            return Err(JoinError::input(
                "predicate_count",
                format!(
                    "IEJoin requires exactly 2 range predicates, got {}",
                    self.predicates.len()
                ),
            ));
        }
        let mut preds = self.predicates.into_iter();
        let p1 = preds.next().expect("checked len").into_predicate()?;
        let p2 = preds.next().expect("checked len").into_predicate()?;
        let plan = JoinPlan::new(p1, p2);
        plan.validate()?;
        let left = self.left.into_batch()?;
        let right = self.right.into_batch()?;
        // Note: column-reference binding (does each predicate column
        // exist on its side?) is deferred to the engine's prepare step,
        // where the run id and trace context exist. `parse` validates
        // shape and syntax only.
        let budget = self.budget.unwrap_or_default();
        budget.check_input(left.row_count(), right.row_count())?;
        Ok(ParsedJoin {
            plan,
            left,
            right,
            budget,
            include_trace: self.include_trace,
        })
    }
}

/// Validated native request.
#[derive(Debug)]
pub struct ParsedJoin {
    pub plan: JoinPlan,
    pub left: Batch,
    pub right: Batch,
    pub budget: Budget,
    pub include_trace: bool,
}

/// Output pair on the wire, using caller-supplied identities.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct PairDto {
    pub left_id: String,
    pub right_id: String,
}

/// Work counters on the wire.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct CountersDto {
    pub right_rows_scanned: u64,
    pub gate1_steps: u64,
    pub candidate_accesses: u64,
    pub pairs_emitted: u64,
}

impl From<crate::operator::Counters> for CountersDto {
    fn from(c: crate::operator::Counters) -> Self {
        Self {
            right_rows_scanned: c.right_rows_scanned,
            gate1_steps: c.gate1_steps,
            candidate_accesses: c.candidate_accesses,
            pairs_emitted: c.pairs_emitted,
        }
    }
}

/// One-shot response.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct JoinResponse {
    pub run_id: String,
    pub pairs: Vec<PairDto>,
    pub count: usize,
    pub truncated: bool,
    pub counters: CountersDto,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub events: Vec<crate::trace::TraceEvent>,
}

/// Open a server-side cursor and (optionally) return the first page.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct CursorOpenRequest {
    #[serde(flatten)]
    pub join: JoinRequest,
    /// Pairs returned per `next_page` call. Defaults to the budget cap.
    #[serde(default)]
    pub page_size: Option<u64>,
}

/// Request the next page of an open cursor.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct CursorPageRequest {
    pub cursor_id: String,
    #[serde(default)]
    pub page_size: Option<u64>,
}

/// A cursor page.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct CursorPageResponse {
    pub run_id: String,
    pub cursor_id: String,
    pub pairs: Vec<PairDto>,
    pub count: usize,
    /// True on the final page; the cursor is closed afterwards.
    pub finished: bool,
    pub counters: CountersDto,
}

/// Error envelope. Category maps to HTTP status at the API boundary.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct ErrorResponse {
    pub category: String,
    pub code: String,
    pub message: String,
    pub details: serde_json::Value,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub run_id: Option<String>,
}

impl From<&JoinError> for ErrorResponse {
    fn from(e: &JoinError) -> Self {
        let run_id = e
            .details
            .get("run_id")
            .and_then(|v| v.as_str())
            .map(str::to_owned);
        Self {
            category: e.category.to_string(),
            code: e.code.to_string(),
            message: e.message.clone(),
            details: serde_json::Value::Object(e.details.clone()),
            run_id,
        }
    }
}
