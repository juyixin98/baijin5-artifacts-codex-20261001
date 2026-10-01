//! Per-(group, operator) accumulation state and the bounded group registry.
//!
//! Skew handling: the global [`MemoryGuard`] is shared by every state. When it
//! is exhausted the executor spills the *largest resident buffer*, chosen
//! through a bounded `BTreeSet` with exactly one entry per live state, so one
//! heavy group repeatedly flushes itself instead of starving thousands of
//! small groups. Distinct groups are independently capped.
//!
//! Durability for cancellation: spilled run files plus one `checkpoint.json`
//! written at the safe boundary are sufficient to resume. Process crashes
//! outside cooperative cancellation are not a durability target.

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

use crate::error::{ErrorKind, PctlError, Result};
use crate::exec::codec::{Entry, RunWriter};
use crate::resources::{MemoryGuard, SpillManager};
use crate::spec::GroupKey;

pub type StateId = (u32, usize); // (group ordinal, operator index)

/// Mutable aggregation state for one (group, operator).
pub struct AccState {
    pub entries: Vec<Entry>,
    pub resident_bytes: usize,
    pub run_seq: u32,
    pub run_paths: Vec<PathBuf>,
    /// non-NULL measure values ingested so far
    pub non_null: u64,
    pub has_nan: bool,
}

impl AccState {
    fn new() -> Self {
        Self {
            entries: Vec::new(),
            resident_bytes: 0,
            run_seq: 0,
            run_paths: Vec::new(),
            non_null: 0,
            has_nan: false,
        }
    }
}

/// All live state for one query.
pub struct Registry {
    /// group ordinal -> (key, total row count including NULL measures)
    groups: Vec<(Option<GroupKey>, u64)>,
    key_to_ord: std::collections::HashMap<Option<GroupKey>, u32>,
    states: std::collections::HashMap<StateId, AccState>,
    /// Exactly one `(resident_bytes, id)` row per state: bounded by
    /// group_table_cap * operator_count. Ordered set doubles as victim index.
    active: BTreeSet<(usize, StateId)>,
    pub cap: usize,
    pub rejected_by_cap: u64,
}

impl Registry {
    pub fn empty(cap: usize) -> Self {
        Self {
            groups: Vec::new(),
            key_to_ord: std::collections::HashMap::new(),
            states: std::collections::HashMap::new(),
            active: BTreeSet::new(),
            cap,
            rejected_by_cap: 0,
        }
    }

    pub fn groups(&self) -> &[(Option<GroupKey>, u64)] {
        &self.groups
    }

    pub fn groups_tracked(&self) -> usize {
        self.groups.len()
    }

    pub fn state_mut(&mut self, id: StateId) -> &mut AccState {
        self.states.entry(id).or_insert_with(AccState::new)
    }

    pub fn states(&self) -> &std::collections::HashMap<StateId, AccState> {
        &self.states
    }

    /// Register (or look up) a group ordinal.
    pub fn ordinal_for(&mut self, key: Option<GroupKey>) -> Result<u32> {
        if let Some(&g) = self.key_to_ord.get(&key) {
            return Ok(g);
        }
        if self.groups.len() >= self.cap {
            self.rejected_by_cap += 1;
            return Err(PctlError::new(
                ErrorKind::Resource,
                "groups_cap_exceeded",
                format!(
                    "distinct group count reached the configured cap of {}; \
                     raise group_table_cap or reduce key cardinality",
                    self.cap
                ),
            ));
        }
        let g = self.groups.len() as u32;
        self.groups.push((key.clone(), 0));
        self.key_to_ord.insert(key, g);
        Ok(g)
    }

    pub fn bump_rows(&mut self, g: u32) {
        self.groups[g as usize].1 += 1;
    }

    /// Append an entry and keep the bounded victim index consistent.
    pub fn append_entry(&mut self, id: StateId, entry: Entry, bytes: usize) {
        let st = self.states.entry(id).or_insert_with(AccState::new);
        let old = st.resident_bytes;
        st.entries.push(entry);
        st.resident_bytes = old + bytes;
        // remove unconditionally: absent for a brand-new state, present as
        // `(0, id)` for one that was previously spilled.
        self.active.remove(&(old, id));
        self.active.insert((old + bytes, id));
    }

    /// Largest non-empty resident buffer; bounded O(log n) selection.
    pub fn largest_resident(&mut self) -> Option<StateId> {
        self.active
            .iter()
            .rev()
            .find(|(bytes, _)| *bytes > 0)
            .map(|(_, id)| *id)
    }

    /// Detach the resident vector of a state for merge finalization,
    /// releasing its victim-index row.
    pub fn take_resident(&mut self, id: StateId) -> (Vec<Entry>, usize) {
        let st = self.states.entry(id).or_insert_with(AccState::new);
        let bytes = st.resident_bytes;
        self.active.remove(&(bytes, id));
        st.resident_bytes = 0;
        (std::mem::take(&mut st.entries), bytes)
    }

    /// Spill one state's buffer; keeps the victim index at zero for it.
    /// `ascending` fixes the on-disk run order for this operator (the
    /// operator's direction never changes, so all its runs agree).
    pub fn spill_state(
        &mut self,
        id: StateId,
        spill: &SpillManager,
        memory: &MemoryGuard,
        ascending: bool,
    ) -> Result<u64> {
        let st = self.states.entry(id).or_insert_with(AccState::new);
        let old = st.resident_bytes;
        let size = state_spill_inner(st, id, spill, memory, ascending)?;
        if old > 0 {
            self.active.remove(&(old, id));
            self.active.insert((0, id));
        }
        Ok(size)
    }
}

pub const CHECKPOINT_NAME: &str = "checkpoint.json";

#[derive(serde::Serialize, serde::Deserialize)]
struct GroupDto {
    g: u32,
    key: Option<GroupKeyDto>,
    rows: u64,
}

#[derive(serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
enum GroupKeyDto {
    I64 { i64: i64 },
    Utf8 { utf8: String },
}

impl GroupKeyDto {
    fn from_key(k: &GroupKey) -> Self {
        match k {
            GroupKey::I64(v) => GroupKeyDto::I64 { i64: *v },
            GroupKey::Utf8(s) => GroupKeyDto::Utf8 { utf8: s.clone() },
        }
    }
    fn into_key(self) -> GroupKey {
        match self {
            GroupKeyDto::I64 { i64 } => GroupKey::I64(i64),
            GroupKeyDto::Utf8 { utf8 } => GroupKey::Utf8(utf8),
        }
    }
}

#[derive(serde::Serialize, serde::Deserialize)]
struct StateDto {
    g: u32,
    op: usize,
    non_null: u64,
    has_nan: bool,
    run_seq: u32,
    runs: Vec<String>,
}

#[derive(serde::Serialize, serde::Deserialize)]
pub struct Checkpoint {
    version: u32,
    ordinal_cursor: u64,
    fingerprint: String,
    groups: Vec<GroupDto>,
    states: Vec<StateDto>,
}

/// Persist a cancellation-safe checkpoint. Every referenced run file is
/// already durable (spilling finishes before this call).
pub fn write_checkpoint(
    dir: &Path,
    registry: &Registry,
    ordinal_cursor: u64,
    fingerprint: &str,
) -> Result<()> {
    let groups = registry
        .groups
        .iter()
        .enumerate()
        .map(|(g, (key, rows))| GroupDto {
            g: g as u32,
            key: key.as_ref().map(GroupKeyDto::from_key),
            rows: *rows,
        })
        .collect();
    let states = registry
        .states
        .iter()
        .filter(|(_, s)| s.non_null > 0 || !s.run_paths.is_empty())
        .map(|((g, op), s)| StateDto {
            g: *g,
            op: *op,
            non_null: s.non_null,
            has_nan: s.has_nan,
            run_seq: s.run_seq,
            runs: s
                .run_paths
                .iter()
                .filter_map(|p| p.file_name().map(|n| n.to_string_lossy().into_owned()))
                .collect(),
        })
        .collect();
    let dto = Checkpoint {
        version: 1,
        ordinal_cursor,
        fingerprint: fingerprint.to_string(),
        groups,
        states,
    };
    let tmp = dir.join(format!("{CHECKPOINT_NAME}.tmp"));
    let file = std::fs::File::create(&tmp)?;
    serde_json::to_writer_pretty(file, &dto)?;
    std::fs::rename(&tmp, dir.join(CHECKPOINT_NAME))?;
    Ok(())
}

/// Read only the cursor/fingerprint from a checkpoint.
pub fn read_checkpoint_header(dir: &Path) -> Result<(u64, String)> {
    let cp: Checkpoint = serde_json::from_reader(std::fs::File::open(dir.join(CHECKPOINT_NAME))?)
        .map_err(|e| {
        PctlError::new(
            ErrorKind::Resource,
            "bad_checkpoint",
            format!("unreadable checkpoint: {e}"),
        )
    })?;
    Ok((cp.ordinal_cursor, cp.fingerprint))
}

/// Rebuild a registry from a checkpoint, verifying runs exist and that the
/// resumed request matches the original plan fingerprint.
pub fn recover(
    spill: &SpillManager,
    cap: usize,
    expected_fingerprint: &str,
) -> Result<(Registry, u64)> {
    let dto: Checkpoint = serde_json::from_reader(std::fs::File::open(
        spill.dir().join(CHECKPOINT_NAME),
    )?)
    .map_err(|e| {
        PctlError::new(
            ErrorKind::Validation,
            "bad_checkpoint",
            format!("resume checkpoint is unreadable: {e}"),
        )
    })?;
    if dto.version != 1 {
        return Err(PctlError::new(
            ErrorKind::Validation,
            "unsupported_checkpoint_version",
            format!("checkpoint version {} unsupported", dto.version),
        ));
    }
    if dto.fingerprint != expected_fingerprint {
        return Err(PctlError::new(
            ErrorKind::Validation,
            "resume_plan_mismatch",
            "resume token was produced by a different query shape (group_by/operators/columns)",
        ));
    }
    let mut reg = Registry::empty(cap);
    for grp in dto.groups {
        let key = grp.key.map(GroupKeyDto::into_key);
        if grp.g as usize != reg.groups.len() {
            return Err(corrupt("group ordinals are not dense in checkpoint"));
        }
        reg.groups.push((key.clone(), grp.rows));
        reg.key_to_ord.insert(key, grp.g);
    }
    for st in dto.states {
        let mut runs = Vec::with_capacity(st.runs.len());
        for name in &st.runs {
            let p = spill.dir().join(name);
            if !p.is_file() {
                return Err(PctlError::new(
                    ErrorKind::Validation,
                    "missing_run_file",
                    format!("checkpoint references missing run {name}"),
                ));
            }
            runs.push(p);
        }
        let state = reg.state_mut((st.g, st.op));
        state.non_null = st.non_null;
        state.has_nan = st.has_nan;
        state.run_seq = st.run_seq;
        state.run_paths = runs;
    }
    Ok((reg, dto.ordinal_cursor))
}

fn corrupt(msg: &str) -> PctlError {
    PctlError::new(ErrorKind::Validation, "corrupt_checkpoint", msg)
}

fn state_spill_inner(
    state: &mut AccState,
    id: StateId,
    spill: &SpillManager,
    memory: &MemoryGuard,
    ascending: bool,
) -> Result<u64> {
    if state.entries.is_empty() {
        return Ok(0);
    }
    // Stable sort in the operator's fixed direction; equal keys retain their
    // ingest ordinal order (compare keeps ordinal ascending either way).
    state.entries.sort_by(|a, b| a.compare(b, ascending));
    let (g, op) = id;
    state.run_seq += 1;
    let name = format!("g{g:08}-o{op:03}-r{:06}.sst", state.run_seq);
    let path = spill.dir().join(&name);
    {
        let mut w = RunWriter::create(&path)?;
        for e in &state.entries {
            w.write_entry(e)?;
        }
        w.finish()?;
    }
    let size = spill.account_finished(&path)?;
    state.run_paths.push(path);
    let freed = state.resident_bytes;
    state.entries.clear();
    state.resident_bytes = 0;
    memory.release(freed);
    Ok(size)
}
