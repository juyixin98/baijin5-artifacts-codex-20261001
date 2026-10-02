//! Snapshot collection from a local synthetic proc filesystem.
//!
//! Layout of one snapshot directory:
//!
//! ```text
//! <dir>/meta.json            {"seq": 1, "taken_at_ms": 1000}
//! <dir>/proc/<pid>/stat      "pid ppid start_gen utime stime rss_bytes state"
//! ```
//!
//! A stat file whose content is the literal `IOERR` simulates a partial read
//! failure for that PID (the directory existed but the read failed). Such PIDs
//! land in `Snapshot::read_failures` and must not be treated as exited.

use std::fmt;
use std::fs;
use std::path::{Path, PathBuf};

use crate::model::{Pid, ProcStat, ProcessIdentity, Snapshot, SnapshotMeta};

/// Sentinel content of a stat file that simulates a read failure.
pub const IOERR_SENTINEL: &str = "IOERR";

#[derive(Debug)]
pub enum SnapshotError {
    Io { path: PathBuf, source: std::io::Error },
    BadMeta { path: PathBuf, detail: String },
    BadStat { path: PathBuf, detail: String },
    NotASnapshotDir { path: PathBuf },
}

impl fmt::Display for SnapshotError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            SnapshotError::Io { path, source } => write!(f, "io error reading {}: {source}", path.display()),
            SnapshotError::BadMeta { path, detail } => write!(f, "bad meta.json at {}: {detail}", path.display()),
            SnapshotError::BadStat { path, detail } => write!(f, "bad stat at {}: {detail}", path.display()),
            SnapshotError::NotASnapshotDir { path } => write!(f, "{} is not a snapshot directory", path.display()),
        }
    }
}

impl std::error::Error for SnapshotError {}

/// Load a single snapshot directory.
pub fn load_snapshot(dir: &Path) -> Result<Snapshot, SnapshotError> {
    let meta_path = dir.join("meta.json");
    let meta_raw = fs::read_to_string(&meta_path).map_err(|e| SnapshotError::Io {
        path: meta_path.clone(),
        source: e,
    })?;
    let meta: SnapshotMeta =
        serde_json::from_str(&meta_raw).map_err(|e| SnapshotError::BadMeta {
            path: meta_path.clone(),
            detail: e.to_string(),
        })?;

    let proc_dir = dir.join("proc");
    let entries = fs::read_dir(&proc_dir).map_err(|e| SnapshotError::Io {
        path: proc_dir.clone(),
        source: e,
    })?;

    let mut procs = Vec::new();
    let mut read_failures = Vec::new();
    for entry in entries {
        let entry = entry.map_err(|e| SnapshotError::Io {
            path: proc_dir.clone(),
            source: e,
        })?;
        let pid_dir = entry.path();
        let Some(pid) = pid_dir
            .file_name()
            .and_then(|n| n.to_str())
            .and_then(|s| s.parse::<Pid>().ok())
        else {
            continue; // ignore non-PID entries
        };
        let stat_path = pid_dir.join("stat");
        let raw = fs::read_to_string(&stat_path).map_err(|e| SnapshotError::Io {
            path: stat_path.clone(),
            source: e,
        })?;
        let trimmed = raw.trim();
        if trimmed == IOERR_SENTINEL {
            read_failures.push(pid);
            continue;
        }
        procs.push(parse_stat(trimmed, &stat_path)?);
    }

    // Deterministic ordering: by pid, and read failures sorted.
    procs.sort_by_key(|p| p.identity.pid);
    read_failures.sort_unstable();

    Ok(Snapshot { meta, procs, read_failures })
}

/// Parse one synthetic stat line: `pid ppid start_gen utime stime rss_bytes state`.
fn parse_stat(line: &str, path: &Path) -> Result<ProcStat, SnapshotError> {
    let bad = |detail: &str| SnapshotError::BadStat {
        path: path.to_path_buf(),
        detail: format!("{detail}: {line:?}"),
    };
    let fields: Vec<&str> = line.split_whitespace().collect();
    if fields.len() != 7 {
        return Err(bad("expected 7 whitespace-separated fields"));
    }
    let num = |i: usize| fields[i].parse::<u64>().map_err(|_| bad("non-numeric field"));
    Ok(ProcStat {
        identity: ProcessIdentity {
            pid: num(0)? as Pid,
            start_generation: num(2)?,
        },
        ppid: num(1)? as Pid,
        utime: num(3)?,
        stime: num(4)?,
        rss_bytes: num(5)?,
        state: fields[6].to_string(),
    })
}

/// Load a series of snapshot directories (`<root>/snapshots/<NNNN>/`) in
/// directory-name order. Used by fixtures and by offline replay.
pub fn load_series(root: &Path) -> Result<Vec<Snapshot>, SnapshotError> {
    let snaps_dir = root.join("snapshots");
    let entries = fs::read_dir(&snaps_dir).map_err(|_| SnapshotError::NotASnapshotDir {
        path: snaps_dir.clone(),
    })?;
    let mut dirs: Vec<PathBuf> = entries
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| p.is_dir())
        .collect();
    dirs.sort();
    dirs.iter().map(|d| load_snapshot(d)).collect()
}
