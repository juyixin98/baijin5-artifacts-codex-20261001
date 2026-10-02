use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::fmt;

/// OS process id (only unique together with boot id + start time).
pub type Pid = u32;
/// Monotonic snapshot sequence number (from snapshot directory name).
pub type Seq = u64;

/// Process identity. A PID alone is NOT an identity: the kernel may reuse a
/// PID after the previous owner exits. Identity includes the boot generation
/// (`boot_id`) and the process start time (`start_time`, jiffies since boot),
/// so a reused PID never inherits the previous generation's counters.
#[derive(Clone, Debug, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
pub struct ProcessIdentity {
    pub boot_id: String,
    pub pid: Pid,
    pub start_time: u64,
}

impl fmt::Display for ProcessIdentity {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "pid={} start={} boot={}", self.pid, self.start_time, self.boot_id)
    }
}

/// Parsed subset of a synthetic `/proc/<pid>/stat` record.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProcStat {
    pub pid: Pid,
    pub comm: String,
    pub state: char,
    pub ppid: Pid,
    pub utime: u64,
    pub stime: u64,
    pub start_time: u64,
    pub rss_pages: i64,
}

impl ProcStat {
    /// Cumulative CPU consumed by this process, in jiffies.
    pub fn cpu_total(&self) -> u64 {
        self.utime + self.stime
    }

    pub fn identity(&self, boot_id: &str) -> ProcessIdentity {
        ProcessIdentity {
            boot_id: boot_id.to_string(),
            pid: self.pid,
            start_time: self.start_time,
        }
    }
}

/// One sampled snapshot of a synthetic /proc tree.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Snapshot {
    /// Sequence number taken from the snapshot directory name.
    pub seq: Seq,
    /// Boot generation this snapshot belongs to.
    pub boot_id: String,
    /// Successfully parsed processes, keyed by pid.
    pub procs: BTreeMap<Pid, ProcStat>,
    /// Pids whose directory existed but whose `stat` could not be read or
    /// parsed (partial read failure). Such processes must NOT be treated as
    /// exited. Value is a short, non-sensitive error kind.
    pub read_failures: BTreeMap<Pid, String>,
}
