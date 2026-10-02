//! Process tree reconstruction.
//!
//! Parent-child relationships change over time: when a parent exits, its
//! children are re-parented (to init or a subreaper). The engine records the
//! full parent history of every process as a list of [`ParentSpan`]s plus a
//! chronological event log, so a tree can be rebuilt for any past sequence
//! number without losing the temporal relationship.

use crate::model::{Pid, ProcessIdentity, Seq};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

/// Inclusive sequence range during which `ppid` was the observed parent.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct ParentSpan {
    pub ppid: Pid,
    pub from_seq: Seq,
    /// Last sequence the span covers; `None` while the span is still open.
    pub to_seq: Option<Seq>,
}

/// Chronological tree event.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum TreeEvent {
    /// First observation of a process identity.
    Spawned {
        identity: ProcessIdentity,
        ppid: Pid,
        at_seq: Seq,
    },
    /// Observed parent changed (e.g. orphan re-parented to init).
    Reparented {
        identity: ProcessIdentity,
        from_ppid: Pid,
        to_ppid: Pid,
        at_seq: Seq,
    },
    /// Process no longer present. `exact` is false when missing snapshots or
    /// a boot change make the exit time uncertain.
    Exited {
        identity: ProcessIdentity,
        at_seq: Seq,
        exact: bool,
    },
}

/// Parent of a process at a given sequence number, from its parent history.
pub fn parent_at(history: &[ParentSpan], seq: Seq) -> Option<Pid> {
    history
        .iter()
        .find(|span| span.from_seq <= seq && span.to_seq.is_none_or(|t| seq <= t))
        .map(|span| span.ppid)
}

/// A reconstructed tree node at one sequence number.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct TreeNode {
    pub identity: ProcessIdentity,
    /// Observed parent pid at this sequence (raw pid; resolve via `children`).
    pub ppid: Option<Pid>,
    /// Identities whose parent at this sequence is this process.
    pub children: Vec<ProcessIdentity>,
}

/// Build the parent/children view for every process alive at `seq`.
///
/// `alive` yields `(identity, parent_history)` for each candidate process;
/// the engine supplies it from its process table.
pub fn build_tree<'a, I>(alive: I, seq: Seq) -> BTreeMap<ProcessIdentity, TreeNode>
where
    I: IntoIterator<Item = (&'a ProcessIdentity, &'a [ParentSpan])>,
{
    let mut nodes: BTreeMap<ProcessIdentity, TreeNode> = BTreeMap::new();
    let mut edges: Vec<(ProcessIdentity, Pid)> = Vec::new();
    for (identity, history) in alive {
        let ppid = parent_at(history, seq);
        if let Some(p) = ppid {
            edges.push((identity.clone(), p));
        }
        nodes.insert(
            identity.clone(),
            TreeNode {
                identity: identity.clone(),
                ppid,
                children: Vec::new(),
            },
        );
    }
    for (child, ppid) in edges {
        // Attach to the parent identity alive at `seq` with that pid, if any.
        let parent_id = nodes
            .keys()
            .find(|id| id.pid == ppid)
            .cloned();
        if let Some(parent_id) = parent_id {
            if let Some(node) = nodes.get_mut(&parent_id) {
                node.children.push(child);
            }
        }
    }
    nodes
}
