//! Diagnostic report structures (read-only views over engine state).

use serde::Serialize;

use crate::{
    engine::SnapId,
    store::{Counters, PhysId},
};

#[derive(Debug, Serialize)]
pub struct StatsReport {
    pub page_size: usize,
    pub logical_pages: usize,
    pub capacity_pages: usize,
    pub used_pages: usize,
    pub free_pages: usize,
    pub snapshot_count: usize,
    pub snapshots: Vec<SnapshotStat>,
    pub counters: Counters,
}

#[derive(Debug, Serialize)]
pub struct SnapshotStat {
    pub id: SnapId,
    pub parent: Option<SnapId>,
    /// Logical pages backed by a physical page (rest read as zeros).
    pub live_pages: usize,
    /// Of those, pages shared with at least one other snapshot.
    pub shared_pages: usize,
}

#[derive(Debug, Serialize)]
pub struct VerifyReport {
    pub ok: bool,
    pub anomalies: Vec<RefcountAnomaly>,
}

#[derive(Debug, Serialize)]
pub struct RefcountAnomaly {
    pub page: PhysId,
    /// Refcount recorded in the store, if the page is known there.
    pub recorded: Option<u32>,
    /// Refcount recomputed from all snapshot tables.
    pub recomputed: u32,
}
