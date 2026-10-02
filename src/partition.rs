//! Buffer-bounded hash partitioning (the external-memory front end).
//!
//! A [`Partitioner`] routes canonical row keys into `fanout` small in-memory
//! [`Buffer`]s using [`route`]; whenever a buffer reaches the configured byte
//! budget it is spilled as one immutable Arrow IPC key segment and replaced.
//! Because only `fanout` small buffers are ever resident, a relation of
//! arbitrary size can be ingested from a lazy iterator without being held in
//! memory.
//!
//! Hashing is *routing only*: callers group/aggregate by exact key comparison,
//! so hash bucket collisions never merge distinct rows.

use crate::encoding::{encode_row, hash_key};
use crate::error::Result;
use crate::resource::Budget;
use crate::spill::{Segment, SpillManager};
use crate::value::Value;

/// Rough average key size used only to size the initial fan-out.
const EST_KEY_BYTES: usize = 48;

/// A partition buffer of canonical keys.
pub(crate) struct Buffer {
    pub keys: Vec<Vec<u8>>,
    pub bytes: usize,
}

impl Buffer {
    pub(crate) fn new() -> Self {
        Self {
            keys: Vec::new(),
            bytes: 0,
        }
    }

    pub(crate) fn push(&mut self, key: Vec<u8>) {
        self.bytes += key.len();
        self.keys.push(key);
    }

    pub(crate) fn is_empty(&self) -> bool {
        self.keys.is_empty()
    }
}

/// Route one key at a recursion `level` into `[0, fanout)`. `level` salts the
/// hash so recursive re-splitting produces different sub-partitions.
pub(crate) fn route(key: &[u8], level: u32, fanout: usize) -> usize {
    (hash_key(key, level) as usize) % fanout
}

/// Choose an initial fan-out from estimated size and budgets.
pub fn choose_fanout(total_rows: usize, budget: &Budget) -> usize {
    let est_bytes = total_rows.saturating_mul(EST_KEY_BYTES).max(1);
    let by_buffer = est_bytes
        .div_ceil(budget.partition_buffer_bytes)
        .clamp(1, 4096);
    // Two relations buffer concurrently, so bound the resident working set by
    // memory_bytes.
    let max_for_memory =
        (budget.memory_bytes / (2 * budget.partition_buffer_bytes.max(1))).clamp(2, 4096);
    by_buffer.clamp(2, max_for_memory)
}

/// Encode and route one batch of rows into partition buffers, spilling any
/// buffer that crosses the byte budget.
pub(crate) fn ingest_rows(
    buffers: &mut [Buffer],
    segments: &mut [Vec<Segment>],
    rows: &[Vec<Value>],
    level: u32,
    spill: &SpillManager,
    budget: &Budget,
    label: &str,
) -> Result<()> {
    let fanout = buffers.len();
    for row in rows {
        let key = encode_row(row);
        let p = route(&key, level, fanout);
        buffers[p].push(key);
        if buffers[p].bytes >= budget.partition_buffer_bytes {
            let seg = spill.write_keys(&buffers[p].keys, &format!("lv{level}_p{p}_{label}"))?;
            segments[p].push(seg);
            buffers[p] = Buffer::new();
        }
    }
    Ok(())
}

/// A streaming, buffer-bounded hash partitioner for one relation.
pub struct Partitioner {
    buffers: Vec<Buffer>,
    segments: Vec<Vec<Segment>>,
}

impl Partitioner {
    pub fn new(fanout: usize) -> Self {
        Self {
            buffers: (0..fanout).map(|_| Buffer::new()).collect(),
            segments: vec![Vec::new(); fanout],
        }
    }

    pub fn fanout(&self) -> usize {
        self.buffers.len()
    }

    /// Ingest typed batches, spilling partition buffers as they fill.
    pub fn ingest_batches(
        &mut self,
        batches: &[Vec<Vec<Value>>],
        level: u32,
        spill: &SpillManager,
        budget: &Budget,
        label: &str,
    ) -> Result<()> {
        for batch in batches {
            ingest_rows(
                &mut self.buffers,
                &mut self.segments,
                batch,
                level,
                spill,
                budget,
                label,
            )?;
        }
        Ok(())
    }

    /// Ingest rows from a lazy iterator without retaining the relation: each
    /// row is encoded, routed and either buffered or spilled. This is the
    /// genuinely larger-than-memory entry point.
    pub fn ingest_iter<I>(
        &mut self,
        rows: &mut I,
        level: u32,
        spill: &SpillManager,
        budget: &Budget,
        label: &str,
    ) -> Result<u64>
    where
        I: Iterator<Item = Vec<Value>>,
    {
        let fanout = self.buffers.len();
        let mut n = 0u64;
        for row in rows {
            let key = encode_row(&row);
            let p = route(&key, level, fanout);
            self.buffers[p].push(key);
            if self.buffers[p].bytes >= budget.partition_buffer_bytes {
                let seg =
                    spill.write_keys(&self.buffers[p].keys, &format!("lv{level}_p{p}_{label}"))?;
                self.segments[p].push(seg);
                self.buffers[p] = Buffer::new();
            }
            n += 1;
        }
        Ok(n)
    }

    /// Flush tail buffers and return the per-partition segment lists.
    pub fn finish(
        mut self,
        level: u32,
        spill: &SpillManager,
        _budget: &Budget,
        label: &str,
    ) -> Result<Vec<Vec<Segment>>> {
        for (p, buf) in self.buffers.iter_mut().enumerate() {
            if !buf.is_empty() {
                let seg = spill.write_keys(&buf.keys, &format!("lv{level}_p{p}_{label}tail"))?;
                self.segments[p].push(seg);
                buf.keys.clear();
            }
        }
        Ok(self.segments)
    }
}
