//! Spill storage: length-prefixed row-key frames with multiplicity and an
//! end-of-frame checksum.
//!
//! Frame layout (all integers little-endian):
//! ```text
//! magic     : 10 bytes  b"SETOPSFRM1"
//! records   : repeated { u32 key_len, key bytes, u64 multiplicity }
//! trailer   : u64 FNV-1a checksum over magic+records bytes, u64 record count
//! ```
//! A frame that fails its checksum, truncates, or claims a wrong count is
//! rejected as `state_conflict/spill_corrupted` — never silently accepted.

use std::fs::{File, OpenOptions};
use std::io::{BufReader, BufWriter, Read, Write};
use std::path::{Path, PathBuf};

use crate::batch::encode::{RowKey, canonical_hash};
use crate::error::{ResourceCode, Result, SetOpsError, StateCode};
use crate::resource::ResourceLimits;

const MAGIC: &[u8; 10] = b"SETOPSFRM1";

/// Owns one run's spill directory and its byte budget.
pub(crate) struct SpillManager {
    root: PathBuf,
    written_bytes: u64,
    budget: u64,
}

impl SpillManager {
    pub(crate) fn new(root: impl Into<PathBuf>, limits: &ResourceLimits) -> Result<Self> {
        let root = root.into();
        std::fs::create_dir_all(&root).map_err(|e| {
            SetOpsError::resource(
                ResourceCode::SpillIo,
                format!("cannot create spill dir {}: {e}", root.display()),
            )
        })?;
        Ok(Self {
            root,
            written_bytes: 0,
            budget: limits.spill_bytes,
        })
    }

    pub(crate) fn written_bytes(&self) -> u64 {
        self.written_bytes
    }

    fn part_path(&self, side: &str, partition_id: &str, part: usize) -> PathBuf {
        self.root
            .join(format!("{side}.{partition_id}.part{part:03}.frame"))
    }

    fn charge(&mut self, n: u64) -> Result<()> {
        self.written_bytes = self.written_bytes.saturating_add(n);
        if self.written_bytes > self.budget {
            return Err(SetOpsError::resource(
                ResourceCode::SpillIo,
                format!(
                    "spill budget of {} bytes exceeded (used {})",
                    self.budget, self.written_bytes
                ),
            ));
        }
        Ok(())
    }

    /// Begin writing a numbered part file for one (side, partition chain).
    /// `partition_id` is the full recursion path, e.g. `p03` or `p03.p01`.
    pub(crate) fn begin_part(
        &mut self,
        side: &str,
        partition_id: &str,
        part: usize,
    ) -> Result<FrameWriter> {
        let path = self.part_path(side, partition_id, part);
        FrameWriter::create(&path)
    }

    /// Persist one record through an open writer and charge its bytes.
    pub(crate) fn write_record(
        &mut self,
        w: &mut FrameWriter,
        key: &RowKey,
        mult: u64,
    ) -> Result<()> {
        w.write_record(key, mult)?;
        // record = 4 + key_len + 8
        self.charge((12 + key.bytes().len()) as u64)
    }

    /// Remove part files after a partition pair has been processed.
    pub(crate) fn cleanup_parts(&self, parts: &[SpillPart]) {
        for p in parts {
            let _ = std::fs::remove_file(&p.path);
        }
    }
}

#[derive(Debug, Clone)]
pub(crate) struct SpillPart {
    pub(crate) path: PathBuf,
    /// Sum of canonical key bytes in this frame (distinct keys only).
    /// Lets the engine decide resident fit without opening the frame.
    pub(crate) key_bytes: usize,
    pub(crate) partition_id: String,
}

pub(crate) struct FrameWriter {
    path: PathBuf,
    w: BufWriter<File>,
    hasher: u64,
    count: u64,
}

impl FrameWriter {
    fn create(path: &Path) -> Result<Self> {
        let file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(path)
            .map_err(|e| {
                SetOpsError::resource(
                    ResourceCode::SpillIo,
                    format!("cannot create spill frame {}: {e}", path.display()),
                )
            })?;
        let mut w = BufWriter::new(file);
        w.write_all(MAGIC).map_err(frame_io)?;
        Ok(Self {
            path: path.to_path_buf(),
            w,
            hasher: fnv_continue(canonical_hash(b""), MAGIC),
            count: 0,
        })
    }

    fn write_record(&mut self, key: &RowKey, mult: u64) -> Result<()> {
        let len = u32::try_from(key.bytes().len()).map_err(|_| {
            SetOpsError::resource(ResourceCode::SpillIo, "single row key exceeds 4GiB")
        })?;
        self.w.write_all(&len.to_le_bytes()).map_err(frame_io)?;
        self.w.write_all(key.bytes()).map_err(frame_io)?;
        self.w.write_all(&mult.to_le_bytes()).map_err(frame_io)?;
        self.hasher = fnv_continue(self.hasher, &len.to_le_bytes());
        self.hasher = fnv_continue(self.hasher, key.bytes());
        self.hasher = fnv_continue(self.hasher, &mult.to_le_bytes());
        self.count += 1;
        Ok(())
    }

    pub(crate) fn finish(mut self) -> Result<(PathBuf, u64)> {
        // End-of-records marker (u32 zero) followed by the trailer.
        self.w.write_all(&0u32.to_le_bytes()).map_err(frame_io)?;
        self.w
            .write_all(&self.hasher.to_le_bytes())
            .map_err(frame_io)?;
        self.w
            .write_all(&self.count.to_le_bytes())
            .map_err(frame_io)?;
        self.w.flush().map_err(frame_io)?;
        Ok((self.path, self.count))
    }
}

/// Iterator over a frame's `(RowKey, multiplicity)` records.
pub(crate) struct FrameReader {
    r: BufReader<File>,
    seen: u64,
    hasher: u64,
}

impl FrameReader {
    pub(crate) fn open(path: &Path) -> Result<Self> {
        let file = File::open(path).map_err(|e| {
            SetOpsError::resource(
                ResourceCode::SpillIo,
                format!("cannot reopen spill frame {}: {e}", path.display()),
            )
        })?;
        let mut r = BufReader::new(file);
        let mut magic = [0u8; 10];
        read_exact(&mut r, &mut magic).map_err(|_| corrupted("missing/short magic"))?;
        if &magic != MAGIC {
            return Err(corrupted("bad magic"));
        }
        Ok(Self {
            r,
            seen: 0,
            hasher: fnv_continue(canonical_hash(b""), &magic),
        })
    }

    fn next_record(&mut self) -> Result<Option<(RowKey, u64)>> {
        let mut lenb = [0u8; 4];
        match read_fill(&mut self.r, &mut lenb).map_err(frame_io)? {
            0 => return Err(corrupted("missing end marker and trailer")),
            n if n < 4 => return Err(corrupted("truncated record length")),
            _ => {}
        }
        // Zero length marks end of records; the trailer follows.
        if lenb == [0, 0, 0, 0] {
            let mut checksum = [0u8; 8];
            let mut countb = [0u8; 8];
            read_exact(&mut self.r, &mut checksum).map_err(|_| corrupted("missing checksum"))?;
            read_exact(&mut self.r, &mut countb).map_err(|_| corrupted("missing count"))?;
            let stored = u64::from_le_bytes(checksum);
            let expected = u64::from_le_bytes(countb);
            if stored != self.hasher {
                return Err(corrupted("checksum mismatch"));
            }
            if expected != self.seen {
                return Err(corrupted(format!(
                    "record count mismatch: trailer says {expected}, frame holds {}",
                    self.seen
                )));
            }
            // Nothing should remain after the trailer.
            if self.r.read(&mut [0u8; 1]).map_err(frame_io)? != 0 {
                return Err(corrupted("trailing bytes after trailer"));
            }
            return Ok(None);
        }
        self.hasher = fnv_continue(self.hasher, &lenb);
        let len = u32::from_le_bytes(lenb) as usize;
        // Structural sanity bound: no single row key can exceed 1 GiB.
        if len > 1 << 30 {
            return Err(corrupted("implausible record length"));
        }
        let mut kb = vec![0u8; len];
        read_exact(&mut self.r, &mut kb).map_err(|_| corrupted("truncated key"))?;
        let mut mb = [0u8; 8];
        read_exact(&mut self.r, &mut mb).map_err(|_| corrupted("truncated multiplicity"))?;
        self.hasher = fnv_continue(self.hasher, &kb);
        self.hasher = fnv_continue(self.hasher, &mb);
        let mult = u64::from_le_bytes(mb);
        self.seen += 1;
        let hash = canonical_hash(&kb);
        Ok(Some((RowKey::from_parts(kb, hash), mult)))
    }
}

impl Iterator for FrameReader {
    type Item = Result<(RowKey, u64)>;
    fn next(&mut self) -> Option<Self::Item> {
        self.next_record().transpose()
    }
}

/// Fill `buf` fully from `r`, looping across short reads. Returns the number of
/// bytes read (0 only at a clean EOF).
fn read_fill(r: &mut impl Read, buf: &mut [u8]) -> std::io::Result<usize> {
    let mut filled = 0;
    while filled < buf.len() {
        match r.read(&mut buf[filled..]) {
            Ok(0) => break,
            Ok(n) => filled += n,
            Err(e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
            Err(e) => return Err(e),
        }
    }
    Ok(filled)
}

fn read_exact(r: &mut impl Read, buf: &mut [u8]) -> std::io::Result<()> {
    let n = read_fill(r, buf)?;
    if n != buf.len() {
        Err(std::io::Error::new(
            std::io::ErrorKind::UnexpectedEof,
            format!("read {n} of {} bytes", buf.len()),
        ))
    } else {
        Ok(())
    }
}

fn frame_io(e: std::io::Error) -> SetOpsError {
    SetOpsError::resource(ResourceCode::SpillIo, format!("spill frame IO failed: {e}"))
}

fn corrupted(msg: impl Into<String>) -> SetOpsError {
    SetOpsError::state(
        StateCode::SpillCorrupted,
        format!("corrupt spill frame: {}", msg.into()),
    )
}

/// FNV-1a continue (same constants as row hashing).
fn fnv_continue(mut h: u64, bytes: &[u8]) -> u64 {
    for &b in bytes {
        h ^= b as u64;
        h = h.wrapping_mul(0x0000_0100_0000_01b3);
    }
    h
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::batch::encode::row_key_for_test;
    use tempfile::tempdir;

    #[test]
    fn frame_roundtrip_with_multiplicity() -> std::result::Result<(), Box<dyn std::error::Error>> {
        let dir = tempdir().unwrap();
        let limits = ResourceLimits::default();
        let mut mgr = SpillManager::new(dir.path(), &limits).unwrap();
        let k1 = row_key_for_test(vec![1, 0, 0, 0, 0, 0, 0, 7]);
        let k2 = row_key_for_test(vec![4, 0, 0, 3]);
        let mut w = mgr.begin_part("L", "p01", 0).unwrap();
        mgr.write_record(&mut w, &k1, 3).unwrap();
        mgr.write_record(&mut w, &k2, 9).unwrap();
        let (path, n) = w.finish().unwrap();
        assert_eq!(n, 2);
        let part = SpillPart {
            path,
            key_bytes: k1.bytes().len() + k2.bytes().len(),
            partition_id: "p01".into(),
        };
        let got: Vec<_> = FrameReader::open(&part.path)
            .unwrap()
            .map(|x| x.unwrap())
            .collect();
        assert_eq!(got.len(), 2);
        assert_eq!(got[0].1, 3);
        assert_eq!(got[1].1, 9);
        Ok(())
    }

    #[test]
    fn detects_corrupted_frame() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("x.frame");
        std::fs::write(&path, b"SETOPSFRM1garbage-not-a-real-trailer").unwrap();
        let err = FrameReader::open(&path)
            .unwrap()
            .find_map(|r| r.err())
            .expect("error");
        assert!(matches!(
            err.kind,
            crate::error::ErrorKind::StateConflict(StateCode::SpillCorrupted)
        ));
    }
}
