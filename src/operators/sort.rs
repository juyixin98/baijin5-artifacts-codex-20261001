//! Blocking sort with bounded memory and disk spill.
//!
//! ## Phases
//! 1. **Build** (on the first pull): drain the whole child into a row buffer.
//!    Whenever buffered logical bytes exceed `memory_budget`, the buffer is
//!    sorted and spilled as one sorted run, then freed. Cancellation and the
//!    deadline are checked before every upstream pull and around every spill,
//!    so a blocked source still responds promptly.
//! 2. **Emit**:
//!    * If nothing spilled, the in-memory buffer is sorted and replayed in
//!      fixed-size output batches.
//!    * Otherwise the tail is spilled too and a bounded N-way streaming merge
//!      over [`RunReader`]s produces output — only one input frame per run is
//!      materialized at once, independent of total input size.
//!
//! ## Resources
//! Run files live in a per-operator temp directory. [`release`] drops every run
//! handle (removing files at most once), removes the directory, shuts the child
//! down and zeroes the memory account. Double close is prevented by
//! [`OperatorCore`].

use std::path::{Path, PathBuf};
use std::sync::Arc;

use crate::batch::{Batch, Scalar, Schema};
use crate::cancel::Control;
use crate::error::{QueryError, QueryResult};
use crate::operator::{Operator, OperatorCore};
use crate::operators::projection::Unary;
use crate::operators::{batch_rows, compare_keys, rows_to_batch};
use crate::resource::{row_size, ResourceTracker, RunFile, RunReader, SpillWriter};

/// Rows per emitted output batch by default.
const DEFAULT_OUT_BATCH: usize = 64;

enum Phase {
    Building,
    Memory {
        rows: Vec<Vec<Scalar>>,
        from: usize,
    },
    Merge {
        cursors: Vec<RunCursor>,
    },
    /// Closed: all frames dropped, merge readers (and their fds) released.
    Closed,
}

/// Streaming cursor over one spilled run. Only the run's current frame is
/// materialized into rows; when the frame is exhausted the next is loaded.
struct RunCursor {
    #[allow(dead_code)]
    run: Arc<RunFile>,
    reader: RunReader,
    rows: Vec<Vec<Scalar>>,
    row: usize,
}

impl RunCursor {
    fn open(run: Arc<RunFile>) -> QueryResult<Self> {
        let reader = RunReader::open(run.clone())?;
        let mut c = Self {
            run,
            reader,
            rows: Vec::new(),
            row: 0,
        };
        c.load_frame()?;
        Ok(c)
    }

    fn load_frame(&mut self) -> QueryResult<bool> {
        match self.reader.next_batch()? {
            Some(b) => {
                self.rows = batch_rows(&b)?;
                self.row = 0;
                Ok(true)
            }
            None => {
                self.rows.clear();
                self.row = 0;
                Ok(false)
            }
        }
    }

    fn head(&self) -> Option<&[Scalar]> {
        self.rows.get(self.row).map(|r| r.as_slice())
    }

    fn pop(&mut self) -> QueryResult<Vec<Scalar>> {
        let row = self
            .rows
            .get(self.row)
            .cloned()
            .ok_or_else(|| QueryError::computation("merge cursor pop past end"))?;
        self.row += 1;
        if self.row >= self.rows.len() {
            self.load_frame()?;
        }
        Ok(row)
    }
}

pub struct Sort {
    base: Unary,
    keys: Vec<usize>,
    schema: Arc<Schema>,
    memory_budget: u64,
    out_batch_rows: usize,
    tracker: Arc<ResourceTracker>,
    spill_dir: PathBuf,
    run_seq: u64,
    runs: Vec<Arc<RunFile>>,
    /// Total runs ever spilled (survives the move of handles into merge
    /// cursors), so callers can observe spill count after emission too.
    runs_count: usize,
    phase: Phase,
}

static DIR_SEQ: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(1);

fn unique_dir(root: &Path) -> PathBuf {
    let seq = DIR_SEQ.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
    root.join(format!("sort-{}-{:06}", std::process::id(), seq))
}

impl Sort {
    pub fn new(
        name: impl Into<String>,
        child: Box<dyn Operator>,
        key_columns: &[String],
        memory_budget: u64,
        tracker: Arc<ResourceTracker>,
        spill_root: PathBuf,
    ) -> QueryResult<Self> {
        let schema = child.schema_out();
        let mut keys = Vec::with_capacity(key_columns.len());
        for k in key_columns {
            let idx = schema.index_of(k).ok_or_else(|| {
                QueryError::invalid_input(format!(
                    "sort references unknown key {k:?}; have {:?}",
                    schema.fields().iter().map(|(n, _)| n).collect::<Vec<_>>()
                ))
            })?;
            keys.push(idx);
        }
        if keys.is_empty() {
            return Err(QueryError::invalid_input("sort requires at least one key"));
        }
        std::fs::create_dir_all(&spill_root).map_err(|e| {
            QueryError::resource_exhausted(format!("cannot create spill root {spill_root:?}: {e}"))
        })?;
        let spill_dir = unique_dir(&spill_root);
        std::fs::create_dir_all(&spill_dir).map_err(|e| {
            QueryError::resource_exhausted(format!("cannot create spill dir {spill_dir:?}: {e}"))
        })?;

        Ok(Self {
            base: Unary::new(name, child),
            keys,
            schema,
            memory_budget,
            out_batch_rows: DEFAULT_OUT_BATCH,
            tracker,
            spill_dir,
            run_seq: 0,
            runs: Vec::new(),
            runs_count: 0,
            phase: Phase::Building,
        })
    }

    pub fn with_out_batch_rows(mut self, n: usize) -> Self {
        self.out_batch_rows = n.max(1);
        self
    }

    /// Adjust the output batch size after construction (runtime/test tuning).
    pub fn set_out_batch_rows(&mut self, n: usize) {
        self.out_batch_rows = n.max(1);
    }

    pub fn runs_spilled(&self) -> usize {
        self.runs_count
    }

    pub fn spill_dir(&self) -> &Path {
        &self.spill_dir
    }

    /// Sort and flush the current in-memory buffer into one spilled run.
    fn spill_buffer(&mut self, rows: &mut Vec<Vec<Scalar>>, buffered: &mut u64) -> QueryResult<()> {
        rows.sort_by(|a, b| compare_keys(a, b, &self.keys));
        let batch = rows_to_batch(self.schema.clone(), rows)?;
        self.run_seq += 1;
        let mut writer = SpillWriter::create(
            &self.spill_dir,
            self.run_seq,
            self.schema.clone(),
            self.tracker.clone(),
        )?;
        writer.write_batch(&batch)?;
        let run = writer.seal();
        self.runs.push(run);
        self.runs_count += 1;
        self.tracker.sub_buffered(*buffered);
        *buffered = 0;
        rows.clear();
        Ok(())
    }

    /// Drain the child, spilling whenever the memory budget is exceeded.
    fn build(&mut self, ctrl: &Control) -> QueryResult<()> {
        let name = self.name().to_string();
        let mut rows: Vec<Vec<Scalar>> = Vec::new();
        let mut buffered: u64 = 0;

        loop {
            // Cancellation/deadline safe point before every blocking pull.
            ctrl.check()?;
            let batch = match self.base.child.next(ctrl) {
                Ok(Some(b)) => b,
                Ok(None) => break,
                // An errored upstream has already poisoned itself; never pull
                // from it again. Propagate immediately.
                Err(e) => return Err(e),
            };

            for row in batch_rows(&batch)? {
                let sz = row_size(&row);
                rows.push(row);
                buffered += sz;
                self.tracker.add_buffered(sz);
                if self.memory_budget > 0 && buffered > self.memory_budget {
                    ctrl.diag().info(
                        &name,
                        "spilling",
                        format!(
                            "buffered={buffered}B > budget={}B; spilling run #{} rows={}",
                            self.memory_budget,
                            self.run_seq + 1,
                            rows.len()
                        ),
                    );
                    ctrl.check()?;
                    self.spill_buffer(&mut rows, &mut buffered)?;
                }
            }
        }

        if self.runs.is_empty() {
            rows.sort_by(|a, b| compare_keys(a, b, &self.keys));
            ctrl.diag().info(
                &name,
                "sorted_in_memory",
                format!("rows={} (no spill)", rows.len()),
            );
            self.phase = Phase::Memory { rows, from: 0 };
        } else {
            if !rows.is_empty() {
                ctrl.diag()
                    .info(&name, "spilling_tail", format!("rows={}", rows.len()));
                self.spill_buffer(&mut rows, &mut buffered)?;
            }
            let mut cursors = Vec::with_capacity(self.runs.len());
            for run in std::mem::take(&mut self.runs) {
                cursors.push(RunCursor::open(run)?);
            }
            ctrl.diag().info(
                &name,
                "merging_runs",
                format!("runs={} k-way streaming merge", cursors.len()),
            );
            self.phase = Phase::Merge { cursors };
        }
        Ok(())
    }

    fn emit_merge(&mut self) -> QueryResult<Option<Batch>> {
        let mut picked: Vec<Vec<Scalar>> = Vec::with_capacity(self.out_batch_rows);
        let cursors = match &mut self.phase {
            Phase::Merge { cursors } => cursors,
            _ => return Err(QueryError::state_conflict("emit_merge without merge phase")),
        };
        loop {
            // Choose the run whose head sorts first; ties favor the lower run
            // index, giving a stable merge.
            let mut best: Option<usize> = None;
            for i in 0..cursors.len() {
                let head = cursors[i].head();
                match (head, best) {
                    (Some(_), None) => best = Some(i),
                    (Some(row), Some(bi)) => {
                        if compare_keys(row, cursors[bi].head().unwrap(), &self.keys)
                            == std::cmp::Ordering::Less
                        {
                            best = Some(i);
                        }
                    }
                    (None, _) => {}
                }
            }
            match best {
                None => break,
                Some(i) => {
                    picked.push(cursors[i].pop()?);
                    if picked.len() >= self.out_batch_rows {
                        break;
                    }
                }
            }
        }
        if picked.is_empty() {
            Ok(None)
        } else {
            Ok(Some(rows_to_batch(self.schema.clone(), &picked)?))
        }
    }
}

impl Operator for Sort {
    fn name(&self) -> &str {
        self.base.core.name()
    }
    fn schema_out(&self) -> Arc<Schema> {
        self.schema.clone()
    }
    fn core(&self) -> &OperatorCore {
        &self.base.core
    }
    fn core_mut(&mut self) -> &mut OperatorCore {
        &mut self.base.core
    }

    fn pull(&mut self, ctrl: &Control) -> QueryResult<Option<Batch>> {
        if matches!(self.phase, Phase::Building) {
            self.build(ctrl)?;
        }
        match &mut self.phase {
            Phase::Memory { rows, from } => {
                if *from >= rows.len() {
                    return Ok(None);
                }
                let end = (*from + self.out_batch_rows).min(rows.len());
                let batch = rows_to_batch(self.schema.clone(), &rows[*from..end])?;
                *from = end;
                Ok(Some(batch))
            }
            Phase::Merge { .. } => self.emit_merge(),
            Phase::Building => Err(QueryError::state_conflict(
                "sort still building after build() returned",
            )),
            Phase::Closed => Err(QueryError::state_conflict("pull after close")),
        }
    }

    fn release(&mut self, ctrl: Option<&Control>) {
        // Replacing the phase drops in-memory rows or, more importantly, the
        // merge cursors and therefore their RunReader file descriptors.
        self.phase = Phase::Closed;
        // Drop any run handles retained before the merge phase (defensive):
        // physical files are removed at most once by RunFile's Drop.
        self.runs.clear();
        if self.spill_dir.exists() {
            let _ = std::fs::remove_dir_all(&self.spill_dir);
        }
        // Zero any still-accounted buffered bytes (tail never emitted).
        self.tracker.sub_buffered(self.tracker.buffered_bytes());
        self.base.child.shutdown(ctrl);
    }
}
