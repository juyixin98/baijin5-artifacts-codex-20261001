//! Sampling state engine: applies snapshots in sequence and maintains
//! per-identity state, the process tree (including re-parent history), CPU
//! deltas, exits, and the list of undeterminable intervals.
//!
//! Key invariants:
//! - Identity = (pid, start_generation); a reused PID never inherits counters.
//! - A PID absent from a snapshot but present in `read_failures` is kept
//!   alive (stale), not treated as exited.
//! - Counter regression is classified as wrap vs anomaly by `diff` module.

use std::collections::{HashMap, HashSet};

use crate::diff::{classify_counter, CounterClass};
use crate::model::{
    CpuDelta, DeltaCategory, ExitEvent, ExitReason, FailureCategory, Pid, ProcStat,
    ProcessIdentity, ReparentEvent, Seq, Snapshot, TreeNode, TreeView, UndeterminedInterval,
};

#[derive(Clone, Copy, Debug)]
pub struct EngineConfig {
    /// Maximum value of the cumulative CPU counter before wraparound.
    pub counter_max: u64,
    /// Largest backwards jump still explainable as a counter wrap.
    pub wrap_max_plausible_delta: u64,
}

#[derive(Clone, Debug)]
struct ProcState {
    ppid: Pid,
    cpu_total: u64,
    rss_bytes: u64,
    last_seq: Seq,
}

/// Why a snapshot was rejected.
#[derive(Clone, PartialEq, Eq, Debug)]
pub enum RejectReason {
    /// seq is not greater than the last applied seq.
    DuplicateOrOutOfOrder { last: Seq, got: Seq },
}

#[derive(Clone, PartialEq, Eq, Debug)]
pub enum IngestOutcome {
    Accepted { seq: Seq, procs: usize, read_failures: usize },
    Rejected(RejectReason),
}

#[derive(Debug, Default)]
pub struct Engine {
    cfg: Option<EngineConfig>,
    last_seq: Option<Seq>,
    live: HashMap<ProcessIdentity, ProcState>,
    pid_index: HashMap<Pid, ProcessIdentity>,
    deltas: Vec<CpuDelta>,
    exits: Vec<ExitEvent>,
    reparents: Vec<ReparentEvent>,
    undetermined: Vec<UndeterminedInterval>,
}

impl Engine {
    pub fn new(cfg: EngineConfig) -> Self {
        Engine { cfg: Some(cfg), ..Default::default() }
    }

    /// Apply one snapshot. Out-of-order or duplicate seqs are rejected without
    /// mutating state.
    pub fn apply(&mut self, snap: &Snapshot) -> IngestOutcome {
        let cfg = self.cfg.expect("engine config");
        let seq = snap.meta.seq;
        if let Some(last) = self.last_seq {
            if seq <= last {
                return IngestOutcome::Rejected(RejectReason::DuplicateOrOutOfOrder { last, got: seq });
            }
        }
        let gap = self.last_seq.is_some_and(|l| seq > l + 1);
        let prev_seq = self.last_seq;
        let at_ms = snap.meta.taken_at_ms;

        let failed: HashSet<Pid> = snap.read_failures.iter().copied().collect();

        // 1. Partial read failures: keep the process alive, flag the interval.
        for pid in &snap.read_failures {
            if let Some(identity) = self.pid_index.get(pid).copied() {
                let from = self.live[&identity].last_seq;
                self.undetermined.push(UndeterminedInterval {
                    identity,
                    from_seq: from,
                    to_seq: seq,
                    category: FailureCategory::ReadFailure,
                    delta_known: false,
                    reason: format!("stat read failed for pid {pid} at seq {seq}; process kept alive"),
                });
            }
        }

        // 2. Walk observed processes.
        let mut seen: HashSet<Pid> = HashSet::new();
        for proc in &snap.procs {
            seen.insert(proc.identity.pid);
            match self.pid_index.get(&proc.identity.pid).copied() {
                Some(existing) if existing != proc.identity => {
                    // PID reuse: retire the old identity, start a fresh one.
                    self.retire(existing, seq, at_ms, ExitReason::PidReused);
                    self.insert_fresh(proc, seq);
                    self.deltas.push(CpuDelta {
                        identity: proc.identity,
                        from_seq: None,
                        to_seq: seq,
                        at_ms,
                        delta: None,
                        category: DeltaCategory::IdentityReset,
                        reason: format!(
                            "pid {} reused: generation {} -> {}; old counters not inherited",
                            proc.identity.pid, existing.start_generation, proc.identity.start_generation
                        ),
                    });
                }
                Some(existing) => self.update_existing(existing, proc, seq, at_ms, gap, prev_seq, cfg),
                None => {
                    self.insert_fresh(proc, seq);
                    self.deltas.push(CpuDelta {
                        identity: proc.identity,
                        from_seq: None,
                        to_seq: seq,
                        at_ms,
                        delta: None,
                        category: DeltaCategory::FirstObservation,
                        reason: "first observation of identity".to_string(),
                    });
                }
            }
        }

        // 3. Exits: live identities neither observed nor read-failed.
        let mut retiring: Vec<ProcessIdentity> = self
            .live
            .keys()
            .filter(|id| !seen.contains(&id.pid) && !failed.contains(&id.pid))
            .copied()
            .collect();
        retiring.sort();
        for id in retiring {
            self.retire(id, seq, at_ms, ExitReason::Exited);
        }

        self.last_seq = Some(seq);
        IngestOutcome::Accepted {
            seq,
            procs: snap.procs.len(),
            read_failures: snap.read_failures.len(),
        }
    }

    fn insert_fresh(&mut self, proc: &ProcStat, seq: Seq) {
        self.pid_index.insert(proc.identity.pid, proc.identity);
        self.live.insert(
            proc.identity,
            ProcState {
                ppid: proc.ppid,
                cpu_total: proc.cpu_total(),
                rss_bytes: proc.rss_bytes,
                last_seq: seq,
            },
        );
    }

    fn retire(&mut self, id: ProcessIdentity, seq: Seq, at_ms: u64, reason: ExitReason) {
        self.live.remove(&id);
        self.pid_index.remove(&id.pid);
        self.exits.push(ExitEvent { identity: id, seq, at_ms, reason });
    }

    fn update_existing(
        &mut self,
        identity: ProcessIdentity,
        proc: &ProcStat,
        seq: Seq,
        at_ms: u64,
        gap: bool,
        prev_seq: Option<Seq>,
        cfg: EngineConfig,
    ) {
        let st = self.live.get_mut(&identity).expect("live state");
        if st.ppid != proc.ppid {
            self.reparents.push(ReparentEvent {
                identity,
                old_ppid: st.ppid,
                new_ppid: proc.ppid,
                seq,
                at_ms,
            });
            st.ppid = proc.ppid;
        }
        let prev_total = st.cpu_total;
        let from_seq = st.last_seq;
        let next_total = proc.cpu_total();
        st.cpu_total = next_total;
        st.rss_bytes = proc.rss_bytes;
        st.last_seq = seq;

        let (delta, category, reason) = match classify_counter(
            prev_total,
            next_total,
            cfg.counter_max,
            cfg.wrap_max_plausible_delta,
        ) {
            CounterClass::Advance(d) if gap => (
                Some(d),
                DeltaCategory::SamplingGap,
                format!(
                    "delta spans sampling gap (seq {} -> {}); cumulative value exact, per-sample breakdown unknown",
                    prev_seq.unwrap_or(0), seq
                ),
            ),
            CounterClass::Advance(d) => (Some(d), DeltaCategory::Ok, "monotonic advance".to_string()),
            CounterClass::Wrap(d) => (
                Some(d),
                DeltaCategory::CounterWrap,
                format!("counter wrapped at {} ({} -> {}); delta reconstructed", cfg.counter_max, prev_total, next_total),
            ),
            CounterClass::Anomaly => (
                None,
                DeltaCategory::CounterAnomaly,
                format!("counter regressed {} -> {} beyond plausible wrap; interval undeterminable", prev_total, next_total),
            ),
        };

        if matches!(category, DeltaCategory::SamplingGap | DeltaCategory::CounterAnomaly) {
            self.undetermined.push(UndeterminedInterval {
                identity,
                from_seq,
                to_seq: seq,
                category: match category {
                    DeltaCategory::SamplingGap => FailureCategory::SamplingGap,
                    _ => FailureCategory::CounterAnomaly,
                },
                delta_known: delta.is_some(),
                reason: reason.clone(),
            });
        }

        self.deltas.push(CpuDelta {
            identity,
            from_seq: Some(from_seq),
            to_seq: seq,
            at_ms,
            delta,
            category,
            reason,
        });
    }

    pub fn deltas(&self) -> &[CpuDelta] {
        &self.deltas
    }

    pub fn deltas_for(&self, pid: Pid) -> Vec<&CpuDelta> {
        self.deltas.iter().filter(|d| d.identity.pid == pid).collect()
    }

    pub fn exits(&self) -> &[ExitEvent] {
        &self.exits
    }

    pub fn reparents(&self) -> &[ReparentEvent] {
        &self.reparents
    }

    pub fn undetermined(&self) -> &[UndeterminedInterval] {
        &self.undetermined
    }

    /// Current forest plus the full re-parent history.
    pub fn tree(&self) -> TreeView {
        let mut nodes: Vec<TreeNode> = self
            .live
            .keys()
            .map(|id| TreeNode {
                identity: *id,
                ppid: self.live[id].ppid,
                children: Vec::new(),
            })
            .collect();
        nodes.sort_by_key(|n| n.identity);
        let live_ids: HashSet<ProcessIdentity> = self.live.keys().copied().collect();
        for i in 0..nodes.len() {
            let child = nodes[i].identity;
            let ppid = nodes[i].ppid;
            if let Some(parent) = self.pid_index.get(&ppid).copied() {
                if live_ids.contains(&parent) && parent != child {
                    if let Some(p) = nodes.iter_mut().find(|n| n.identity == parent) {
                        p.children.push(child);
                    }
                }
            }
        }
        for n in &mut nodes {
            n.children.sort();
        }
        TreeView { nodes, reparents: self.reparents.clone() }
    }
}
