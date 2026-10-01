//! External sorting: bounded-memory sorted runs on disk + k-way merge.
//!
//! Ingestion keeps at most one sorted buffer in memory.  When the next record
//! would exceed the memory budget, the buffer is sorted and written as a
//! *run file*, then dropped — so neither large groups, heavy skew nor long
//! strings can cause unbounded in-memory growth.  Final grouping reads the
//! sorted runs through a stable k-way merge keyed on
//! `(group key, value, ingestion sequence)`.
//!
//! Runs and a JSON manifest are durable before a cancellation is honoured,
//! which makes the merge phase resumable: see [`SpillManager::resume`].

use std::cmp::Ordering;
use std::collections::BinaryHeap;
use std::fs::{self, File};
use std::io::{BufReader, BufWriter, Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering as AtomicOrdering};

use serde::{Deserialize, Serialize};

use crate::error::{Error, ErrorKind, Result};
use crate::exec::budget::RECORD_OVERHEAD_BYTES;
use crate::exec::cancel::Token;
use crate::exec::cells::{read_cell, write_cell, Cell};

const MAGIC: &[u8; 4] = b"GRUN";
const FORMAT_VERSION: u8 = 1;
/// Check for cancellation every N merged records.
pub const CANCEL_CHECK_INTERVAL: u64 = 256;

/// One ingested row projected onto the columns an aggregation needs.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Record {
    pub key: Vec<Cell>,
    /// Index of the aggregation this record feeds.
    pub agg: u32,
    pub value: Cell,
    /// Whether the aggregation's within-group order is descending.
    pub desc: bool,
    /// Global ingestion order; tie-breaker that makes the sort stable.
    pub seq: u64,
}

impl Record {
    /// Sort order: group key, aggregation index, ordering value, then the
    /// stable ingestion sequence.  Descending aggs reverse only the value
    /// comparison; equal values still keep ingestion order (stable).
    fn sort_cmp(&self, other: &Record) -> Ordering {
        self.key
            .cmp(&other.key)
            .then_with(|| self.agg.cmp(&other.agg))
            .then_with(|| {
                let cmp = self.value.cmp(&other.value);
                if self.desc {
                    cmp.reverse()
                } else {
                    cmp
                }
            })
            .then_with(|| self.seq.cmp(&other.seq))
    }

    pub fn approx_bytes(&self) -> usize {
        RECORD_OVERHEAD_BYTES
            + 8
            + self.key.iter().map(Cell::approx_bytes).sum::<usize>()
            + self.value.approx_bytes()
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct RunMeta {
    pub file: String,
    pub rows: u64,
}

/// Persisted description of the spilled state of one query.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct Manifest {
    pub plan_hash: String,
    pub key_arity: usize,
    pub runs: Vec<RunMeta>,
    /// Sequence number to assign to the next ingested record.
    pub next_seq: u64,
    pub ingested_rows: u64,
    pub peak_memory_bytes: usize,
    pub complete: bool,
}

impl Manifest {
    fn path_in(dir: &Path) -> PathBuf {
        dir.join("manifest.json")
    }
}

/// Owns the per-query spill directory and run bookkeeping.
#[derive(Debug)]
pub struct SpillManager {
    dir: PathBuf,
    plan_hash: String,
    key_arity: usize,
    runs: Vec<RunMeta>,
    next_seq: u64,
    ingested_rows: u64,
    peak_memory_bytes: usize,
    run_counter: AtomicU64,
    complete: bool,
}

impl SpillManager {
    pub fn create(dir: PathBuf, plan_hash: String, key_arity: usize) -> Result<Self> {
        fs::create_dir_all(&dir).map_err(|e| {
            Error::new(
                ErrorKind::SpillIo,
                format!("cannot create spill dir {}: {e}", dir.display()),
            )
        })?;
        Ok(Self {
            dir,
            plan_hash,
            key_arity,
            runs: Vec::new(),
            next_seq: 0,
            ingested_rows: 0,
            peak_memory_bytes: 0,
            run_counter: AtomicU64::new(0),
            complete: false,
        })
    }

    /// Re-open a previously spilled query for resume.
    pub fn resume(spill_root: &Path, token: &ResumeToken) -> Result<(Self, Manifest)> {
        let dir = spill_root.join(&token.dir);
        let manifest = read_manifest(&dir)?;
        if manifest.plan_hash != token.plan_hash {
            return Err(Error::new(
                ErrorKind::InvalidResume,
                "resume token does not match the spilled plan; refusing to mix plans",
            ));
        }
        if !manifest.complete {
            return Err(Error::new(
                ErrorKind::InvalidResume,
                "spilled manifest is not marked complete; ingestion cannot be resumed safely",
            ));
        }
        for run in &manifest.runs {
            if !dir.join(&run.file).is_file() {
                return Err(Error::new(
                    ErrorKind::InvalidResume,
                    format!("spill run '{}' is missing", run.file),
                ));
            }
        }
        let next_run = manifest
            .runs
            .last()
            .map(|r| {
                r.file
                    .trim_start_matches("run-")
                    .trim_end_matches(".bin")
                    .parse::<u64>()
                    .unwrap_or(0)
                    + 1
            })
            .unwrap_or(0);
        let mgr = Self {
            dir,
            plan_hash: manifest.plan_hash.clone(),
            key_arity: manifest.key_arity,
            runs: manifest.runs.clone(),
            next_seq: manifest.next_seq,
            ingested_rows: manifest.ingested_rows,
            peak_memory_bytes: manifest.peak_memory_bytes,
            run_counter: AtomicU64::new(next_run),
            complete: manifest.complete,
        };
        Ok((mgr, manifest))
    }

    pub fn dir(&self) -> &Path {
        &self.dir
    }

    pub fn next_sequence(&mut self) -> u64 {
        let s = self.next_seq;
        self.next_seq += 1;
        self.ingested_rows += 1;
        s
    }

    pub fn ingested_rows(&self) -> u64 {
        self.ingested_rows
    }

    pub fn run_count(&self) -> usize {
        self.runs.len()
    }

    pub fn peak_memory_bytes(&self) -> usize {
        self.peak_memory_bytes
    }

    /// Track the high-water mark of the ingest memory budget.
    pub fn observe_peak(&mut self, peak: usize) {
        self.peak_memory_bytes = self.peak_memory_bytes.max(peak);
    }

    /// Sort and persist a buffer as one run, then return the number of bytes
    /// the caller can release from the memory budget.  Cancellation is checked
    /// before the (non-interruptible) sort and before fsync-style finalization.
    pub fn write_run(&self, mut records: Vec<Record>, cancel: &Token) -> Result<(RunMeta, usize)> {
        cancel.check()?;
        records.sort_by(Record::sort_cmp);
        cancel.check()?;

        let released: usize = records.iter().map(Record::approx_bytes).sum();
        let id = self.run_counter.fetch_add(1, AtomicOrdering::SeqCst);
        let file_name = format!("run-{id:06}.bin");
        let path = self.dir.join(&file_name);
        let file = File::create(&path).map_err(|e| {
            Error::new(
                ErrorKind::SpillIo,
                format!("cannot create spill file {}: {e}", path.display()),
            )
        })?;
        let mut w = BufWriter::new(file);
        w.write_all(MAGIC).map_err(io_err)?;
        w.write_all(&[FORMAT_VERSION]).map_err(io_err)?;
        w.write_all(&(self.key_arity as u32).to_le_bytes())
            .map_err(io_err)?;
        for rec in &records {
            w.write_all(&rec.seq.to_le_bytes()).map_err(io_err)?;
            w.write_all(&rec.agg.to_le_bytes()).map_err(io_err)?;
            w.write_all(&[u8::from(rec.desc)]).map_err(io_err)?;
            for cell in &rec.key {
                write_cell(&mut w, cell).map_err(io_err)?;
            }
            write_cell(&mut w, &rec.value).map_err(io_err)?;
        }
        w.flush().map_err(io_err)?;
        drop(w);
        // Durability boundary: a cancellation arriving after this point still
        // finds a fully written run.
        cancel.check()?;
        Ok((
            RunMeta {
                file: file_name,
                rows: records.len() as u64,
            },
            released,
        ))
    }

    pub fn record_run(&mut self, meta: RunMeta) {
        self.runs.push(meta);
    }

    /// Persist the manifest.  `complete` indicates ingestion is finished and
    /// the merge phase may be (re)started.
    pub fn checkpoint(&mut self, complete: bool) -> Result<Manifest> {
        self.complete = complete;
        let manifest = Manifest {
            plan_hash: self.plan_hash.clone(),
            key_arity: self.key_arity,
            runs: self.runs.clone(),
            next_seq: self.next_seq,
            ingested_rows: self.ingested_rows,
            peak_memory_bytes: self.peak_memory_bytes,
            complete,
        };
        let path = Manifest::path_in(&self.dir);
        let json = serde_json::to_vec_pretty(&manifest).expect("manifest serializes");
        let tmp = self.dir.join("manifest.json.tmp");
        fs::write(&tmp, json).map_err(|e| {
            Error::new(
                ErrorKind::SpillIo,
                format!("cannot write manifest {}: {e}", tmp.display()),
            )
        })?;
        fs::rename(&tmp, &path).map_err(|e| {
            Error::new(
                ErrorKind::SpillIo,
                format!("cannot finalize manifest {}: {e}", path.display()),
            )
        })?;
        Ok(manifest)
    }

    pub fn resume_token(&self) -> ResumeToken {
        ResumeToken {
            dir: self
                .dir
                .file_name()
                .map(|s| s.to_string_lossy().into_owned())
                .unwrap_or_default(),
            plan_hash: self.plan_hash.clone(),
        }
    }

    /// Open a fresh k-way merge over every recorded run, polling cancellation
    /// every `cancel_check_rows` emitted records.
    pub fn open_merge(&self, cancel_check_rows: u64) -> Result<KWayMerge> {
        let interval = cancel_check_rows.max(1);
        let mut readers = Vec::with_capacity(self.runs.len());
        for meta in &self.runs {
            readers.push(RunReader::open(&self.dir.join(&meta.file), self.key_arity)?);
        }
        KWayMerge::new(readers, interval)
    }
}

fn read_manifest(dir: &Path) -> Result<Manifest> {
    let path = Manifest::path_in(dir);
    let bytes = fs::read(&path).map_err(|e| {
        Error::new(
            ErrorKind::InvalidResume,
            format!("cannot read resume manifest {}: {e}", path.display()),
        )
    })?;
    serde_json::from_slice(&bytes).map_err(|e| {
        Error::new(
            ErrorKind::InvalidResume,
            format!("resume manifest is corrupt: {e}"),
        )
    })
}

/// Opaque resumption handle returned to clients after a mid-merge cancellation.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ResumeToken {
    pub dir: String,
    pub plan_hash: String,
}

fn io_err(e: std::io::Error) -> Error {
    Error::new(ErrorKind::SpillIo, format!("spill I/O failure: {e}"))
}

/// Streams one sorted run file.
pub struct RunReader {
    r: BufReader<File>,
    key_arity: usize,
}

impl RunReader {
    fn open(path: &Path, expected_arity: usize) -> Result<Self> {
        let file = File::open(path).map_err(|e| {
            Error::new(
                ErrorKind::SpillIo,
                format!("cannot open spill run {}: {e}", path.display()),
            )
        })?;
        let mut r = BufReader::new(file);
        let mut magic = [0u8; 4];
        r.read_exact(&mut magic).map_err(io_err)?;
        if &magic != MAGIC {
            return Err(Error::new(
                ErrorKind::SpillIo,
                format!("spill run {} has a bad magic header", path.display()),
            ));
        }
        let mut version = [0u8; 1];
        r.read_exact(&mut version).map_err(io_err)?;
        if version[0] != FORMAT_VERSION {
            return Err(Error::new(
                ErrorKind::SpillIo,
                format!(
                    "spill run {} uses unsupported version {}",
                    path.display(),
                    version[0]
                ),
            ));
        }
        let mut arity_buf = [0u8; 4];
        r.read_exact(&mut arity_buf).map_err(io_err)?;
        let key_arity = u32::from_le_bytes(arity_buf) as usize;
        if key_arity != expected_arity {
            return Err(Error::new(
                ErrorKind::InvalidResume,
                format!(
                    "spill run {} stores {key_arity} key columns but plan expects {expected_arity}",
                    path.display()
                ),
            ));
        }
        Ok(Self { r, key_arity })
    }

    fn next_record(&mut self) -> Result<Option<Record>> {
        let mut seq_buf = [0u8; 8];
        match self.r.read_exact(&mut seq_buf) {
            Ok(()) => {}
            Err(e) if e.kind() == std::io::ErrorKind::UnexpectedEof => return Ok(None),
            Err(e) => return Err(io_err(e)),
        }
        let seq = u64::from_le_bytes(seq_buf);
        let mut agg_buf = [0u8; 4];
        self.r.read_exact(&mut agg_buf).map_err(io_err)?;
        let agg = u32::from_le_bytes(agg_buf);
        let mut desc_buf = [0u8; 1];
        self.r.read_exact(&mut desc_buf).map_err(io_err)?;
        let desc = desc_buf[0] != 0;
        let mut key = Vec::with_capacity(self.key_arity);
        for _ in 0..self.key_arity {
            key.push(read_cell(&mut self.r).map_err(io_err)?);
        }
        let value = read_cell(&mut self.r).map_err(io_err)?;
        Ok(Some(Record {
            key,
            agg,
            value,
            desc,
            seq,
        }))
    }
}

/// BinaryHeap entry: reversed ordering because Rust's heap is a max-heap.
struct HeapEntry {
    record: Record,
    reader_idx: usize,
}

impl PartialEq for HeapEntry {
    fn eq(&self, other: &Self) -> bool {
        self.record.sort_cmp(&other.record) == Ordering::Equal
    }
}
impl Eq for HeapEntry {}
impl Ord for HeapEntry {
    fn cmp(&self, other: &Self) -> Ordering {
        // Reverse so the smallest record has heap priority.
        other.record.sort_cmp(&self.record)
    }
}
impl PartialOrd for HeapEntry {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

/// Stable k-way merge over sorted run readers.
pub struct KWayMerge {
    readers: Vec<RunReader>,
    heap: BinaryHeap<HeapEntry>,
    emitted: u64,
    check_interval: u64,
}

impl KWayMerge {
    fn new(mut readers: Vec<RunReader>, check_interval: u64) -> Result<Self> {
        let mut heap = BinaryHeap::new();
        for (reader_idx, reader) in readers.iter_mut().enumerate() {
            if let Some(record) = reader.next_record()? {
                heap.push(HeapEntry { record, reader_idx });
            }
        }
        Ok(Self {
            readers,
            heap,
            emitted: 0,
            check_interval: check_interval.max(1),
        })
    }

    /// Pull the next globally-smallest record.  Cancellation is polled on a
    /// fixed cadence; the merge is read-only, so restarting it is always safe.
    pub fn next(&mut self, cancel: &Token) -> Result<Option<Record>> {
        if self.emitted.is_multiple_of(self.check_interval) {
            cancel.tick_merge()?;
        }
        let Some(HeapEntry { record, reader_idx }) = self.heap.pop() else {
            return Ok(None);
        };
        if let Some(next) = self.readers[reader_idx].next_record()? {
            self.heap.push(HeapEntry {
                record: next,
                reader_idx,
            });
        }
        self.emitted += 1;
        Ok(Some(record))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::exec::budget::MemoryBudget;
    use tempfile::tempdir;

    fn rec(key: i64, value: i64, seq: u64) -> Record {
        Record {
            key: vec![Cell::I64(key)],
            agg: 0,
            value: Cell::I64(value),
            desc: false,
            seq,
        }
    }

    #[test]
    fn kway_merge_is_sorted_and_stable() {
        let dir = tempdir().unwrap();
        let mut mgr = SpillManager::create(dir.path().to_path_buf(), "hash".into(), 1).unwrap();
        let cancel = Token::new();
        let budget = MemoryBudget::new(10_000);
        // Two runs with overlapping keys and equal values across runs: seq must
        // decide tie order.
        let run1 = vec![rec(1, 10, 0), rec(1, 10, 2), rec(2, 5, 3)];
        let run2 = vec![rec(1, 10, 1), rec(2, 5, 4)];
        for run in [run1, run2] {
            let bytes: usize = run.iter().map(Record::approx_bytes).sum();
            budget.try_grow(bytes).unwrap();
            let (meta, released) = mgr.write_run(run, &cancel).unwrap();
            budget.shrink(released);
            mgr.record_run(meta);
        }
        mgr.checkpoint(true).unwrap();

        let mut merge = mgr.open_merge(256).unwrap();
        let mut got = Vec::new();
        while let Some(r) = merge.next(&cancel).unwrap() {
            got.push((r.key[0].clone(), r.value.clone(), r.seq));
        }
        let expected: Vec<(Cell, Cell, u64)> = vec![
            (Cell::I64(1), Cell::I64(10), 0),
            (Cell::I64(1), Cell::I64(10), 1),
            (Cell::I64(1), Cell::I64(10), 2),
            (Cell::I64(2), Cell::I64(5), 3),
            (Cell::I64(2), Cell::I64(5), 4),
        ];
        assert_eq!(got, expected);
        assert_eq!(budget.used(), 0);
    }

    #[test]
    fn resume_rejects_plan_mismatch() {
        let dir = tempdir().unwrap();
        let mut mgr = SpillManager::create(dir.path().join("q1"), "aaa".into(), 1).unwrap();
        mgr.checkpoint(true).unwrap();
        let mut token = mgr.resume_token();
        token.plan_hash = "bbb".into();
        let err = SpillManager::resume(dir.path(), &token).unwrap_err();
        assert_eq!(err.kind, ErrorKind::InvalidResume);
    }

    #[test]
    fn resume_rejects_incomplete_manifest() {
        let dir = tempdir().unwrap();
        let mut mgr = SpillManager::create(dir.path().join("q1"), "aaa".into(), 1).unwrap();
        mgr.checkpoint(false).unwrap();
        let token = mgr.resume_token();
        drop(mgr);
        let err = SpillManager::resume(dir.path(), &token).unwrap_err();
        assert_eq!(err.kind, ErrorKind::InvalidResume);
    }
}
