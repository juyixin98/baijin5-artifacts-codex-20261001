//! Wire-level DTOs and JSON conversion for [`crate::exec::cells::Cell`].

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::batch::Field;
use crate::exec::cells::Cell;
use crate::exec::GroupResult;

/// Request envelope for `POST /query`.
#[derive(Debug, Deserialize)]
pub struct QueryRequest {
    /// Client-chosen identifier; becomes the spill directory name.  Optional —
    /// the server generates one. Must match `[A-Za-z0-9_-]{1,64}`.
    pub query_id: Option<String>,
    /// The logical plan (see [`crate::plan::Plan::from_json`]).
    pub plan: Value,
    /// Either inline data or a named local fixture must be supplied.
    #[serde(default)]
    pub schema: Option<Vec<FieldDto>>,
    /// Simple single-batch payload: `{column_name: [values]}`.
    #[serde(default)]
    pub columns: Option<serde_json::Map<String, Value>>,
    /// Multi-batch payload for external-sort testing.
    #[serde(default)]
    pub batches: Option<Vec<serde_json::Map<String, Value>>>,
    /// Name of a built-in synthetic fixture (see [`crate::fixtures`]).
    #[serde(default)]
    pub fixture: Option<String>,
    /// Deterministic diagnostic hook: automatically cancel the query during
    /// the merge after this many merge cancellation points have passed (each
    /// point is `cancel_check_rows` merged records apart). Lets clients observe
    /// the accepted→undetermined→resumed transition without a timing race.
    /// Ignored on resume requests.
    #[serde(default)]
    pub cancel_after_merge_checks: Option<u64>,
}

/// Schema entry on the wire.
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct FieldDto {
    pub name: String,
    pub data_type: String,
}

impl FieldDto {
    pub fn to_field(&self) -> crate::error::Result<Field> {
        let dt = crate::batch::DataType::parse(&self.data_type).ok_or_else(|| {
            crate::error::Error::invalid_request(format!(
                "unknown data type '{}' for column '{}' (want int64|float64|utf8)",
                self.data_type, self.name
            ))
        })?;
        Ok(Field::new(&self.name, dt))
    }
}

/// Request for `POST /query/resume`.
#[derive(Debug, Deserialize)]
pub struct ResumeRequest {
    pub query_id: String,
    pub plan: Value,
}

/// Request for `POST /query/cancel`.
#[derive(Debug, Deserialize)]
pub struct CancelRequest {
    pub query_id: String,
}

/// Successful response payload.
#[derive(Debug, Serialize)]
pub struct QueryData {
    pub query_id: String,
    pub groups: Vec<GroupDto>,
    pub stats: StatsDto,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub resume_token: Option<ResumeTokenDto>,
}

#[derive(Debug, Serialize)]
pub struct ResumeTokenDto {
    pub query_id: String,
    pub plan_hash: String,
}

#[derive(Debug, Serialize)]
pub struct StatsDto {
    pub ingested_rows: u64,
    pub spill_runs: usize,
    pub peak_memory_bytes: usize,
    pub groups_emitted: usize,
}

#[derive(Debug, Serialize)]
pub struct GroupDto {
    pub group: Value,
    pub aggregations: Value,
}

/// Error envelope.  Always carries the request id and the machine-readable
/// error kind so clients branch on categories, not prose.
#[derive(Debug, Serialize)]
pub struct ErrorBody {
    pub status: &'static str,
    pub request_id: String,
    pub query_id: Value,
    pub error_kind: String,
    pub message: String,
    pub decision: Value,
}

/// Convert a [`Cell`] to JSON.  `Null` maps to JSON null (distinct from the
/// string `"null"`); numbers keep their physical type.
pub fn cell_to_json(cell: &Cell) -> Value {
    match cell {
        Cell::Null => Value::Null,
        Cell::I64(v) => Value::from(*v),
        Cell::F64(v) => Value::from(*v),
        Cell::Str(v) => Value::from(v.clone()),
    }
}

/// Serialize grouped results using the plan's group-by and alias names.
pub fn groups_to_json(
    groups: &[GroupResult],
    group_names: &[String],
    aliases: &[String],
) -> Vec<GroupDto> {
    groups
        .iter()
        .map(|g| {
            let group = if group_names.is_empty() {
                serde_json::json!({})
            } else {
                let mut map = serde_json::Map::new();
                for (name, cell) in group_names.iter().zip(g.key.iter()) {
                    map.insert(name.clone(), cell_to_json(cell));
                }
                Value::Object(map)
            };
            let mut aggs = serde_json::Map::new();
            for (alias, value) in aliases.iter().zip(g.values.iter()) {
                aggs.insert(
                    alias.clone(),
                    value.as_ref().map(cell_to_json).unwrap_or(Value::Null),
                );
            }
            GroupDto {
                group,
                aggregations: Value::Object(aggs),
            }
        })
        .collect()
}
