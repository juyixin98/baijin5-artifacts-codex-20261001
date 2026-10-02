//! Blocking sort operator with external spill.
//!
//! Phase 1 (accumulate, blocking): consumes the *entire* input before
//! emitting anything. Input is buffered into runs of `run_rows` rows; each
//! run is sorted in memory, then either kept (if the memory budget grants a
//! reservation) or spilled to an Arrow IPC stream file. If neither memory
//! nor the spill quota can hold the run, the operator fails with
//! `ResourceExhausted`.
//!
//! Phase 2 (merge): k-way merge over run cursors (in-memory chunks or IPC
//! stream readers), emitting sorted batches of `MERGE_BATCH_ROWS` rows.
//!
//! Resource hygiene: in-memory runs hold [`Reservation`] guards, spilled
//! runs hold [`SpillFile`] guards (file deleted on drop). `close()` drops
//! everything deterministically; plain `Drop` also releases everything.

use std::fs::File;
use std::io::BufReader;
use std::sync::Arc;

use async_trait::async_trait;
use arrow2::array::Array;
use arrow2::chunk::Chunk;
use arrow2::compute::concatenate::concatenate;
use arrow2::compute::sort::{sort_to_indices, SortOptions};
use arrow2::compute::take::take;
use arrow2::datatypes::Schema;
use arrow2::io::ipc::read::{read_stream_metadata, StreamReader, StreamState};
use arrow2::io::ipc::write::{StreamWriter, WriteOptions};

use crate::batch::TypedBatch;
use crate::error::QueryError;
use crate::exec::ExecutionContext;
use crate::resources::{Reservation, SpillFile};

use super::{ensure_pollable, Operator, OperatorState};

const MERGE_BATCH_ROWS: usize = 1024;

#[derive(Debug, Clone)]
pub struct SortConfig {
    /// Resolved index of the sort key column (validated at plan time).
    pub key_col: usize,
    pub run_rows: usize,
    pub max_spill_bytes: usize,
}

enum Run {
    Mem(TypedBatch),
    Spilled(SpillFile),
}

enum Phase {
    Accumulating,
    Merging,
    Done,
}

pub struct SortOperator {
    name: String,
    state: OperatorState,
    ctx: Arc<ExecutionContext>,
    input: Box<dyn Operator>,
    config: SortConfig,
    schema: Schema,
    phase: Phase,
    runs: Vec<Run>,
    reservations: Vec<Reservation>,
    buffered: Vec<TypedBatch>,
    buffered_rows: usize,
    merge: Option<MergeState>,
    /// Diagnostics: how many runs went where (asserted in tests).
    pub runs_in_memory: usize,
    pub runs_spilled: usize,
}

impl SortOperator {
    pub fn new(
        input: Box<dyn Operator>,
        config: SortConfig,
        ctx: Arc<ExecutionContext>,
    ) -> Self {
        let schema = input.schema().clone();
        Self {
            name: "sort".to_string(),
            state: OperatorState::Open,
            ctx,
            input,
            config,
            schema,
            phase: Phase::Accumulating,
            runs: Vec::new(),
            reservations: Vec::new(),
            buffered: Vec::new(),
            buffered_rows: 0,
            merge: None,
            runs_in_memory: 0,
            runs_spilled: 0,
        }
    }

    async fn poll_inner(&mut self) -> Result<Option<TypedBatch>, QueryError> {
        if matches!(self.phase, Phase::Accumulating) {
            self.build_runs().await?;
            self.merge = Some(self.build_merge()?);
            self.phase = Phase::Merging;
        }
        if matches!(self.phase, Phase::Merging) {
            self.ctx.cancel.check()?;
            let batch = self.merge_next()?;
            if batch.is_none() {
                self.phase = Phase::Done;
            }
            return Ok(batch);
        }
        Ok(None)
    }

    /// Blocking accumulation: drain the whole input into sorted runs.
    async fn build_runs(&mut self) -> Result<(), QueryError> {
        loop {
            self.ctx.cancel.check()?;
            match self.input.next_batch().await? {
                None => break,
                Some(batch) => {
                    self.buffered_rows += batch.num_rows();
                    self.buffered.push(batch);
                    if self.buffered_rows >= self.config.run_rows {
                        self.flush_run()?;
                    }
                }
            }
        }
        if self.buffered_rows > 0 {
            self.flush_run()?;
        }
        Ok(())
    }

    /// Sort the buffered rows into one run and keep or spill it.
    fn flush_run(&mut self) -> Result<(), QueryError> {
        let buffered = std::mem::take(&mut self.buffered);
        self.buffered_rows = 0;
        let schema = Arc::new(self.schema.clone());
        let chunk = concat_chunks(&schema, &buffered)?;
        let sorted = sort_chunk(schema, chunk, self.config.key_col)?;
        let bytes = sorted.estimated_bytes();
        match self.ctx.resources.reserve(bytes) {
            Ok(reservation) => {
                self.runs.push(Run::Mem(sorted));
                self.reservations.push(reservation);
                self.runs_in_memory += 1;
            }
            Err(_) => {
                let spill = self.spill_run(&sorted)?;
                self.runs.push(Run::Spilled(spill));
                self.runs_spilled += 1;
            }
        }
        Ok(())
    }

    fn spill_run(&self, run: &TypedBatch) -> Result<SpillFile, QueryError> {
        let estimate = run.estimated_bytes();
        let written = self.ctx.resources.snapshot().spill_bytes_written_total;
        if written.saturating_add(estimate) > self.config.max_spill_bytes {
            return Err(QueryError::resource(format!(
                "spill quota exceeded: {written} written + ~{estimate} needed > {} quota",
                self.config.max_spill_bytes
            )));
        }
        let spill = self.ctx.resources.create_spill_file()?;
        let file = File::create(spill.path())?;
        let mut writer = StreamWriter::new(file, WriteOptions::default());
        writer.start(&self.schema, None)?;
        writer.write(run.chunk(), None)?;
        writer.finish()?;
        self.ctx.resources.record_spill_write(estimate);
        Ok(spill)
    }

    fn build_merge(&mut self) -> Result<MergeState, QueryError> {
        let runs = std::mem::take(&mut self.runs);
        let mut cursors = Vec::with_capacity(runs.len());
        for run in runs {
            cursors.push(RunCursor::open(run, &self.schema)?);
        }
        Ok(MergeState {
            schema: Arc::new(self.schema.clone()),
            key_col: self.config.key_col,
            cursors,
        })
    }

    fn merge_next(&mut self) -> Result<Option<TypedBatch>, QueryError> {
        let merge = self.merge.as_mut().expect("merge state in merging phase");
        // Segments reference chunks in `bag` by index, so a cursor may
        // advance to its next chunk in the middle of one output batch
        // without invalidating segments recorded earlier.
        let mut bag: Vec<Chunk<Box<dyn Array>>> = Vec::new();
        let mut cursor_chunk: Vec<usize> = vec![usize::MAX; merge.cursors.len()];
        let mut segments: Vec<(usize, usize, usize)> = Vec::new(); // (bag idx, start, len)
        let mut out_rows = 0;
        while out_rows < MERGE_BATCH_ROWS {
            // Advance exhausted cursors, then pick the smallest current key.
            let mut best: Option<(i64, usize)> = None;
            for (idx, cursor) in merge.cursors.iter_mut().enumerate() {
                if cursor.advance_if_needed()? {
                    cursor_chunk[idx] = usize::MAX; // current chunk changed
                }
                if cursor.exhausted {
                    continue;
                }
                let key = key_at(cursor.current_chunk(), merge.key_col, cursor.pos)?;
                match best {
                    Some((best_key, _)) if best_key <= key => {}
                    _ => best = Some((key, idx)),
                }
            }
            let Some((_, run_idx)) = best else { break };
            let cursor = &mut merge.cursors[run_idx];
            let pos = cursor.pos;
            if cursor_chunk[run_idx] == usize::MAX {
                bag.push(cursor.current_chunk().clone());
                cursor_chunk[run_idx] = bag.len() - 1;
            }
            let chunk_idx = cursor_chunk[run_idx];
            match segments.last_mut() {
                Some((seg_chunk, start, len)) if *seg_chunk == chunk_idx && *start + *len == pos => {
                    *len += 1
                }
                _ => segments.push((chunk_idx, pos, 1)),
            }
            cursor.pos += 1;
            out_rows += 1;
        }
        if out_rows == 0 {
            return Ok(None);
        }
        let mut columns: Vec<Box<dyn Array>> = Vec::with_capacity(merge.schema.fields.len());
        for col in 0..merge.schema.fields.len() {
            let parts: Vec<Box<dyn Array>> = segments
                .iter()
                .map(|(chunk_idx, start, len)| bag[*chunk_idx].arrays()[col].sliced(*start, *len))
                .collect();
            let refs: Vec<&dyn Array> = parts.iter().map(|p| p.as_ref()).collect();
            columns.push(concatenate(&refs)?);
        }
        TypedBatch::new(merge.schema.clone(), Chunk::new(columns)).map(Some)
    }
}

#[async_trait]
impl Operator for SortOperator {
    fn name(&self) -> &str {
        &self.name
    }

    fn schema(&self) -> &Schema {
        &self.schema
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
            return Ok(()); // idempotent: second close is a no-op
        }
        self.state = OperatorState::Closed;
        // Drop guards release memory, delete spill files. Order is
        // irrelevant: each guard releases exactly once.
        self.buffered.clear();
        self.runs.clear();
        self.reservations.clear();
        self.merge = None;
        self.input.close().await
    }
}

// ---- run internals ---------------------------------------------------------

fn concat_chunks(
    schema: &Arc<Schema>,
    batches: &[TypedBatch],
) -> Result<Chunk<Box<dyn Array>>, QueryError> {
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

fn sort_chunk(
    schema: Arc<Schema>,
    chunk: Chunk<Box<dyn Array>>,
    key_col: usize,
) -> Result<TypedBatch, QueryError> {
    let indices = sort_to_indices::<u32>(
        chunk.arrays()[key_col].as_ref(),
        &SortOptions::default(),
        None,
    )?;
    let mut columns = Vec::with_capacity(chunk.arrays().len());
    for array in chunk.arrays() {
        columns.push(take(array.as_ref(), &indices)?);
    }
    TypedBatch::new(schema, Chunk::new(columns))
}

fn key_at(chunk: &Chunk<Box<dyn Array>>, key_col: usize, row: usize) -> Result<i64, QueryError> {
    let keys = chunk.arrays()[key_col]
        .as_any()
        .downcast_ref::<arrow2::array::PrimitiveArray<i64>>()
        .ok_or_else(|| QueryError::compute("sort key column is not Int64"))?;
    Ok(keys.value(row))
}

// ---- merge internals -------------------------------------------------------

struct MergeState {
    schema: Arc<Schema>,
    key_col: usize,
    cursors: Vec<RunCursor>,
}

struct RunCursor {
    reader: RunReader,
    current: Option<Chunk<Box<dyn Array>>>,
    pos: usize,
    exhausted: bool,
    /// Keeps the spill file alive (and on disk) until the cursor drops.
    _spill_guard: Option<SpillFile>,
}

impl RunCursor {
    fn open(run: Run, schema: &Schema) -> Result<Self, QueryError> {
        let (reader, guard) = match run {
            Run::Mem(batch) => (RunReader::Mem(Some(batch.chunk().clone()).into_iter()), None),
            Run::Spilled(spill) => {
                let mut file = BufReader::new(File::open(spill.path())?);
                let metadata = read_stream_metadata(&mut file)?;
                if metadata.schema.fields.len() != schema.fields.len() {
                    return Err(QueryError::compute(
                        "spilled run schema does not match sort schema",
                    ));
                }
                (
                    RunReader::Ipc(Box::new(StreamReader::new(file, metadata, None))),
                    Some(spill),
                )
            }
        };
        Ok(Self {
            reader,
            current: None,
            pos: 0,
            exhausted: false,
            _spill_guard: guard,
        })
    }

    /// Pull the next chunk when the current one is consumed. Returns `true`
    /// if the cursor's current chunk changed (callers must drop any
    /// references to the previous chunk).
    fn advance_if_needed(&mut self) -> Result<bool, QueryError> {
        let mut changed = false;
        while !self.exhausted && self.current.as_ref().is_none_or(|c| self.pos >= c.len()) {
            match self.reader.next_chunk()? {
                Some(chunk) if !chunk.is_empty() => {
                    self.current = Some(chunk);
                    self.pos = 0;
                    changed = true;
                }
                Some(_) => continue, // skip empty chunks
                None => {
                    self.current = None;
                    self.exhausted = true;
                    changed = true;
                }
            }
        }
        Ok(changed)
    }

    fn current_chunk(&self) -> &Chunk<Box<dyn Array>> {
        self.current.as_ref().expect("cursor advanced before merge step")
    }
}

enum RunReader {
    Mem(std::option::IntoIter<Chunk<Box<dyn Array>>>),
    Ipc(Box<StreamReader<BufReader<File>>>),
}

impl RunReader {
    fn next_chunk(&mut self) -> Result<Option<Chunk<Box<dyn Array>>>, QueryError> {
        match self {
            Self::Mem(iter) => Ok(iter.next()),
            Self::Ipc(reader) => loop {
                match reader.next() {
                    None => return Ok(None),
                    Some(Ok(StreamState::Some(chunk))) => return Ok(Some(chunk)),
                    Some(Ok(StreamState::Waiting)) => continue,
                    Some(Err(e)) => return Err(e.into()),
                }
            },
        }
    }
}
