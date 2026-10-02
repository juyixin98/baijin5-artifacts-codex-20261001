//! Hash join operator (inner join, build side fully materialized).
//!
//! Blocking on the build side: the first pull drains the build input into a
//! hash table (`key -> row indices` over one concatenated chunk), accounted
//! against the memory budget — a join that does not fit fails with
//! `ResourceExhausted` (joins do not spill). The probe side then streams:
//! each pull gathers up to `out_batch_rows` matched pairs and emits probe
//! columns followed by the non-key build columns.
//!
//! Null keys never match (inner-join semantics).

use std::collections::HashMap;
use std::sync::Arc;

use async_trait::async_trait;
use arrow2::array::{new_empty_array, Array, Int64Array, UInt32Array};
use arrow2::chunk::Chunk;
use arrow2::compute::concatenate::concatenate;
use arrow2::compute::take::take;
use arrow2::datatypes::Schema;

use crate::batch::TypedBatch;
use crate::error::QueryError;
use crate::exec::ExecutionContext;
use crate::resources::Reservation;

use super::{ensure_pollable, Operator, OperatorState};

/// Rough per-entry hash table overhead (key + index + bucket slack).
const MAP_ENTRY_BYTES: usize = 32;

#[derive(Debug, Clone)]
pub struct JoinConfig {
    pub key_build_col: usize,
    pub key_probe_col: usize,
    /// Build-side columns emitted in the output (all except the key).
    pub build_keep_cols: Vec<usize>,
    pub out_batch_rows: usize,
}

enum Phase {
    Build,
    Probe,
    Done,
}

pub struct HashJoinOperator {
    name: String,
    state: OperatorState,
    ctx: Arc<ExecutionContext>,
    build: Box<dyn Operator>,
    probe: Box<dyn Operator>,
    config: JoinConfig,
    out_schema: Schema,
    phase: Phase,
    table: Option<HashMap<i64, Vec<u32>>>,
    build_chunk: Option<Chunk<Box<dyn Array>>>,
    reservation: Option<Reservation>,
    probe_current: Option<(TypedBatch, usize)>,
}

impl HashJoinOperator {
    pub fn new(
        build: Box<dyn Operator>,
        probe: Box<dyn Operator>,
        config: JoinConfig,
        out_schema: Schema,
        ctx: Arc<ExecutionContext>,
    ) -> Self {
        Self {
            name: "hash_join".to_string(),
            state: OperatorState::Open,
            ctx,
            build,
            probe,
            config,
            out_schema,
            phase: Phase::Build,
            table: None,
            build_chunk: None,
            reservation: None,
            probe_current: None,
        }
    }

    async fn poll_inner(&mut self) -> Result<Option<TypedBatch>, QueryError> {
        if matches!(self.phase, Phase::Build) {
            self.consume_build_side().await?;
            self.phase = Phase::Probe;
        }
        if matches!(self.phase, Phase::Probe) {
            let batch = self.probe_next().await?;
            if batch.is_none() {
                self.phase = Phase::Done;
            }
            return Ok(batch);
        }
        Ok(None)
    }

    /// Drain the build input into the hash table. Blocking and cancellable.
    async fn consume_build_side(&mut self) -> Result<(), QueryError> {
        let mut batches = Vec::new();
        loop {
            self.ctx.cancel.check()?;
            match self.build.next_batch().await? {
                None => break,
                Some(batch) => batches.push(batch),
            }
        }
        let schema = Arc::new(self.build.schema().clone());
        let chunk = concat_side(&schema, &batches)?;
        let rows = chunk.len();
        let data_bytes: usize = batches.iter().map(|b| b.estimated_bytes()).sum();
        let reservation = self
            .ctx
            .resources
            .reserve(data_bytes + rows * MAP_ENTRY_BYTES)?;
        let keys = chunk.arrays()[self.config.key_build_col]
            .as_any()
            .downcast_ref::<Int64Array>()
            .ok_or_else(|| QueryError::compute("join build key is not Int64"))?;
        let mut table: HashMap<i64, Vec<u32>> = HashMap::with_capacity(rows);
        for row in 0..rows {
            if keys.is_null(row) {
                continue; // null keys never match
            }
            table.entry(keys.value(row)).or_default().push(row as u32);
        }
        self.table = Some(table);
        self.build_chunk = Some(chunk);
        self.reservation = Some(reservation);
        Ok(())
    }

    async fn probe_next(&mut self) -> Result<Option<TypedBatch>, QueryError> {
        let out_rows = self.config.out_batch_rows.max(1);
        loop {
            self.ctx.cancel.check()?;
            if self.probe_current.as_ref().is_none_or(|(b, pos)| *pos >= b.num_rows()) {
                match self.probe.next_batch().await? {
                    None => return Ok(None),
                    Some(batch) => self.probe_current = Some((batch, 0)),
                }
            }
            let (batch, pos) = self.probe_current.as_ref().expect("refilled above");
            let keys = batch.int64_column(self.config.key_probe_col)?;
            let mut probe_indices: Vec<u32> = Vec::new();
            let mut build_indices: Vec<u32> = Vec::new();
            let mut next_pos = *pos;
            while next_pos < batch.num_rows() && probe_indices.len() < out_rows {
                if keys.is_valid(next_pos) {
                    if let Some(rows) = self
                        .table
                        .as_ref()
                        .expect("build phase completed")
                        .get(&keys.value(next_pos))
                    {
                        probe_indices.extend(std::iter::repeat_n(next_pos as u32, rows.len()));
                        build_indices.extend_from_slice(rows);
                    }
                }
                next_pos += 1;
            }
            self.probe_current = Some((batch.clone(), next_pos));
            if probe_indices.is_empty() {
                continue; // no matches in this probe batch; pull the next one
            }
            return self.emit(&probe_indices, &build_indices).map(Some);
        }
    }

    fn emit(&self, probe_idx: &[u32], build_idx: &[u32]) -> Result<TypedBatch, QueryError> {
        let (probe_batch, _) = self.probe_current.as_ref().expect("probe batch present");
        let probe_take = UInt32Array::from_vec(probe_idx.to_vec());
        let build_take = UInt32Array::from_vec(build_idx.to_vec());
        let mut columns: Vec<Box<dyn Array>> = Vec::with_capacity(self.out_schema.fields.len());
        for array in probe_batch.chunk().arrays() {
            columns.push(take(array.as_ref(), &probe_take)?);
        }
        let build_chunk = self.build_chunk.as_ref().expect("build phase completed");
        for &col in &self.config.build_keep_cols {
            columns.push(take(build_chunk.arrays()[col].as_ref(), &build_take)?);
        }
        TypedBatch::new(Arc::new(self.out_schema.clone()), Chunk::new(columns))
    }
}

#[async_trait]
impl Operator for HashJoinOperator {
    fn name(&self) -> &str {
        &self.name
    }

    fn schema(&self) -> &Schema {
        &self.out_schema
    }

    fn state(&self) -> OperatorState {
        self.state
    }

    async fn next_batch(&mut self) -> Result<Option<TypedBatch>, QueryError> {
        ensure_pollable(self.state, &self.name)?;
        let result = self.poll_inner().await;
        if result.is_err() {
            self.state = OperatorState::Errored;
        }
        result
    }

    async fn close(&mut self) -> Result<(), QueryError> {
        if self.state == OperatorState::Closed {
            return Ok(()); // idempotent
        }
        self.state = OperatorState::Closed;
        self.table = None;
        self.build_chunk = None;
        self.reservation = None;
        self.probe_current = None;
        // Close both children even if the first close fails.
        let build_result = self.build.close().await;
        let probe_result = self.probe.close().await;
        build_result.and(probe_result)
    }
}

/// Concatenate one side's batches into a single chunk (empty side yields
/// zero-length columns of the right types).
fn concat_side(
    schema: &Arc<Schema>,
    batches: &[TypedBatch],
) -> Result<Chunk<Box<dyn Array>>, QueryError> {
    if batches.is_empty() {
        let columns = schema
            .fields
            .iter()
            .map(|f| new_empty_array(f.data_type.clone()))
            .collect();
        return Ok(Chunk::new(columns));
    }
    if batches.len() == 1 {
        return Ok(batches[0].chunk().clone());
    }
    let mut columns: Vec<Box<dyn Array>> = Vec::with_capacity(schema.fields.len());
    for col in 0..schema.fields.len() {
        let refs: Vec<&dyn Array> = batches
            .iter()
            .map(|b| b.chunk().arrays()[col].as_ref())
            .collect();
        columns.push(concatenate(&refs)?);
    }
    Ok(Chunk::new(columns))
}
