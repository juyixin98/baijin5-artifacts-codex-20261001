//! Sampling state machine (the run model).
//!
//! The engine ingests ordered snapshots and maintains:
//! - a process table keyed by [`ProcessIdentity`] (boot id + pid + start
//!   time), so a reused PID never inherits the previous generation's
//!   counters;
//! - per-process parent histories, preserving the temporal relationship when
//!   a parent exits and children are re-parented;
//! - an interval-delta log where every interval is either an exact delta or
//!   an explicitly classified indeterminate interval (reset / wrap / anomaly
//!   / partial read / missing samples);
//! - a diagnostic ring buffer recording why each input was accepted,
//!   rejected, or could not be determined.

use crate::config::Config;
use crate::delta::{classify_counter, CounterDelta, DeltaClass, IntervalDelta};
use crate::diag::{push_record, redact_comm, Decision, DiagRecord};
use crate::model::{Pid, ProcessIdentity, ProcStat, Seq, Snapshot};
use crate::tree::{parent_at, ParentSpan, TreeEvent, TreeNode};
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::collections::{BTreeMap, VecDeque};

/// Per-process sampling state.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProcState {
    pub identity: ProcessIdentity,
    /// Redacted command name (see [`redact_comm`]); raw comm is never stored.
    pub comm_hash: String,
    pub spawned_seq: Seq,
    pub exit: Option<ExitMark>,
    pub last_cpu: u64,
    pub last_rss: i64,
    /// Sequence of the last *successful* read of this process.
    pub last_seq: Seq,
    /// Consecutive samples in which this process's stat was unreadable.
    pub missed_reads: u32,
    pub parent_history: Vec<ParentSpan>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct ExitMark {
    pub at_seq: Seq,
    /// False when missing snapshots (or a boot change) make the exit time
    /// uncertain: the process disappeared somewhere in `(last_seq, at_seq]`.
    pub exact: bool,
}

/// Persistent engine state (everything except configuration).
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct EngineState {
    pub boot_id: Option<String>,
    pub last_seq: Option<Seq>,
    pub request_counter: u64,
    /// Process table; persisted as a sequence (JSON object keys must be
    /// strings, so the identity-keyed map is flattened on save).
    #[serde(with = "identity_map_serde")]
    pub procs: BTreeMap<ProcessIdentity, ProcState>,
    pub deltas: Vec<IntervalDelta>,
    pub events: Vec<TreeEvent>,
    pub diags: VecDeque<DiagRecord>,
}

mod identity_map_serde {
    use super::{ProcState, ProcessIdentity};
    use serde::{Deserialize, Deserializer, Serialize, Serializer};
    use std::collections::BTreeMap;

    pub fn serialize<S: Serializer>(
        map: &BTreeMap<ProcessIdentity, ProcState>,
        s: S,
    ) -> Result<S::Ok, S::Error> {
        map.values().collect::<Vec<_>>().serialize(s)
    }

    pub fn deserialize<'de, D: Deserializer<'de>>(
        d: D,
    ) -> Result<BTreeMap<ProcessIdentity, ProcState>, D::Error> {
        let states = Vec::<ProcState>::deserialize(d)?;
        Ok(states
            .into_iter()
            .map(|ps| (ps.identity.clone(), ps))
            .collect())
    }
}

/// Outcome of ingesting one snapshot.
#[derive(Clone, Debug, Default, Serialize)]
pub struct IngestReport {
    pub request_id: String,
    pub seq: Seq,
    pub rejected: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reject_reason: Option<String>,
    pub spawns: usize,
    pub exits: usize,
    pub deltas: usize,
    pub partial_reads: usize,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub gap: Option<(Seq, Seq)>,
}

pub struct Engine {
    pub cfg: Config,
    pub state: EngineState,
}

impl Engine {
    pub fn new(cfg: Config) -> Self {
        Self {
            cfg,
            state: EngineState::default(),
        }
    }

    pub fn from_state(cfg: Config, state: EngineState) -> Self {
        Self { cfg, state }
    }

    fn next_request_id(&mut self) -> String {
        self.state.request_counter += 1;
        format!("req-{:06}", self.state.request_counter)
    }

    fn record(
        &mut self,
        request_id: &str,
        decision: Decision,
        action: &str,
        pid: Option<Pid>,
        seq: Option<Seq>,
        reason: String,
        key_state: serde_json::Value,
    ) {
        let record = DiagRecord {
            request_id: request_id.to_string(),
            seq,
            decision,
            action: action.to_string(),
            pid,
            reason,
            key_state,
        };
        push_record(&mut self.state.diags, self.cfg.max_diag_records, record);
    }

    /// Ingest one snapshot. Snapshots must arrive in strictly increasing
    /// sequence order; anything else is rejected and recorded.
    pub fn ingest(&mut self, snap: &Snapshot) -> IngestReport {
        let request_id = self.next_request_id();
        let mut report = IngestReport {
            request_id: request_id.clone(),
            seq: snap.seq,
            ..Default::default()
        };

        if let Some(last) = self.state.last_seq {
            if snap.seq <= last {
                let reason = format!(
                    "snapshot seq {got} rejected: not after last ingested seq {last}",
                    got = snap.seq
                );
                self.record(
                    &request_id,
                    Decision::Rejected,
                    "ingest",
                    None,
                    Some(snap.seq),
                    reason.clone(),
                    json!({"last_seq": last, "got_seq": snap.seq}),
                );
                report.rejected = true;
                report.reject_reason = Some(reason);
                return report;
            }
        }

        // Boot generation change: every previously live identity is stale.
        if let Some(prev_boot) = self.state.boot_id.clone() {
            if prev_boot != snap.boot_id {
                let stale: Vec<ProcessIdentity> = self
                    .state
                    .procs
                    .values()
                    .filter(|p| p.exit.is_none())
                    .map(|p| p.identity.clone())
                    .collect();
                for id in &stale {
                    self.mark_exit(id, snap.seq, false, &request_id, "boot_id changed");
                }
                self.record(
                    &request_id,
                    Decision::Indeterminate,
                    "boot-change",
                    None,
                    Some(snap.seq),
                    format!(
                        "boot generation changed; {} live process(es) closed with uncertain exit time",
                        stale.len()
                    ),
                    json!({"stale_count": stale.len()}),
                );
            }
        }

        // Sequence gap: intervals spanning it cannot be broken down.
        let gap = self
            .state
            .last_seq
            .filter(|last| snap.seq > last + 1)
            .map(|last| (last, snap.seq));
        if let Some((from, to)) = gap {
            self.record(
                &request_id,
                Decision::Indeterminate,
                "gap",
                None,
                Some(snap.seq),
                format!("snapshot sequence gap {from} -> {to}: {} sample(s) missing", to - from - 1),
                json!({"from_seq": from, "to_seq": to}),
            );
            report.gap = Some((from, to));
        }

        // 1. Processes readable in this snapshot.
        for stat in snap.procs.values() {
            let id = stat.identity(&snap.boot_id);
            let alive = self
                .state
                .procs
                .get(&id)
                .is_some_and(|p| p.exit.is_none());
            if alive {
                self.update_existing(&id, stat, snap, gap.is_some(), &request_id, &mut report);
            } else {
                self.register_new(&id, stat, snap, &request_id, &mut report);
            }
        }

        // 2. Live processes NOT readable in this snapshot.
        let live_ids: Vec<ProcessIdentity> = self
            .state
            .procs
            .values()
            .filter(|p| p.exit.is_none() && p.identity.boot_id == snap.boot_id)
            .map(|p| p.identity.clone())
            .collect();
        for id in live_ids {
            if snap.procs.contains_key(&id.pid) {
                continue;
            }
            if let Some(kind) = snap.read_failures.get(&id.pid).cloned() {
                // Partial read failure: the process directory existed, so it
                // must NOT be treated as exited. The interval is
                // indeterminate; the baseline is kept for recovery.
                let (from_seq, missed) = {
                    let ps = self.state.procs.get_mut(&id).expect("live process");
                    ps.missed_reads += 1;
                    (ps.last_seq, ps.missed_reads)
                };
                self.state.deltas.push(IntervalDelta {
                    identity: id.clone(),
                    from_seq,
                    to_seq: snap.seq,
                    cpu_jiffies: None,
                    rss_pages: None,
                    class: DeltaClass::PartialRead,
                    reason: format!(
                        "stat unreadable at seq {} ({kind}); process kept alive, baseline held at seq {from_seq}",
                        snap.seq
                    ),
                });
                self.record(
                    &request_id,
                    Decision::Indeterminate,
                    "partial-read",
                    Some(id.pid),
                    Some(snap.seq),
                    format!("stat unreadable ({kind}); process not declared exited"),
                    json!({"missed_reads": missed, "last_good_seq": from_seq}),
                );
                report.partial_reads += 1;
            } else {
                self.mark_exit(&id, snap.seq, gap.is_none(), &request_id, "absent from snapshot");
                report.exits += 1;
            }
        }

        self.state.boot_id = Some(snap.boot_id.clone());
        self.state.last_seq = Some(snap.seq);
        report
    }

    /// Update a live process observed again in `snap`.
    fn update_existing(
        &mut self,
        id: &ProcessIdentity,
        stat: &ProcStat,
        snap: &Snapshot,
        gap: bool,
        request_id: &str,
        report: &mut IngestReport,
    ) {
        let (prev_cpu, prev_seq, missed) = {
            let ps = self.state.procs.get(id).expect("live process");
            (ps.last_cpu, ps.last_seq, ps.missed_reads)
        };
        let next_cpu = stat.cpu_total();
        let elapsed = snap.seq.saturating_sub(prev_seq).max(1);

        let (class, cpu, reason) = if gap {
            (
                DeltaClass::MissingSamples,
                None,
                format!("snapshot(s) missing between seq {prev_seq} and {}; interval indeterminate", snap.seq),
            )
        } else {
            match classify_counter(
                prev_cpu,
                next_cpu,
                elapsed,
                self.cfg.counter_modulus,
                self.cfg.max_jiffies_per_interval,
            ) {
                CounterDelta::Monotonic(d) if missed > 0 => (
                    DeltaClass::Ok,
                    Some(d),
                    format!(
                        "recovered after {missed} unreadable sample(s); cumulative delta spans seq {prev_seq}..={}",
                        snap.seq
                    ),
                ),
                CounterDelta::Monotonic(d) => {
                    (DeltaClass::Ok, Some(d), "monotonic counters".to_string())
                }
                CounterDelta::Wrapped(d) => (
                    DeltaClass::Wrap,
                    Some(d),
                    format!(
                        "counter wrapped modulo {} (raw {prev_cpu} -> {next_cpu})",
                        self.cfg.counter_modulus
                    ),
                ),
                CounterDelta::Anomalous => (
                    DeltaClass::Anomaly,
                    None,
                    format!(
                        "cumulative cpu regressed {prev_cpu} -> {next_cpu} within one generation; no plausible wraparound"
                    ),
                ),
            }
        };

        // Update sampling state (re-baseline on every successful read).
        let reparent_from = {
            let ps = self.state.procs.get_mut(id).expect("live process");
            ps.last_cpu = next_cpu;
            ps.last_rss = stat.rss_pages;
            ps.last_seq = snap.seq;
            ps.missed_reads = 0;
            let current_ppid = ps.parent_history.last().map(|s| s.ppid);
            if current_ppid != Some(stat.ppid) {
                if let Some(last) = ps.parent_history.last_mut() {
                    last.to_seq = Some(snap.seq.saturating_sub(1));
                }
                ps.parent_history.push(ParentSpan {
                    ppid: stat.ppid,
                    from_seq: snap.seq,
                    to_seq: None,
                });
                current_ppid
            } else {
                None
            }
        };

        self.state.deltas.push(IntervalDelta {
            identity: id.clone(),
            from_seq: prev_seq,
            to_seq: snap.seq,
            cpu_jiffies: cpu,
            rss_pages: Some(stat.rss_pages),
            class,
            reason,
        });
        report.deltas += 1;

        if let Some(from_ppid) = reparent_from {
            self.state.events.push(TreeEvent::Reparented {
                identity: id.clone(),
                from_ppid,
                to_ppid: stat.ppid,
                at_seq: snap.seq,
            });
            self.record(
                request_id,
                Decision::Accepted,
                "reparent",
                Some(id.pid),
                Some(snap.seq),
                format!("parent changed {from_ppid} -> {} at seq {}", stat.ppid, snap.seq),
                json!({"from_ppid": from_ppid, "to_ppid": stat.ppid}),
            );
        }

        match class {
            DeltaClass::Wrap => self.record(
                request_id,
                Decision::Accepted,
                "delta",
                Some(id.pid),
                Some(snap.seq),
                "counter wraparound recognised; wrap-adjusted delta accepted".to_string(),
                json!({"prev_cpu": prev_cpu, "next_cpu": next_cpu, "modulus": self.cfg.counter_modulus}),
            ),
            DeltaClass::Anomaly => self.record(
                request_id,
                Decision::Indeterminate,
                "delta",
                Some(id.pid),
                Some(snap.seq),
                format!("cpu regression {prev_cpu} -> {next_cpu} is a data anomaly; interval indeterminate"),
                json!({"prev_cpu": prev_cpu, "next_cpu": next_cpu}),
            ),
            DeltaClass::MissingSamples => self.record(
                request_id,
                Decision::Indeterminate,
                "delta",
                Some(id.pid),
                Some(snap.seq),
                format!("interval {prev_seq} -> {} spans missing snapshot(s)", snap.seq),
                json!({"from_seq": prev_seq, "to_seq": snap.seq}),
            ),
            _ => {}
        }
    }

    /// Register a newly observed identity. If the PID was seen before under a
    /// different generation, the old generation is closed and the new one
    /// starts from a fresh baseline — counters are never inherited.
    fn register_new(
        &mut self,
        id: &ProcessIdentity,
        stat: &ProcStat,
        snap: &Snapshot,
        request_id: &str,
        report: &mut IngestReport,
    ) {
        // Most recent prior generation of the same pid, if any.
        let prior: Vec<ProcessIdentity> = self
            .state
            .procs
            .keys()
            .filter(|k| k.pid == id.pid && *k != id)
            .cloned()
            .collect();
        let mut reused_from_seq = None;
        for old in &prior {
            let old_last_seq = self.state.procs[old].last_seq;
            reused_from_seq = Some(reused_from_seq.map_or(old_last_seq, |s: Seq| s.max(old_last_seq)));
            if self.state.procs[old].exit.is_none() {
                // Rapid reuse: the old generation vanished between samples and
                // its PID was already recycled.
                self.mark_exit(
                    old,
                    snap.seq,
                    false,
                    request_id,
                    "pid reused by a new generation before its exit was observed",
                );
                report.exits += 1;
            }
        }

        let ps = ProcState {
            identity: id.clone(),
            comm_hash: redact_comm(&stat.comm),
            spawned_seq: snap.seq,
            exit: None,
            last_cpu: stat.cpu_total(),
            last_rss: stat.rss_pages,
            last_seq: snap.seq,
            missed_reads: 0,
            parent_history: vec![ParentSpan {
                ppid: stat.ppid,
                from_seq: snap.seq,
                to_seq: None,
            }],
        };
        self.state.procs.insert(id.clone(), ps);
        self.state.events.push(TreeEvent::Spawned {
            identity: id.clone(),
            ppid: stat.ppid,
            at_seq: snap.seq,
        });
        report.spawns += 1;

        if let Some(from_seq) = reused_from_seq {
            self.state.deltas.push(IntervalDelta {
                identity: id.clone(),
                from_seq,
                to_seq: snap.seq,
                cpu_jiffies: None,
                rss_pages: None,
                class: DeltaClass::Reset,
                reason: format!(
                    "pid {} reused: new generation (start_time={}) re-baselined; old counters not inherited",
                    id.pid, id.start_time
                ),
            });
            self.record(
                request_id,
                Decision::Accepted,
                "generation-reset",
                Some(id.pid),
                Some(snap.seq),
                format!(
                    "pid {} generation changed (start_time={}); counters re-baselined",
                    id.pid, id.start_time
                ),
                json!({"start_time": id.start_time, "prior_last_seq": from_seq}),
            );
        }
    }

    fn mark_exit(
        &mut self,
        id: &ProcessIdentity,
        at_seq: Seq,
        exact: bool,
        request_id: &str,
        reason: &str,
    ) {
        let comm_hash = {
            let Some(ps) = self.state.procs.get_mut(id) else { return };
            if ps.exit.is_some() {
                return;
            }
            ps.exit = Some(ExitMark { at_seq, exact });
            if let Some(last) = ps.parent_history.last_mut() {
                if last.to_seq.is_none() {
                    last.to_seq = Some(at_seq.saturating_sub(1));
                }
            }
            ps.comm_hash.clone()
        };
        self.state.events.push(TreeEvent::Exited {
            identity: id.clone(),
            at_seq,
            exact,
        });
        let (decision, note) = if exact {
            (Decision::Accepted, "exit observed at snapshot boundary".to_string())
        } else {
            (
                Decision::Indeterminate,
                format!("exit time uncertain ({}); disappeared somewhere before seq {at_seq}", reason),
            )
        };
        self.record(
            request_id,
            decision,
            "exit",
            Some(id.pid),
            Some(at_seq),
            note,
            json!({"exact": exact, "comm_hash": comm_hash}),
        );
    }

    /// Reconstruct the process tree at a sequence number.
    pub fn tree_at(&self, seq: Seq) -> BTreeMap<ProcessIdentity, TreeNode> {
        let alive = self
            .state
            .procs
            .values()
            .filter(|ps| ps.spawned_seq <= seq && ps.exit.is_none_or(|e| e.at_seq > seq))
            .map(|ps| (&ps.identity, ps.parent_history.as_slice()));
        crate::tree::build_tree(alive, seq)
    }

    /// Parent of a process at a sequence number, from its parent history.
    pub fn parent_of(&self, id: &ProcessIdentity, seq: Seq) -> Option<Pid> {
        self.state
            .procs
            .get(id)
            .and_then(|ps| parent_at(&ps.parent_history, seq))
    }
}
