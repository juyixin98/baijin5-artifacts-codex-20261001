//! On-disk run codec for external sorting.
//!
//! A spilled *run* is a sequence of length-delimited, already-sorted records:
//!
//! ```text
//! tag:u8  ordinal:u64le  payload
//!   tag 1 (i64)  : value:i64le
//!   tag 2 (f64)  : bits:u64le            (total-order bit patterns preserved)
//!   tag 3 (utf8) : byte_len:u32le bytes
//! ```
//!
//! The format is local, versioned by the leading magic, and free of any
//! external service dependency. Records are keyed by (group, operator) through
//! the run manifest, so group keys are never stored redundantly.

use std::io::{self, BufReader, BufWriter, Read, Write};

use crate::error::{PctlError, Result};
use crate::operator::SortKey;

pub const RUN_MAGIC: u32 = 0x50_53_52_31; // "PSR1" pctl sort run v1

/// One sort record: measure value plus the global, stable ingest ordinal.
#[derive(Debug, Clone, PartialEq)]
pub struct Entry {
    pub key: SortKey,
    pub ordinal: u64,
}

impl Entry {
    /// Comparator used both when sorting an in-memory run and when merging.
    /// Equal keys keep their ingest order (`ordinal` ascending) regardless of
    /// the requested direction — ties are always stable.
    pub fn compare(&self, other: &Entry, ascending: bool) -> std::cmp::Ordering {
        use std::cmp::Ordering;
        let key_ord = self.key.cmp_asc(&other.key);
        let key_ord = if ascending {
            key_ord
        } else {
            key_ord.reverse()
        };
        if key_ord != Ordering::Equal {
            return key_ord;
        }
        self.ordinal.cmp(&other.ordinal)
    }

    /// Resident size charged against the query budget. The fixed term covers
    /// the `Entry`/`String`/Vec-capacity overhead; the variable term covers
    /// actual UTF-8 payload bytes so string-heavy groups cannot hide memory.
    pub fn charged_bytes(&self) -> usize {
        const ENTRY_OVERHEAD: usize = 56;
        ENTRY_OVERHEAD
            + match &self.key {
                SortKey::Utf8(s) => s.len(),
                _ => 0,
            }
    }
}

pub struct RunWriter<W: Write> {
    inner: BufWriter<W>,
}

impl RunWriter<std::fs::File> {
    pub fn create(path: &std::path::Path) -> Result<Self> {
        let file = std::fs::File::create(path)?;
        let mut w = BufWriter::new(file);
        w.write_all(&RUN_MAGIC.to_le_bytes())?;
        Ok(Self { inner: w })
    }
}

impl<W: Write> RunWriter<W> {
    pub fn write_entry(&mut self, e: &Entry) -> Result<()> {
        match &e.key {
            SortKey::I64(v) => {
                self.inner.write_all(&[1u8])?;
                self.inner.write_all(&e.ordinal.to_le_bytes())?;
                self.inner.write_all(&v.to_le_bytes())?;
            }
            SortKey::F64(v) => {
                self.inner.write_all(&[2u8])?;
                self.inner.write_all(&e.ordinal.to_le_bytes())?;
                self.inner.write_all(&v.to_bits().to_le_bytes())?;
            }
            SortKey::Utf8(s) => {
                self.inner.write_all(&[3u8])?;
                self.inner.write_all(&e.ordinal.to_le_bytes())?;
                let bytes = s.as_bytes();
                let len = u32::try_from(bytes.len()).map_err(|_| {
                    PctlError::new(
                        crate::error::ErrorKind::Validation,
                        "string_too_long",
                        format!("run record exceeds 4 GiB: {} bytes", bytes.len()),
                    )
                })?;
                self.inner.write_all(&len.to_le_bytes())?;
                self.inner.write_all(bytes)?;
            }
        }
        Ok(())
    }

    pub fn finish(mut self) -> Result<()> {
        self.inner.flush()?;
        Ok(())
    }
}

pub struct RunReader<R: Read> {
    inner: BufReader<R>,
}

impl RunReader<std::fs::File> {
    pub fn open(path: &std::path::Path) -> Result<Self> {
        let file = std::fs::File::open(path)?;
        let mut r = BufReader::new(file);
        let mut magic = [0u8; 4];
        read_exact_or_eof(&mut r, &mut magic)?;
        if u32::from_le_bytes(magic) != RUN_MAGIC {
            return Err(PctlError::new(
                crate::error::ErrorKind::Resource,
                "bad_run_magic",
                format!("spill file {} is not a pctl run v1", path.display()),
            ));
        }
        Ok(Self { inner: r })
    }
}

impl<R: Read> RunReader<R> {
    pub fn next_entry(&mut self) -> Result<Option<Entry>> {
        let mut tag = [0u8; 1];
        match read_exact_opt(&mut self.inner, &mut tag)? {
            ReadOutcome::Eof => return Ok(None),
            ReadOutcome::Ok => {}
        }
        let ordinal = read_u64(&mut self.inner)?;
        let key = match tag[0] {
            1 => SortKey::I64(read_i64(&mut self.inner)?),
            2 => SortKey::F64(f64::from_bits(read_u64(&mut self.inner)?)),
            3 => {
                let len = read_u32(&mut self.inner)? as usize;
                let mut buf = vec![0u8; len];
                self.inner.read_exact(&mut buf)?;
                let s = String::from_utf8(buf).map_err(|_| {
                    PctlError::new(
                        crate::error::ErrorKind::Resource,
                        "bad_run_utf8",
                        "spilled string record is not valid UTF-8",
                    )
                })?;
                SortKey::Utf8(s)
            }
            other => {
                return Err(PctlError::new(
                    crate::error::ErrorKind::Resource,
                    "bad_run_tag",
                    format!("unknown record tag {other} in spill file"),
                ))
            }
        };
        Ok(Some(Entry { key, ordinal }))
    }
}

enum ReadOutcome {
    Ok,
    Eof,
}

fn read_exact_or_eof<R: Read>(r: &mut R, buf: &mut [u8]) -> Result<()> {
    match read_exact_opt(r, buf)? {
        ReadOutcome::Ok => Ok(()),
        ReadOutcome::Eof => {
            Err(io::Error::new(io::ErrorKind::UnexpectedEof, "truncated run header").into())
        }
    }
}

/// Like `Read::read_exact` but distinguishes a clean EOF before any byte.
fn read_exact_opt<R: Read>(r: &mut R, buf: &mut [u8]) -> Result<ReadOutcome> {
    let mut filled = 0;
    while filled < buf.len() {
        match r.read(&mut buf[filled..]) {
            Ok(0) => {
                return if filled == 0 {
                    Ok(ReadOutcome::Eof)
                } else {
                    Err(io::Error::new(io::ErrorKind::UnexpectedEof, "truncated run record").into())
                }
            }
            Ok(n) => filled += n,
            Err(e) if e.kind() == io::ErrorKind::Interrupted => continue,
            Err(e) => return Err(e.into()),
        }
    }
    Ok(ReadOutcome::Ok)
}

fn read_u64<R: Read>(r: &mut R) -> Result<u64> {
    let mut b = [0u8; 8];
    r.read_exact(&mut b)?;
    Ok(u64::from_le_bytes(b))
}

fn read_i64<R: Read>(r: &mut R) -> Result<i64> {
    let mut b = [0u8; 8];
    r.read_exact(&mut b)?;
    Ok(i64::from_le_bytes(b))
}

fn read_u32<R: Read>(r: &mut R) -> Result<u32> {
    let mut b = [0u8; 4];
    r.read_exact(&mut b)?;
    Ok(u32::from_le_bytes(b))
}
