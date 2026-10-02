//! Resources & state: memory accounting, spill files, and a typed run codec.
//!
//! The blocking sort cannot hold unlimited input. Once buffered rows exceed a
//! memory budget it sorts the in-memory part and *spills* one sorted run to a
//! temporary file, then frees the buffer. A run goes through three cheaply
//! shareable phases:
//!
//! 1. [`SpillWriter`] holds the write file descriptor; `write_batch` appends a
//!    length-prefixed typed frame.
//! 2. [`SpillWriter::seal`] drops the write fd and returns an cheaply-clonable
//!    [`RunFile`] (`Arc`). The file stays on disk for the merge.
//! 3. [`RunReader::open`] takes an `Arc<RunFile>` and streams frames back one
//!    batch at a time. The physical file is removed when the last `RunFile`
//!    owner disappears (or on an explicit, idempotent [`RunFile::delete`]), so
//!    readers can be stored alongside other operator state without lifetime
//!    coupling to the run registry.
//!
//! Every live file descriptor and every accounted byte flows through
//! [`ResourceTracker`], which lets tests assert deterministically that handles,
//! buffers and spill files are all reclaimed after close.

use std::fs::{File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;

use crate::batch::{Batch, BatchBuilder, ColumnType, Scalar, Schema};
use crate::error::{QueryError, QueryResult};

/// Process-wide accounting of live fds, buffered/spilled bytes and run count.
#[derive(Debug)]
pub struct ResourceTracker {
    open_files: AtomicU64,
    buffered_bytes: AtomicU64,
    spilled_bytes: AtomicU64,
    spill_files_created: AtomicU64,
    peak_open_files: AtomicU64,
}

impl ResourceTracker {
    pub fn new() -> Arc<Self> {
        Arc::new(Self {
            open_files: AtomicU64::new(0),
            buffered_bytes: AtomicU64::new(0),
            spilled_bytes: AtomicU64::new(0),
            spill_files_created: AtomicU64::new(0),
            peak_open_files: AtomicU64::new(0),
        })
    }

    pub fn open_files(&self) -> u64 {
        self.open_files.load(Ordering::SeqCst)
    }
    pub fn buffered_bytes(&self) -> u64 {
        self.buffered_bytes.load(Ordering::SeqCst)
    }
    pub fn spilled_bytes(&self) -> u64 {
        self.spilled_bytes.load(Ordering::SeqCst)
    }
    pub fn spill_files_created(&self) -> u64 {
        self.spill_files_created.load(Ordering::SeqCst)
    }
    pub fn peak_open_files(&self) -> u64 {
        self.peak_open_files.load(Ordering::SeqCst)
    }

    fn inc_files(&self) {
        let n = self.open_files.fetch_add(1, Ordering::SeqCst) + 1;
        self.peak_open_files.fetch_max(n, Ordering::SeqCst);
    }
    fn dec_files(&self) {
        self.open_files.fetch_sub(1, Ordering::SeqCst);
    }

    pub fn add_buffered(&self, bytes: u64) {
        self.buffered_bytes.fetch_add(bytes, Ordering::SeqCst);
    }
    pub fn sub_buffered(&self, bytes: u64) {
        self.buffered_bytes.fetch_sub(bytes, Ordering::SeqCst);
    }
    pub fn add_spilled(&self, bytes: u64) {
        self.spilled_bytes.fetch_add(bytes, Ordering::SeqCst);
    }
}

/// Shared identity of one spilled run. The on-disk file is removed at most
/// once: either by an explicit [`RunFile::delete`] or when the last owner is
/// dropped, whichever comes first.
pub struct RunFile {
    path: PathBuf,
    schema: Arc<Schema>,
    tracker: Arc<ResourceTracker>,
    deleted: AtomicBool,
}

impl RunFile {
    pub fn path(&self) -> &Path {
        &self.path
    }
    pub fn schema(&self) -> &Arc<Schema> {
        &self.schema
    }

    /// Remove the backing file now if it still exists. Idempotent and safe to
    /// call while readers are open (their fds keep working until dropped; the
    /// directory entry is gone, which is the desired clean-up guarantee).
    pub fn delete(&self) {
        if self
            .deleted
            .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
            .is_ok()
            && self.path.exists()
        {
            if let Err(e) = std::fs::remove_file(&self.path) {
                eprintln!("warning: failed to remove spill file {:?}: {e}", self.path);
            }
        }
    }
}

impl Drop for RunFile {
    fn drop(&mut self) {
        self.delete();
    }
}

/// Write handle for a run. Owns the only write fd while alive.
pub struct SpillWriter {
    file: Option<File>,
    run: Option<Arc<RunFile>>,
}

impl SpillWriter {
    pub fn create(
        dir: &Path,
        seq: u64,
        schema: Arc<Schema>,
        tracker: Arc<ResourceTracker>,
    ) -> QueryResult<Self> {
        std::fs::create_dir_all(dir).map_err(|e| {
            QueryError::resource_exhausted(format!("cannot create spill dir {dir:?}: {e}"))
        })?;
        let path = dir.join(format!("run-{seq:04}.pqspill"));
        let file = OpenOptions::new()
            .write(true)
            .read(true)
            .create_new(true)
            .open(&path)
            .map_err(|e| {
                QueryError::resource_exhausted(format!("cannot create spill file {path:?}: {e}"))
                    .with("spill_path", path.to_string_lossy().into_owned())
            })?;
        tracker.inc_files();
        tracker.spill_files_created.fetch_add(1, Ordering::SeqCst);
        Ok(Self {
            file: Some(file),
            run: Some(Arc::new(RunFile {
                path,
                schema,
                tracker,
                deleted: AtomicBool::new(false),
            })),
        })
    }

    /// Append one batch as a length-prefixed typed frame.
    pub fn write_batch(&mut self, batch: &Batch) -> QueryResult<u64> {
        let file = self
            .file
            .as_mut()
            .ok_or_else(|| QueryError::state_conflict("write to sealed spill writer"))?;
        let payload = encode_batch(batch)?;
        file.write_all(&(payload.len() as u32).to_le_bytes())?;
        file.write_all(&payload)?;
        file.flush()?;
        let n = 4 + payload.len() as u64;
        self.run
            .as_ref()
            .expect("sealed writer cannot write")
            .tracker
            .add_spilled(n);
        Ok(n)
    }

    /// Drop the write fd and hand back the shareable run identity. The file
    /// remains on disk until `RunFile` owners are gone.
    pub fn seal(mut self) -> Arc<RunFile> {
        if let Some(f) = self.file.take() {
            drop(f);
            if let Some(run) = self.run.as_ref() {
                run.tracker.dec_files();
            }
        }
        self.run.take().expect("seal called once")
    }
}

impl Drop for SpillWriter {
    fn drop(&mut self) {
        // An unsealed writer (e.g. dropped after an error) still holds its fd.
        if let Some(f) = self.file.take() {
            drop(f);
            if let Some(run) = self.run.as_ref() {
                run.tracker.dec_files();
            }
        }
    }
}

/// Streaming reader over a sealed run. Owns an `Arc<RunFile>` and a read fd,
/// yielding one decoded batch at a time. The fd count is reclaimed on drop.
pub struct RunReader {
    #[allow(dead_code)]
    run: Arc<RunFile>,
    file: File,
    tracker: Arc<ResourceTracker>,
    schema: Arc<Schema>,
}

impl RunReader {
    pub fn open(run: Arc<RunFile>) -> QueryResult<Self> {
        let file = File::open(run.path()).map_err(|e| {
            QueryError::computation(format!("cannot reopen spill run {:?}: {e}", run.path()))
        })?;
        run.tracker.inc_files();
        let tracker = Arc::clone(&run.tracker);
        let schema = run.schema.clone();
        Ok(Self {
            run,
            file,
            tracker,
            schema,
        })
    }

    /// Decode the next frame. `Ok(None)` marks a clean end-of-run.
    pub fn next_batch(&mut self) -> QueryResult<Option<Batch>> {
        let mut len_buf = [0u8; 4];
        match self.file.read_exact(&mut len_buf) {
            Ok(()) => {}
            Err(e) if e.kind() == std::io::ErrorKind::UnexpectedEof => return Ok(None),
            Err(e) => return Err(e.into()),
        }
        let len = u32::from_le_bytes(len_buf) as usize;
        let mut payload = vec![0u8; len];
        self.file.read_exact(&mut payload)?;
        Ok(Some(decode_batch(&payload, &self.schema)?))
    }
}

impl Drop for RunReader {
    fn drop(&mut self) {
        self.tracker.dec_files();
    }
}

/// Logical byte size of a scalar for the memory budget.
pub fn scalar_size(s: &Scalar) -> u64 {
    match s {
        Scalar::Int(_) => 8,
        Scalar::Bool(_) => 1,
        Scalar::Utf8(Some(v)) => 8 + v.len() as u64,
        Scalar::Utf8(None) => 8,
    }
}

/// Logical bytes of one row across all columns.
pub fn row_size(row: &[Scalar]) -> u64 {
    row.iter().map(scalar_size).sum()
}

// ---------------------------------------------------------------------------
// Typed frame codec. Payload layout:
//   u32 num_rows, u32 num_cols
//   per col: u8 type tag (0=int,1=utf8,2=bool)
//            ceil(num_rows/8) validity bytes (bit i = valid/non-null)
//            int : num_rows * i64 LE
//            bool: ceil(num_rows/8) value bytes (bit i = value)
//            utf8: per row: u32 byte len (0 when null), then bytes
// Each payload is wrapped as: u32 payload_len + payload (see SpillWriter).
// ---------------------------------------------------------------------------

const TAG_INT: u8 = 0;
const TAG_UTF8: u8 = 1;
const TAG_BOOL: u8 = 2;

fn tag_for(ty: ColumnType) -> u8 {
    match ty {
        ColumnType::Int => TAG_INT,
        ColumnType::Utf8 => TAG_UTF8,
        ColumnType::Bool => TAG_BOOL,
    }
}

fn pack_bits(n: usize, get: impl Fn(usize) -> bool) -> Vec<u8> {
    let mut out = vec![0u8; n.div_ceil(8)];
    for i in 0..n {
        if get(i) {
            out[i / 8] |= 1 << (i % 8);
        }
    }
    out
}

fn bit(bytes: &[u8], i: usize) -> bool {
    bytes
        .get(i / 8)
        .map(|b| b & (1 << (i % 8)) != 0)
        .unwrap_or(false)
}

fn encode_batch(batch: &Batch) -> QueryResult<Vec<u8>> {
    let mut w: Vec<u8> = Vec::new();
    let n = batch.num_rows();
    w.write_all(&(n as u32).to_le_bytes())?;
    w.write_all(&(batch.schema().len() as u32).to_le_bytes())?;

    for (col, (_, ty)) in batch.schema().fields().iter().enumerate() {
        w.write_all(&[tag_for(*ty)])?;
        let scalars = batch.column_scalars(col)?;
        w.write_all(&pack_bits(n, |i| !scalars[i].is_null()))?;

        match ty {
            ColumnType::Int => {
                for s in &scalars {
                    let v = match s {
                        Scalar::Int(Some(x)) => *x,
                        _ => 0,
                    };
                    w.write_all(&v.to_le_bytes())?;
                }
            }
            ColumnType::Bool => {
                w.write_all(&pack_bits(n, |i| {
                    matches!(scalars[i], Scalar::Bool(Some(true)))
                }))?;
            }
            ColumnType::Utf8 => {
                for s in &scalars {
                    match s {
                        Scalar::Utf8(Some(v)) => {
                            w.write_all(&(v.len() as u32).to_le_bytes())?;
                            w.write_all(v.as_bytes())?;
                        }
                        _ => w.write_all(&0u32.to_le_bytes())?,
                    }
                }
            }
        }
    }
    Ok(w)
}

fn read_u32(buf: &[u8], pos: &mut usize) -> QueryResult<u32> {
    if *pos + 4 > buf.len() {
        return Err(QueryError::computation("spill frame truncated (u32)"));
    }
    let v = u32::from_le_bytes(buf[*pos..*pos + 4].try_into().unwrap());
    *pos += 4;
    Ok(v)
}

fn read_exact<'a>(buf: &'a [u8], pos: &mut usize, len: usize) -> QueryResult<&'a [u8]> {
    if *pos + len > buf.len() {
        return Err(QueryError::computation("spill frame truncated (bytes)"));
    }
    let s = &buf[*pos..*pos + len];
    *pos += len;
    Ok(s)
}

fn decode_batch(buf: &[u8], schema: &Arc<Schema>) -> QueryResult<Batch> {
    let mut pos = 0;
    let n = read_u32(buf, &mut pos)? as usize;
    let ncol = read_u32(buf, &mut pos)? as usize;
    if ncol != schema.len() {
        return Err(QueryError::computation(format!(
            "spill frame column count {ncol} != schema {}",
            schema.len()
        )));
    }

    let mut columns: Vec<Vec<Scalar>> = (0..ncol).map(|_| Vec::with_capacity(n)).collect();

    for (col, (_, ty)) in schema.fields().iter().enumerate() {
        let tag = read_exact(buf, &mut pos, 1)?[0];
        let expected_tag = tag_for(*ty);
        if tag != expected_tag {
            return Err(QueryError::computation(format!(
                "spill frame type tag {tag} != schema tag {expected_tag} at column {col}"
            )));
        }
        let vbytes = n.div_ceil(8);
        let validity = read_exact(buf, &mut pos, vbytes)?.to_vec();

        match ty {
            ColumnType::Int => {
                let raw = read_exact(buf, &mut pos, n * 8)?;
                for i in 0..n {
                    let v = i64::from_le_bytes(raw[i * 8..i * 8 + 8].try_into().unwrap());
                    columns[col].push(if bit(&validity, i) {
                        Scalar::Int(Some(v))
                    } else {
                        Scalar::Int(None)
                    });
                }
            }
            ColumnType::Bool => {
                let vals = read_exact(buf, &mut pos, vbytes)?.to_vec();
                for i in 0..n {
                    columns[col].push(if bit(&validity, i) {
                        Scalar::Bool(Some(bit(&vals, i)))
                    } else {
                        Scalar::Bool(None)
                    });
                }
            }
            ColumnType::Utf8 => {
                for i in 0..n {
                    let len = read_u32(buf, &mut pos)? as usize;
                    if bit(&validity, i) {
                        let bytes = read_exact(buf, &mut pos, len)?;
                        let s = std::str::from_utf8(bytes)
                            .map_err(|_| QueryError::computation("spill frame invalid utf8"))?;
                        columns[col].push(Scalar::Utf8(Some(s.to_string())));
                    } else if len != 0 {
                        return Err(QueryError::computation(
                            "spill frame null utf8 had nonzero length",
                        ));
                    } else {
                        columns[col].push(Scalar::Utf8(None));
                    }
                }
            }
        }
    }

    let mut builder = BatchBuilder::new(schema.clone());
    for r in 0..n {
        let row: Vec<Scalar> = columns.iter().map(|c| c[r].clone()).collect();
        builder.add_row(&row)?;
    }
    builder.finish()
}
