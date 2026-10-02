//! Run model: core types describing processes, snapshots, and the events the
//! diff engine emits. These types are the shared vocabulary of the service;
//! they contain no behaviour beyond (de)serialization.

use serde::{Deserialize, Serialize};

pub type Pid = u32;
pub type Seq = u64;

/// A process identity is the PID plus the start generation (analogous to the
/// kernel's start-time tick). A reused PID with a new generation is a *new*
/// process and must never inherit the old process's counters.
#[derive(Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize, Debug)]
pub struct ProcessIdentity {
    pub pid: Pid,
    pub start_generation: u64,
}

/// One row of a synthetic proc stat file.
#[derive(Clone, PartialEq, Eq, Serialize, Deserialize, Debug)]
pub struct ProcStat {
    pub identity: ProcessIdentity,
    pub ppid: Pid,
    pub utime: u64,
    pub stime: u64,
    pub rss_bytes: u64,
    pub state: String,
}

impl ProcStat {
    pub fn cpu_total(&self) -> u64 {
        self.utime + self.stime
    }
}

#[derive(Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Debug)]
pub struct SnapshotMeta {
    pub seq: Seq,
    pub taken_at_ms: u64,
}

/// A collected snapshot. `read_failures` lists PIDs whose stat file existed
/// but could not be parsed/read: those processes must NOT be treated as gone.
#[derive(Clone, PartialEq, Eq, Serialize, Deserialize, Debug)]
pub struct Snapshot {
    pub meta: SnapshotMeta,
    pub procs: Vec<ProcStat>,
    pub read_failures: Vec<Pid>,
}

/// Classification of a per-process CPU delta between two observations.
#[derive(Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Debug)]
#[serde(rename_all = "snake_case")]
pub enum DeltaCategory {
    /// First time we see this identity; no delta can be computed.
    FirstObservation,
    /// PID was reused with a new generation; old counters are not inherited.
    IdentityReset,
    /// Normal monotonic advance.
    Ok,
    /// Counter went backwards but the wraparound delta is plausible.
    CounterWrap,
    /// Counter went backwards and wraparound is implausible: data anomaly.
    CounterAnomaly,
    /// Delta spans a sampling gap (missed snapshots between the two points).
    SamplingGap,
}

/// A CPU delta for one identity between two observations.
/// `delta == None` means the interval is undeterminable.
#[derive(Clone, PartialEq, Eq, Serialize, Deserialize, Debug)]
pub struct CpuDelta {
    pub identity: ProcessIdentity,
    pub from_seq: Option<Seq>,
    pub to_seq: Seq,
    pub at_ms: u64,
    pub delta: Option<u64>,
    pub category: DeltaCategory,
    #[serde(default)]
    pub reason: String,
}

#[derive(Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Debug)]
#[serde(rename_all = "snake_case")]
pub enum ExitReason {
    /// Absent from a fully-read snapshot.
    Exited,
    /// Same PID reappeared with a new start generation.
    PidReused,
}

#[derive(Clone, PartialEq, Eq, Serialize, Deserialize, Debug)]
pub struct ExitEvent {
    pub identity: ProcessIdentity,
    pub seq: Seq,
    pub at_ms: u64,
    pub reason: ExitReason,
}

/// Re-parenting (e.g. orphan re-attached to init). The time relation is
/// preserved via `seq`/`at_ms` so consumers can order it against exits.
#[derive(Clone, PartialEq, Eq, Serialize, Deserialize, Debug)]
pub struct ReparentEvent {
    pub identity: ProcessIdentity,
    pub old_ppid: Pid,
    pub new_ppid: Pid,
    pub seq: Seq,
    pub at_ms: u64,
}

/// Why an interval cannot be determined.
#[derive(Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Debug)]
#[serde(rename_all = "snake_case")]
pub enum FailureCategory {
    ReadFailure,
    SamplingGap,
    CounterAnomaly,
}

/// An interval the engine cannot fully determine, with the reason.
#[derive(Clone, PartialEq, Eq, Serialize, Deserialize, Debug)]
pub struct UndeterminedInterval {
    pub identity: ProcessIdentity,
    pub from_seq: Seq,
    pub to_seq: Seq,
    pub category: FailureCategory,
    /// True when a cumulative delta is still exact (e.g. sampling gap);
    /// false when the delta itself is unknown (read failure, anomaly).
    pub delta_known: bool,
    #[serde(default)]
    pub reason: String,
}

/// Serializable view of the current process forest plus re-parent history.
#[derive(Clone, PartialEq, Eq, Serialize, Deserialize, Debug)]
pub struct TreeView {
    pub nodes: Vec<TreeNode>,
    pub reparents: Vec<ReparentEvent>,
}

#[derive(Clone, PartialEq, Eq, Serialize, Deserialize, Debug)]
pub struct TreeNode {
    pub identity: ProcessIdentity,
    pub ppid: Pid,
    pub children: Vec<ProcessIdentity>,
}
