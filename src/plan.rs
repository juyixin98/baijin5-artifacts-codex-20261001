//! Plan description, validation entry, and operator-tree construction.
//!
//! A [`Plan`] is the external (JSON) description of a query. `build_plan` is
//! the **validation entry**: every plan is checked before any operator runs —
//! unknown tables, missing or wrongly-typed key columns, and output-name
//! collisions are rejected as `Input` errors. Schemas are computed
//! bottom-up, so downstream operators always see validated input schemas.

use std::sync::Arc;
use std::time::Duration;

use arrow2::datatypes::{DataType, Schema};
use serde::Deserialize;

use crate::error::QueryError;
use crate::exec::ExecutionContext;
use crate::fixtures::table_schema;
use crate::operator::join::{HashJoinOperator, JoinConfig};
use crate::operator::scan::{ScanConfig, ScanOperator};
use crate::operator::sort::{SortConfig, SortOperator};
use crate::operator::Operator;

const DEFAULT_BATCHES: usize = 4;
const DEFAULT_BATCH_ROWS: usize = 256;
const DEFAULT_SEED: u64 = 42;
const DEFAULT_RUN_ROWS: usize = 1024;
const DEFAULT_MAX_SPILL_BYTES: usize = 64 * 1024 * 1024;

#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "op", rename_all = "lowercase")]
pub enum Plan {
    Scan {
        table: String,
        batches: Option<usize>,
        batch_rows: Option<usize>,
        seed: Option<u64>,
        /// Test/demo hook: block this long before each batch.
        delay_ms_per_batch: Option<u64>,
        /// Test hook: fail with a Compute error at this batch index.
        fail_at_batch: Option<usize>,
    },
    Sort {
        key: String,
        input: Box<Plan>,
        run_rows: Option<usize>,
        max_spill_bytes: Option<usize>,
    },
    Join {
        build_key: String,
        probe_key: String,
        build: Box<Plan>,
        probe: Box<Plan>,
    },
}

pub struct BuiltPlan {
    pub root: Box<dyn Operator>,
    pub schema: Schema,
}

/// Validate `plan` and build the operator tree. All `Input` errors
/// originate here or in fixture schema lookup — never mid-execution.
pub fn build_plan(plan: &Plan, ctx: &Arc<ExecutionContext>) -> Result<BuiltPlan, QueryError> {
    match plan {
        Plan::Scan {
            table,
            batches,
            batch_rows,
            seed,
            delay_ms_per_batch,
            fail_at_batch,
        } => {
            let schema = table_schema(table)?; // unknown table -> Input
            let operator = ScanOperator::new(
                ScanConfig {
                    table: table.clone(),
                    batches: batches.unwrap_or(DEFAULT_BATCHES),
                    batch_rows: batch_rows.unwrap_or(DEFAULT_BATCH_ROWS),
                    seed: seed.unwrap_or(DEFAULT_SEED),
                    delay_per_batch: Duration::from_millis(delay_ms_per_batch.unwrap_or(0)),
                    fail_at_batch: *fail_at_batch,
                },
                Arc::clone(ctx),
            )?;
            Ok(BuiltPlan { root: Box::new(operator), schema })
        }
        Plan::Sort { key, input, run_rows, max_spill_bytes } => {
            let built = build_plan(input, ctx)?;
            let key_col = resolve_key_column(&built.schema, key, "sort")?;
            let operator = SortOperator::new(
                built.root,
                SortConfig {
                    key_col,
                    run_rows: run_rows.unwrap_or(DEFAULT_RUN_ROWS).max(1),
                    max_spill_bytes: max_spill_bytes.unwrap_or(DEFAULT_MAX_SPILL_BYTES),
                },
                Arc::clone(ctx),
            );
            Ok(BuiltPlan { root: Box::new(operator), schema: built.schema })
        }
        Plan::Join { build_key, probe_key, build, probe } => {
            let build_side = build_plan(build, ctx)?;
            let probe_side = build_plan(probe, ctx)?;
            let key_build_col = resolve_key_column(&build_side.schema, build_key, "join build side")?;
            let key_probe_col = resolve_key_column(&probe_side.schema, probe_key, "join probe side")?;
            let build_keep_cols: Vec<usize> = (0..build_side.schema.fields.len())
                .filter(|&i| i != key_build_col)
                .collect();
            let out_schema = join_output_schema(&probe_side.schema, &build_side.schema, &build_keep_cols)?;
            let operator = HashJoinOperator::new(
                build_side.root,
                probe_side.root,
                JoinConfig {
                    key_build_col,
                    key_probe_col,
                    build_keep_cols,
                    out_batch_rows: 1024,
                },
                out_schema.clone(),
                Arc::clone(ctx),
            );
            Ok(BuiltPlan { root: Box::new(operator), schema: out_schema })
        }
    }
}

fn resolve_key_column(schema: &Schema, key: &str, context: &str) -> Result<usize, QueryError> {
    let (idx, field) = schema
        .fields
        .iter()
        .enumerate()
        .find(|(_, f)| f.name == key)
        .ok_or_else(|| {
            let available: Vec<&str> = schema.fields.iter().map(|f| f.name.as_str()).collect();
            QueryError::input(format!(
                "{context}: key column '{key}' not found; available columns: {available:?}"
            ))
        })?;
    if field.data_type != DataType::Int64 {
        return Err(QueryError::input(format!(
            "{context}: key column '{key}' must be Int64, found {:?}",
            field.data_type
        )));
    }
    Ok(idx)
}

fn join_output_schema(
    probe: &Schema,
    build: &Schema,
    build_keep_cols: &[usize],
) -> Result<Schema, QueryError> {
    let mut fields = probe.fields.clone();
    for &col in build_keep_cols {
        let field = &build.fields[col];
        if fields.iter().any(|f| f.name == field.name) {
            return Err(QueryError::input(format!(
                "join output column name collision: '{}' exists on both sides",
                field.name
            )));
        }
        fields.push(field.clone());
    }
    Ok(Schema::from(fields))
}
