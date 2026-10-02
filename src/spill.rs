//! External storage: immutable Arrow IPC segment files.
//!
//! The engine never relies on holding a relation in memory. When an in-memory
//! partition buffer fills, its row keys (or, for value batches, whole rows) are
//! converted to a typed Arrow batch and written as a *single immutable IPC
//! stream file* (a "segment"). Segments are re-read one at a time; because each
//! is bounded by [`Budget::partition_buffer_bytes`], reading a segment never
//! needs unbounded memory. One immutable file per flush also keeps the number
//! of concurrently open file descriptors at one per reader rather than one per
//! partition.
//!
//! Two payload shapes are supported, both Arrow IPC streams:
//! - *key segments*: a single non-null `LargeBinary` column of canonical keys;
//! - *row segments*: the typed logical schema (used for whole-row spill).
//!
//! Every byte written and every file created is charged atomically against the
//! [`Budget`]; crossing either limit returns `resource_exhausted`.

use std::fs::File;
use std::io::{BufReader, BufWriter};
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use arrow2::array::{Array, BinaryArray, PrimitiveArray};
use arrow2::buffer::Buffer;
use arrow2::chunk::Chunk;
use arrow2::datatypes::{DataType as ADataType, Field as AField, Schema as ASchema};
use arrow2::io::ipc::read::{StreamReader, StreamState, read_stream_metadata};
use arrow2::io::ipc::write::{StreamWriter, WriteOptions};
use arrow2::offset::OffsetsBuffer;

use crate::arrow::{chunk_to_rows, rows_to_chunk};
use crate::error::{Result, SetOpError};
use crate::resource::Budget;
use crate::value::{Schema, Value};

/// One spilled Arrow IPC file.
#[derive(Debug, Clone)]
pub struct Segment {
    pub path: PathBuf,
    pub rows: usize,
    /// On-disk IPC byte length.
    pub bytes: u64,
}

#[derive(Debug, Clone, Copy)]
pub struct SpillCounters {
    pub files: usize,
    pub bytes: u64,
}

#[derive(Default)]
struct Counts {
    files: usize,
    bytes: u64,
    seq: u64,
}

/// Owns a scratch directory and accounts for everything written under it.
pub struct SpillManager {
    root: PathBuf,
    budget: Budget,
    counts: Mutex<Counts>,
}

impl SpillManager {
    /// Use `root` as the scratch directory. It must already exist.
    pub fn new(root: impl Into<PathBuf>, budget: Budget) -> Self {
        Self {
            root: root.into(),
            budget,
            counts: Mutex::new(Counts::default()),
        }
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    pub fn budget(&self) -> &Budget {
        &self.budget
    }

    pub fn counters(&self) -> SpillCounters {
        let c = self.counts.lock().expect("spill lock poisoned");
        SpillCounters {
            files: c.files,
            bytes: c.bytes,
        }
    }

    /// Atomically decide a write fits, reserve its file+byte budget, and return
    /// a unique destination path. Serialized so names and logs are ordered.
    fn reserve(&self, bytes: u64, label: &str) -> Result<PathBuf> {
        let mut c = self.counts.lock().expect("spill lock poisoned");
        if c.files + 1 > self.budget.spill_files {
            return Err(SetOpError::resource(
                "spill_file_limit",
                format!(
                    "spill segment file limit ({}) exceeded",
                    self.budget.spill_files
                ),
            ));
        }
        let new_bytes = c.bytes.checked_add(bytes).ok_or_else(|| {
            SetOpError::resource("spill_byte_overflow", "spill byte counter overflowed u64")
        })?;
        if new_bytes > self.budget.spill_bytes {
            return Err(SetOpError::resource(
                "spill_byte_limit",
                format!(
                    "spill byte budget ({}) exceeded by a {bytes}-byte segment",
                    self.budget.spill_bytes
                ),
            ));
        }
        let id = c.seq;
        c.seq += 1;
        c.files += 1;
        c.bytes = new_bytes;
        Ok(self
            .root
            .join(format!("seg_{id:06}_{}.arrow", sanitize(label))))
    }

    /// Write canonical row keys as a one-column `LargeBinary` IPC segment.
    pub fn write_keys(&self, keys: &[Vec<u8>], label: &str) -> Result<Segment> {
        if keys.is_empty() {
            return Err(SetOpError::compute(
                "empty_segment",
                "refusing to spill an empty segment",
            ));
        }
        let chunk = keys_to_chunk(keys)?;
        // Serialize first; reserve budget only once the byte length is known.
        let bytes = serialize_size(&chunk, &keys_arrow_schema())?;
        let path = self.reserve(bytes, label)?;
        write_ipc(&path, &chunk, &keys_arrow_schema())?;
        Ok(Segment {
            path,
            rows: keys.len(),
            bytes,
        })
    }

    /// Read a key segment back.
    pub fn read_keys(&self, seg: &Segment) -> Result<Vec<Vec<u8>>> {
        read_key_segments(&seg.path)
    }

    /// Write whole typed rows under the logical schema (used for row-level spill
    /// and fixtures/tests).
    pub fn write_rows(&self, schema: &Schema, rows: &[Vec<Value>], label: &str) -> Result<Segment> {
        if rows.is_empty() {
            return Err(SetOpError::compute(
                "empty_segment",
                "refusing to spill an empty segment",
            ));
        }
        let chunk = rows_to_chunk(schema, rows)?;
        let arrow_schema = crate::arrow::to_arrow_schema(schema);
        let bytes = serialize_size(&chunk, &arrow_schema)?;
        let path = self.reserve(bytes, label)?;
        write_ipc(&path, &chunk, &arrow_schema)?;
        Ok(Segment {
            path,
            rows: rows.len(),
            bytes,
        })
    }

    /// Read a whole-row segment back.
    pub fn read_rows(&self, schema: &Schema, seg: &Segment) -> Result<Vec<Vec<Value>>> {
        let file =
            File::open(&seg.path).map_err(|e| SetOpError::compute("spill_open", e.to_string()))?;
        let mut reader = BufReader::new(file);
        let metadata = read_stream_metadata(&mut reader)
            .map_err(|e| SetOpError::compute("ipc_meta", e.to_string()))?;
        let stream = StreamReader::new(reader, metadata, None);
        let mut rows = Vec::with_capacity(seg.rows);
        for state in stream {
            match state.map_err(|e| SetOpError::compute("ipc_read", e.to_string()))? {
                StreamState::Some(chunk) => rows.extend(chunk_to_rows(schema, &chunk)?),
                StreamState::Waiting => {}
            }
        }
        Ok(rows)
    }

    /// Write a result fragment: pairs of canonical key and output multiplicity.
    /// Counts are stored explicitly so DISTINCT/ALL multiplicity is preserved
    /// on disk without expanding rows.
    pub fn write_results(&self, items: &[(Vec<u8>, u64)], label: &str) -> Result<Segment> {
        if items.is_empty() {
            return Err(SetOpError::compute(
                "empty_segment",
                "refusing to spill an empty segment",
            ));
        }
        let chunk = results_to_chunk(items)?;
        let bytes = serialize_size(&chunk, &results_arrow_schema())?;
        let path = self.reserve(bytes, label)?;
        write_ipc(&path, &chunk, &results_arrow_schema())?;
        Ok(Segment {
            path,
            rows: items.len(),
            bytes,
        })
    }

    /// Read one result fragment back as (key, count) pairs.
    pub fn read_results(&self, seg: &Segment) -> Result<Vec<(Vec<u8>, u64)>> {
        read_result_segment(&seg.path)
    }
}

fn sanitize(label: &str) -> String {
    label
        .chars()
        .map(|ch| {
            if ch.is_ascii_alphanumeric() || ch == '_' || ch == '-' {
                ch
            } else {
                '_'
            }
        })
        .collect()
}

// ---- Key segments: single LargeBinary column ----

fn keys_arrow_schema() -> ASchema {
    ASchema::from(vec![AField::new("row_key", ADataType::LargeBinary, false)])
}

fn keys_to_chunk(keys: &[Vec<u8>]) -> Result<Chunk<Box<dyn Array>>> {
    let mut offsets: Vec<i64> = Vec::with_capacity(keys.len() + 1);
    let mut values: Vec<u8> = Vec::new();
    offsets.push(0);
    for k in keys {
        values.extend_from_slice(k);
        offsets.push(values.len() as i64);
    }
    let arr = BinaryArray::<i64>::try_new(
        ADataType::LargeBinary,
        OffsetsBuffer::try_from(Buffer::from(offsets))
            .map_err(|e| SetOpError::compute("offset_error", e.to_string()))?,
        Buffer::from(values),
        None,
    )
    .map_err(|e| SetOpError::compute("keys_chunk", e.to_string()))?;
    Chunk::try_new(vec![arr.boxed()]).map_err(|e| SetOpError::compute("keys_chunk", e.to_string()))
}

fn read_key_segments(path: &Path) -> Result<Vec<Vec<u8>>> {
    let file = File::open(path).map_err(|e| SetOpError::compute("spill_open", e.to_string()))?;
    let mut reader = BufReader::new(file);
    let metadata = read_stream_metadata(&mut reader)
        .map_err(|e| SetOpError::compute("ipc_meta", e.to_string()))?;
    let stream = StreamReader::new(reader, metadata, None);
    let mut keys = Vec::new();
    for state in stream {
        match state.map_err(|e| SetOpError::compute("ipc_read", e.to_string()))? {
            StreamState::Some(chunk) => {
                let arr = chunk.columns()[0]
                    .as_any()
                    .downcast_ref::<BinaryArray<i64>>()
                    .ok_or_else(|| {
                        SetOpError::compute("keys_downcast", "not a LargeBinary column")
                    })?;
                for i in 0..arr.len() {
                    keys.push(arr.value(i).to_vec());
                }
            }
            StreamState::Waiting => {}
        }
    }
    Ok(keys)
}

// ---- Result segments: (LargeBinary key, UInt64 multiplicity) ----

fn results_arrow_schema() -> ASchema {
    ASchema::from(vec![
        AField::new("row_key", ADataType::LargeBinary, false),
        AField::new("count", ADataType::UInt64, false),
    ])
}

fn results_to_chunk(items: &[(Vec<u8>, u64)]) -> Result<Chunk<Box<dyn Array>>> {
    let mut offsets: Vec<i64> = Vec::with_capacity(items.len() + 1);
    let mut values: Vec<u8> = Vec::new();
    let mut counts: Vec<u64> = Vec::with_capacity(items.len());
    offsets.push(0);
    for (k, n) in items {
        values.extend_from_slice(k);
        offsets.push(values.len() as i64);
        counts.push(*n);
    }
    let keys = BinaryArray::<i64>::try_new(
        ADataType::LargeBinary,
        OffsetsBuffer::try_from(Buffer::from(offsets))
            .map_err(|e| SetOpError::compute("offset_error", e.to_string()))?,
        Buffer::from(values),
        None,
    )
    .map_err(|e| SetOpError::compute("results_chunk", e.to_string()))?;
    let cnt = PrimitiveArray::<u64>::try_new(ADataType::UInt64, Buffer::from(counts), None)
        .map_err(|e| SetOpError::compute("results_chunk", e.to_string()))?;
    Chunk::try_new(vec![keys.boxed(), cnt.boxed()])
        .map_err(|e| SetOpError::compute("results_chunk", e.to_string()))
}

fn read_result_segment(path: &Path) -> Result<Vec<(Vec<u8>, u64)>> {
    let file = File::open(path).map_err(|e| SetOpError::compute("spill_open", e.to_string()))?;
    let mut reader = BufReader::new(file);
    let metadata = read_stream_metadata(&mut reader)
        .map_err(|e| SetOpError::compute("ipc_meta", e.to_string()))?;
    let stream = StreamReader::new(reader, metadata, None);
    let mut items = Vec::new();
    for state in stream {
        match state.map_err(|e| SetOpError::compute("ipc_read", e.to_string()))? {
            StreamState::Some(chunk) => {
                let keys = chunk.columns()[0]
                    .as_any()
                    .downcast_ref::<BinaryArray<i64>>()
                    .ok_or_else(|| {
                        SetOpError::compute("results_downcast", "key column is not LargeBinary")
                    })?;
                let counts = chunk.columns()[1]
                    .as_any()
                    .downcast_ref::<PrimitiveArray<u64>>()
                    .ok_or_else(|| {
                        SetOpError::compute("results_downcast", "count column is not UInt64")
                    })?;
                for i in 0..keys.len() {
                    items.push((keys.value(i).to_vec(), counts.value(i)));
                }
            }
            StreamState::Waiting => {}
        }
    }
    Ok(items)
}

// ---- IPC helpers ----

fn serialize_size(chunk: &Chunk<Box<dyn Array>>, schema: &ASchema) -> Result<u64> {
    let mut buf: Vec<u8> = Vec::new();
    {
        let mut writer = StreamWriter::new(&mut buf, WriteOptions::default());
        writer
            .start(schema, None)
            .map_err(|e| SetOpError::compute("ipc_start", e.to_string()))?;
        writer
            .write(chunk, None)
            .map_err(|e| SetOpError::compute("ipc_write", e.to_string()))?;
        writer
            .finish()
            .map_err(|e| SetOpError::compute("ipc_finish", e.to_string()))?;
    }
    Ok(buf.len() as u64)
}

fn write_ipc(path: &Path, chunk: &Chunk<Box<dyn Array>>, schema: &ASchema) -> Result<()> {
    // Serialize fully to memory before creating the file, so a serialization
    // failure leaves no empty segment that was already charged to the budget.
    let mut buf: Vec<u8> = Vec::new();
    {
        let mut writer = StreamWriter::new(&mut buf, WriteOptions::default());
        writer
            .start(schema, None)
            .map_err(|e| SetOpError::compute("ipc_start", e.to_string()))?;
        writer
            .write(chunk, None)
            .map_err(|e| SetOpError::compute("ipc_write", e.to_string()))?;
        writer
            .finish()
            .map_err(|e| SetOpError::compute("ipc_finish", e.to_string()))?;
    }
    let file =
        File::create(path).map_err(|e| SetOpError::compute("spill_create", e.to_string()))?;
    let mut w = BufWriter::new(file);
    use std::io::Write;
    w.write_all(&buf)
        .and_then(|_| w.flush())
        .map_err(|e| SetOpError::compute("spill_write", e.to_string()))
}
