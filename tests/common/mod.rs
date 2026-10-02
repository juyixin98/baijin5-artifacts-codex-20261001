//! Shared helpers for integration tests.
//!
//! Each integration-test binary uses a different subset of these helpers;
//! the allow below avoids per-binary dead-code noise.
#![allow(dead_code)]

use blksched::model::DeviceModel;
use blksched::sched::deadline::DeadlineScheduler;
use blksched::sched::scan::ScanScheduler;
use blksched::sched::Scheduler;
use blksched::trace::{self, Trace};
use std::path::Path;

/// Unit HDD: seek_base 0, 1 ns/sector seek, 1 ns/sector transfer, so every
/// cost is hand-computable: service = |start - head| + len.
pub fn unit_hdd() -> DeviceModel {
    DeviceModel::Hdd {
        seek_base_ns: 0,
        seek_ns_per_sector: 1,
        transfer_ns_per_sector: 1,
    }
}

/// Synthetic constant-latency device: service = 100 + len.
pub fn unit_ssd_like() -> DeviceModel {
    DeviceModel::SsdLike {
        fixed_latency_ns: 100,
        transfer_ns_per_sector: 1,
    }
}

pub fn fixture(name: &str) -> Trace {
    let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("fixtures/traces");
    trace::load_fixture(&dir, name).expect("fixture loads")
}

pub fn scan() -> Box<dyn Scheduler> {
    Box::new(ScanScheduler::new(true))
}

/// Deadline scheduler whose expiry effectively never fires (for ordering tests).
pub fn deadline_no_expire() -> Box<dyn Scheduler> {
    Box::new(DeadlineScheduler::new(u64::MAX, u64::MAX, 2))
}

pub fn deadline(read_expire_ns: u64, write_expire_ns: u64, writes_starved: u32) -> Box<dyn Scheduler> {
    Box::new(DeadlineScheduler::new(
        read_expire_ns,
        write_expire_ns,
        writes_starved,
    ))
}
