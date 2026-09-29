//! HTTP JSON data contract. Typed columns cross the wire as
//! `{ "name", "type", "values" }` where each value is null, an integer,
//! a number or a string. These DTOs are the only serde-facing shapes; the
//! operator core never sees JSON.

use serde::{Deserialize, Serialize};

use crate::error::{ErrorCode, JoinError, JoinResult};
use crate::operator::{Comparator, JoinPlan, Predicate};
use crate::resource::Budget;
use crate::types::builder;
use crate::types::value::KeyType;
use crate::types::{Column, TypedBatch};

#[derive(Debug, Deserialize, Clone)]
pub struct ColumnDto {
    pub name: String,
    #[serde(rename = "type")]
    pub key_type: String,
    /// Untyped JSON values; converted per declared `type`.
    pub values: Vec<serde_json::Value>,
}

#[derive(Debug, Deserialize, Clone)]
pub struct BatchDto {
    pub columns: Vec<ColumnDto>,
}

#[derive(Debug, Deserialize, Clone)]
pub struct PredicateDto {
    pub left_col: usize,
    pub right_col: usize,
    pub op: Comparator,
}

#[derive(Debug, Deserialize, Clone)]
pub struct PlanDto {
    pub p1: PredicateDto,
    pub p2: PredicateDto,
}

#[derive(Debug, Deserialize, Clone, Copy, Default)]
pub struct BudgetDto {
    pub max_output: Option<usize>,
    pub max_candidate_accesses: Option<u64>,
}

/// Full one-shot request.
#[derive(Debug, Deserialize)]
pub struct JoinRequest {
    pub plan: PlanDto,
    pub left: BatchDto,
    pub right: BatchDto,
    #[serde(default)]
    pub budget: BudgetDto,
}

/// Session creation request (same body shape; paging is implicit).
#[derive(Debug, Deserialize)]
pub struct SessionCreateRequest {
    pub plan: PlanDto,
    pub left: BatchDto,
    pub right: BatchDto,
    #[serde(default)]
    pub budget: BudgetDto,
}

#[derive(Debug, Deserialize)]
pub struct ContinueRequest {
    pub cursor: CursorDto,
}

#[derive(Debug, Deserialize, Serialize, Clone)]
pub struct CursorDto {
    pub session_id: String,
    pub page_index: usize,
    pub checkpoint: CheckpointDto,
    pub truncation: String,
}

#[derive(Debug, Deserialize, Serialize, Clone, Copy)]
pub struct CheckpointDto {
    pub dpos: usize,
    pub act_pos: usize,
    pub probe_pos: usize,
    pub probe_hi: usize,
}

impl From<crate::operator::Checkpoint> for CheckpointDto {
    fn from(c: crate::operator::Checkpoint) -> Self {
        let (dpos, act_pos, probe_pos, probe_hi) = c.parts();
        Self {
            dpos,
            act_pos,
            probe_pos,
            probe_hi,
        }
    }
}

// ---------------------------------------------------------------------------
// Decoding / conversion
// ---------------------------------------------------------------------------

pub fn decode_plan(dto: PlanDto) -> JoinPlan {
    JoinPlan::new(
        Predicate {
            left_col: dto.p1.left_col,
            right_col: dto.p1.right_col,
            op: dto.p1.op,
        },
        Predicate {
            left_col: dto.p2.left_col,
            right_col: dto.p2.right_col,
            op: dto.p2.op,
        },
    )
}

impl From<PlanDto> for JoinPlan {
    fn from(dto: PlanDto) -> Self {
        decode_plan(dto)
    }
}

impl From<BudgetDto> for Budget {
    fn from(dto: BudgetDto) -> Self {
        let mut b = Budget::default();
        if let Some(v) = dto.max_output {
            b.max_output = v;
        }
        if let Some(v) = dto.max_candidate_accesses {
            b.max_candidate_accesses = v;
        }
        b
    }
}

fn json_error(msg: impl Into<String>) -> JoinError {
    JoinError::input(ErrorCode::InvalidPayload, msg)
}

pub fn decode_batch(dto: BatchDto) -> JoinResult<TypedBatch> {
    let mut columns: Vec<Column> = Vec::with_capacity(dto.columns.len());
    for c in dto.columns {
        let key_type = KeyType::parse(&c.key_type)
            .ok_or_else(|| json_error(format!("unknown column type '{}'", c.key_type)))?;
        let column = match key_type {
            KeyType::Int64 => {
                let mut vals: Vec<Option<i64>> = Vec::with_capacity(c.values.len());
                for v in &c.values {
                    vals.push(match v {
                        serde_json::Value::Null => None,
                        serde_json::Value::Number(n) => Some(
                            n.as_i64()
                                .ok_or_else(|| json_error("value is not a 64-bit integer"))?,
                        ),
                        other => {
                            return Err(json_error(format!("expected int or null, got {other}")))
                        }
                    });
                }
                builder::int_column(c.name, vals)
            }
            KeyType::Float64 => {
                let mut vals: Vec<Option<f64>> = Vec::with_capacity(c.values.len());
                for v in &c.values {
                    vals.push(match v {
                        serde_json::Value::Null => None,
                        serde_json::Value::Number(n) => Some(
                            n.as_f64()
                                .ok_or_else(|| json_error("value is not a finite number"))?,
                        ),
                        other => {
                            return Err(json_error(format!("expected number or null, got {other}")))
                        }
                    });
                }
                builder::float_column(c.name, vals)
            }
            KeyType::Utf8 => {
                let mut vals: Vec<Option<String>> = Vec::with_capacity(c.values.len());
                for v in &c.values {
                    vals.push(match v {
                        serde_json::Value::Null => None,
                        serde_json::Value::String(s) => Some(s.clone()),
                        other => {
                            return Err(json_error(format!("expected string or null, got {other}")))
                        }
                    });
                }
                builder::utf8_column(c.name, vals.iter().map(|s| s.as_deref()).collect())
            }
        };
        columns.push(column);
    }
    TypedBatch::try_new(columns)
}

// ---------------------------------------------------------------------------
// Response DTOs
// ---------------------------------------------------------------------------

#[derive(Debug, Serialize)]
pub struct PairDto {
    pub left_row: u64,
    pub right_row: u64,
}

#[derive(Debug, Serialize)]
pub struct StatsDto {
    pub emitted: usize,
    pub candidate_accesses: u64,
    pub rows_driven: usize,
}

#[derive(Debug, Serialize)]
pub struct JoinResponse {
    pub run_id: String,
    pub pairs: Vec<PairDto>,
    pub stats: StatsDto,
    pub truncation: String,
    pub finished: bool,
}

#[derive(Debug, Serialize)]
pub struct SessionResponse {
    pub run_id: String,
    pub session_id: String,
    pub page_index: usize,
    pub pairs: Vec<PairDto>,
    pub stats: StatsDto,
    pub truncation: String,
    pub finished: bool,
    /// Present while unfinished; absent on the final page.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub next_cursor: Option<CursorDto>,
}

#[derive(Debug, Serialize)]
pub struct ErrorBody {
    pub code: String,
    pub category: String,
    pub message: String,
}

#[derive(Debug, Serialize)]
pub struct ErrorEnvelope {
    pub error: ErrorBody,
}
