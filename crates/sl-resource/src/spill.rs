//! External (spill) corridor backed by local Arrow IPC *stream* files.
//!
//! When the in-memory state budget is exceeded and the overflow policy is
//! [`sl_types::OverflowPolicy::ExternalSelect`], the operator flushes sorted
//! runs here and merges them afterwards. Everything is local, synthetic,
//! deterministic: a fresh uniquely-named file under a configured directory,
//! no network and no credentials.
//!
//! Each spilled run carries the full relation schema plus one extra trailing
//! `Int64` column holding the global stable row identity, so that ordering and
//! stable tie-breaking survive a round trip.

use std::fs::{self, File};
use std::io::{BufReader, BufWriter};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

use arrow2::array::Array;
use arrow2::chunk::Chunk;
use arrow2::datatypes::{DataType, Field, Schema as ArrowSchema};
use arrow2::io::ipc::read::{read_stream_metadata, StreamReader, StreamState};
use arrow2::io::ipc::write::{StreamWriter, WriteOptions};
use sl_types::{ErrorCategory, RelationSchema, SlError};

/// Name of the trailing identity column appended to spilled runs.
pub const ROW_ID_COLUMN: &str = "__sl_row_id";

/// Owns the spill directory and hands out fresh run files.
#[derive(Debug, Clone)]
pub struct SpillStore {
    dir: PathBuf,
    seq: Arc<AtomicU64>,
}

impl SpillStore {
    /// Open (creating if needed) a spill directory.
    pub fn open(dir: impl Into<PathBuf>) -> Result<Self, SlError> {
        let dir = dir.into();
        fs::create_dir_all(&dir).map_err(|e| {
            SlError::new(
                ErrorCategory::ExternalStorage,
                "spill_dir_create",
                format!("cannot create spill directory {}: {e}", dir.display()),
                "sl_resource::spill",
            )
        })?;
        // Probe writability up front so external-select fails at the boundary
        // instead of deep inside the operator.
        let probe = dir.join(".sl-write-probe");
        fs::write(&probe, b"ok").map_err(|e| {
            SlError::new(
                ErrorCategory::ExternalStorage,
                "spill_dir_not_writable",
                format!("spill directory {} is not writable: {e}", dir.display()),
                "sl_resource::spill",
            )
        })?;
        let _ = fs::remove_file(&probe);
        Ok(Self {
            dir,
            seq: Arc::new(AtomicU64::new(0)),
        })
    }

    pub fn dir(&self) -> &Path {
        &self.dir
    }

    fn fresh_path(&self, request_id: &str, label: &str) -> PathBuf {
        let n = self.seq.fetch_add(1, Ordering::Relaxed);
        // Keep the request identity in the file name for log/artifact
        // correlation; sanitize conservatively.
        let safe: String = request_id
            .chars()
            .map(|c| if c.is_ascii_alphanumeric() || c == '-' || c == '_' { c } else { '_' })
            .collect();
        self.dir
            .join(format!("run-{safe}-{label}-{n:06}.arrow"))
    }

    /// Begin writing a new run. The schema is the user relation plus the
    /// identity column.
    pub fn create_run(
        &self,
        request_id: &str,
        label: &str,
        schema: &RelationSchema,
    ) -> Result<RunWriter, SlError> {
        let path = self.fresh_path(request_id, label);
        let file = File::create(&path).map_err(|e| spill_io(&path, "create", e))?;
        let arrow_schema = spill_arrow_schema(schema);
        let mut writer = StreamWriter::new(
            BufWriter::new(file),
            WriteOptions { compression: None },
        );
        writer
            .start(&arrow_schema, None)
            .map_err(|e| spill_arrow(&path, "start", e))?;
        Ok(RunWriter {
            path,
            writer,
            rows: 0,
        })
    }
}

/// Arrow schema of a spilled run: user columns then the identity column.
pub fn spill_arrow_schema(schema: &RelationSchema) -> ArrowSchema {
    let mut fields: Vec<Field> = schema
        .columns
        .iter()
        .map(|c| c.to_arrow_field())
        .collect();
    fields.push(Field::new(ROW_ID_COLUMN, DataType::Int64, false));
    ArrowSchema::from(fields)
}

/// Writes one sorted run as a sequence of chunks.
pub struct RunWriter {
    path: PathBuf,
    writer: StreamWriter<BufWriter<File>>,
    rows: u64,
}

impl RunWriter {
    /// Append a chunk whose last column must be the non-null Int64 identity.
    pub fn write_chunk(&mut self, chunk: &Chunk<Box<dyn Array>>) -> Result<(), SlError> {
        self.writer
            .write(chunk, None)
            .map_err(|e| spill_arrow(&self.path, "write", e))?;
        self.rows = self.rows.saturating_add(chunk.len() as u64);
        Ok(())
    }

    /// Finish the stream and return the run descriptor.
    pub fn finish(mut self) -> Result<SpilledRun, SlError> {
        self.writer
            .finish()
            .map_err(|e| spill_arrow(&self.path, "finish", e))?;
        let bytes = fs::metadata(&self.path)
            .map(|m| m.len())
            .unwrap_or(0);
        Ok(SpilledRun {
            path: self.path,
            rows: self.rows,
            bytes,
        })
    }
}

/// Descriptor of a finished spilled run.
#[derive(Debug, Clone)]
pub struct SpilledRun {
    pub path: PathBuf,
    pub rows: u64,
    pub bytes: u64,
}

impl SpilledRun {
    /// Open a forward iterator over the run's chunks.
    pub fn open(&self) -> Result<RunReader, SlError> {
        let file = File::open(&self.path).map_err(|e| spill_io(&self.path, "open", e))?;
        let mut reader = BufReader::new(file);
        let metadata = read_stream_metadata(&mut reader)
            .map_err(|e| spill_arrow(&self.path, "read_metadata", e))?;
        Ok(RunReader {
            inner: StreamReader::new(reader, metadata, None),
            path: self.path.clone(),
        })
    }

    /// Best-effort removal; surfaced via tracing, never panics.
    pub fn cleanup(&self) {
        if let Err(e) = fs::remove_file(&self.path) {
            tracing::warn!(path = %self.path.display(), error = %e, "failed to remove spill file");
        }
    }
}

/// Iterator yielding arrow chunks of a spilled run.
pub struct RunReader {
    inner: StreamReader<BufReader<File>>,
    path: PathBuf,
}

impl RunReader {
    pub fn next_chunk(&mut self) -> Result<Option<Chunk<Box<dyn Array>>>, SlError> {
        loop {
            match self.inner.next() {
                None => return Ok(None),
                Some(Ok(StreamState::Some(chunk))) => return Ok(Some(chunk)),
                Some(Ok(StreamState::Waiting)) => continue,
                Some(Err(e)) => return Err(spill_arrow(&self.path, "read", e)),
            }
        }
    }
}

fn spill_io(path: &Path, op: &'static str, e: std::io::Error) -> SlError {
    SlError::new(
        ErrorCategory::ExternalStorage,
        "spill_io",
        format!("spill {op} failed for {}: {e}", path.display()),
        "sl_resource::spill",
    )
}

fn spill_arrow(path: &Path, op: &'static str, e: arrow2::error::Error) -> SlError {
    SlError::new(
        ErrorCategory::ExternalStorage,
        "spill_arrow_ipc",
        format!("spill {op} failed for {}: {e}", path.display()),
        "sl_resource::spill",
    )
}
