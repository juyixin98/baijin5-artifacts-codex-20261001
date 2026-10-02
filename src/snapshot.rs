//! Filesystem loader for synthetic /proc snapshots.
//!
//! A snapshot root directory looks like:
//!
//! ```text
//! <root>/<seq>/boot_id          # single line, boot generation identifier
//! <root>/<seq>/proc/<pid>/stat  # /proc/<pid>/stat-formatted record
//! ```
//!
//! A `<pid>` directory whose `stat` file is missing or unparsable is a
//! *partial read failure*: the pid is recorded in `Snapshot::read_failures`
//! instead of being silently dropped, so the engine will not mistake it for
//! an exited process.

use crate::model::{Pid, ProcStat, Seq, Snapshot};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

#[derive(Debug, thiserror::Error)]
pub enum SnapshotError {
    #[error("snapshot directory {0} does not exist")]
    MissingDir(PathBuf),
    #[error("snapshot directory name {0} is not a sequence number")]
    BadSeq(String),
    #[error("snapshot {0} has no proc/ subdirectory")]
    NoProcDir(PathBuf),
    #[error("cannot read {path}: {source}")]
    Io {
        path: PathBuf,
        #[source]
        source: std::io::Error,
    },
    #[error("malformed stat record: {0}")]
    MalformedStat(String),
}

/// Parse a `/proc/<pid>/stat`-formatted line.
///
/// Field positions follow proc(5): after the parenthesised comm, fields are
/// state(3) ppid(4) ... utime(14) stime(15) ... starttime(22) vsize(23)
/// rss(24) — i.e. indices 0, 1, 11, 12, 19, 21 of the remaining tokens.
pub fn parse_stat(content: &str) -> Result<ProcStat, SnapshotError> {
    let malformed = |msg: &str| SnapshotError::MalformedStat(msg.to_string());
    let open = content.find('(').ok_or_else(|| malformed("missing '('"))?;
    let close = content.rfind(')').ok_or_else(|| malformed("missing ')'"))?;
    if close < open {
        return Err(malformed("')' before '('"));
    }
    let pid: Pid = content[..open]
        .trim()
        .parse()
        .map_err(|_| malformed("bad pid field"))?;
    let comm = content[open + 1..close].to_string();
    let rest: Vec<&str> = content[close + 1..].split_whitespace().collect();
    if rest.len() < 22 {
        return Err(malformed("too few fields after comm"));
    }
    let num = |idx: usize, name: &str| -> Result<u64, SnapshotError> {
        rest[idx]
            .parse()
            .map_err(|_| malformed(name))
    };
    let state = rest[0]
        .chars()
        .next()
        .ok_or_else(|| malformed("empty state field"))?;
    let ppid: Pid = rest[1].parse().map_err(|_| malformed("bad ppid field"))?;
    let rss_pages: i64 = rest[21].parse().map_err(|_| malformed("bad rss field"))?;
    Ok(ProcStat {
        pid,
        comm,
        state,
        ppid,
        utime: num(11, "bad utime field")?,
        stime: num(12, "bad stime field")?,
        start_time: num(19, "bad starttime field")?,
        rss_pages,
    })
}

fn read_to_string(path: &Path) -> Result<String, SnapshotError> {
    std::fs::read_to_string(path).map_err(|source| SnapshotError::Io {
        path: path.to_path_buf(),
        source,
    })
}

/// Load one snapshot directory (`<seq>/` containing `boot_id` and `proc/`).
pub fn load_snapshot(dir: &Path) -> Result<Snapshot, SnapshotError> {
    if !dir.is_dir() {
        return Err(SnapshotError::MissingDir(dir.to_path_buf()));
    }
    let seq_name = dir
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_default();
    let seq: Seq = seq_name
        .parse()
        .map_err(|_| SnapshotError::BadSeq(seq_name.clone()))?;
    let proc_dir = dir.join("proc");
    if !proc_dir.is_dir() {
        return Err(SnapshotError::NoProcDir(dir.to_path_buf()));
    }
    let boot_id = read_to_string(&dir.join("boot_id"))?.trim().to_string();

    let mut procs = BTreeMap::new();
    let mut read_failures = BTreeMap::new();
    let entries = std::fs::read_dir(&proc_dir).map_err(|source| SnapshotError::Io {
        path: proc_dir.clone(),
        source,
    })?;
    for entry in entries {
        let entry = entry.map_err(|source| SnapshotError::Io {
            path: proc_dir.clone(),
            source,
        })?;
        let name = entry.file_name().to_string_lossy().into_owned();
        let Ok(pid) = name.parse::<Pid>() else {
            continue; // non-pid entries (e.g. sys/, net/) are ignored
        };
        match std::fs::read_to_string(entry.path().join("stat")) {
            Ok(content) => match parse_stat(&content) {
                Ok(stat) if stat.pid == pid => {
                    procs.insert(pid, stat);
                }
                Ok(_) => {
                    read_failures.insert(pid, "pid-mismatch".to_string());
                }
                Err(_) => {
                    read_failures.insert(pid, "malformed-stat".to_string());
                }
            },
            Err(_) => {
                read_failures.insert(pid, "stat-unreadable".to_string());
            }
        }
    }
    Ok(Snapshot {
        seq,
        boot_id,
        procs,
        read_failures,
    })
}

/// List snapshot directories under `root` as `(seq, path)` pairs, sorted by
/// sequence number. Callers detect gaps by comparing adjacent seq numbers.
pub fn list_snapshots(root: &Path) -> Result<Vec<(Seq, PathBuf)>, SnapshotError> {
    if !root.is_dir() {
        return Err(SnapshotError::MissingDir(root.to_path_buf()));
    }
    let entries = std::fs::read_dir(root).map_err(|source| SnapshotError::Io {
        path: root.to_path_buf(),
        source,
    })?;
    let mut out = Vec::new();
    for entry in entries {
        let entry = entry.map_err(|source| SnapshotError::Io {
            path: root.to_path_buf(),
            source,
        })?;
        let path = entry.path();
        if !path.is_dir() {
            continue;
        }
        let name = entry.file_name().to_string_lossy().into_owned();
        if let Ok(seq) = name.parse::<Seq>() {
            if path.join("proc").is_dir() {
                out.push((seq, path));
            }
        }
    }
    out.sort_by_key(|(seq, _)| *seq);
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_standard_stat_layout() {
        let line = "2000 (worker) S 1 0 0 0 0 0 0 0 0 0 25 5 0 0 20 0 1 0 100 1048576 42";
        let stat = parse_stat(line).expect("valid stat");
        assert_eq!(stat.pid, 2000);
        assert_eq!(stat.comm, "worker");
        assert_eq!(stat.state, 'S');
        assert_eq!(stat.ppid, 1);
        assert_eq!(stat.utime, 25);
        assert_eq!(stat.stime, 5);
        assert_eq!(stat.start_time, 100);
        assert_eq!(stat.rss_pages, 42);
        assert_eq!(stat.cpu_total(), 30);
    }

    #[test]
    fn rejects_garbage() {
        assert!(parse_stat("not a stat line").is_err());
        assert!(parse_stat("1 (x) S 1").is_err());
    }

    #[test]
    fn comm_with_spaces_and_parens() {
        let line = "7 (my worker (x)) R 1 0 0 0 0 0 0 0 0 0 1 2 0 0 20 0 1 0 9 100 3";
        let stat = parse_stat(line).expect("valid stat");
        assert_eq!(stat.comm, "my worker (x)");
        assert_eq!(stat.utime, 1);
        assert_eq!(stat.stime, 2);
    }
}
