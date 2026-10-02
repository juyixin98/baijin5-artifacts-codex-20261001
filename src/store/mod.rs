//! 中央状态机 [`Lab`]：组合帧池、页表内存、TLB、地址空间与诊断事件环。
//!
//! 文件划分：
//! - `mod.rs`：生命周期、资源记账、快照/恢复、诊断访问器；
//! - `mapping.rs`：map / unmap / protect 与大小页覆盖冲突规则；
//! - `translate.rs`：TLB 快路径 + 走表慢路径 + 跨页逐段权限验证。

pub mod mapping;
pub mod translate;

use crate::config::Config;
use crate::error::{DomainError, DomainResult, ResourceKind};
use crate::events::{EventKind, EventLog};
use crate::frames::FrameAllocator;
use crate::mmu::PageTableMemory;
use crate::tlb::Tlb;
use crate::types::{PageSize, Permissions};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

/// 映射记账记录。页表内容是真相来源，本记录用于资源回收与诊断列举。
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct MappingRecord {
    /// 拥有该数据帧的 ASID；全局映射记 0（帧只归一份）。
    pub owner_asid: u16,
    pub global: bool,
    pub va: u64,
    pub pa: u64,
    pub page: PageSize,
    pub permissions: Permissions,
    pub dirty: bool,
    /// 占用的 4 KiB 帧数（小页 1，大页 1024）。
    pub frames: u64,
}

/// 每个地址空间的元数据（页表层次信息）。
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct AddressSpace {
    pub asid: u16,
    pub name: String,
    /// 根（L1）页表帧物理地址。
    pub root_pa: u64,
    /// L1 索引 →（L2 表物理地址, 有效小页叶子数）。大页槽不在此表中。
    pub l2_tables: BTreeMap<usize, L2Info>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct L2Info {
    pub pa: u64,
    pub leaves: u32,
}

/// 映射键：`(ASID, VA)`；全局映射以 ASID=0 归账。
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub struct MappingKey {
    pub scope: u16,
    pub va: u64,
}

impl MappingKey {
    pub fn private(asid: u16, va: u64) -> Self {
        Self { scope: asid, va }
    }
    pub fn global(va: u64) -> Self {
        Self { scope: 0, va }
    }
}

/// 持久化快照（版本化）。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LabSnapshot {
    pub version: u32,
    pub config: Config,
    pub frames: crate::frames::FrameSnapshot,
    pub tables: PageTableMemory,
    pub tlb: crate::tlb::TlbSnapshot,
    pub tlb_next_seq: u64,
    pub spaces: Vec<AddressSpace>,
    pub mappings: Vec<MappingRecord>,
}

impl LabSnapshot {
    pub const VERSION: u32 = 1;
}

/// 教学机的全部可变状态。
#[derive(Debug)]
pub struct Lab {
    config: Config,
    frames: FrameAllocator,
    tables: PageTableMemory,
    tlb: Tlb,
    spaces: BTreeMap<u16, AddressSpace>,
    mappings: BTreeMap<MappingKey, MappingRecord>,
    events: EventLog,
}

impl Lab {
    pub fn new(config: Config) -> Self {
        config.validate().expect("Config::validate 在构造前调用");
        let frames = FrameAllocator::new(config.total_frames);
        let tlb = Tlb::new(config.tlb_capacity);
        Self {
            frames,
            tables: PageTableMemory::new(),
            tlb,
            spaces: BTreeMap::new(),
            mappings: BTreeMap::new(),
            events: EventLog::new(config.event_buffer),
            config,
        }
    }

    pub fn config(&self) -> &Config {
        &self.config
    }
    pub fn frames(&self) -> &FrameAllocator {
        &self.frames
    }
    pub fn frames_mut(&mut self) -> &mut FrameAllocator {
        &mut self.frames
    }
    pub fn tables(&self) -> &PageTableMemory {
        &self.tables
    }
    pub fn tables_mut(&mut self) -> &mut PageTableMemory {
        &mut self.tables
    }
    pub fn tlb(&self) -> &Tlb {
        &self.tlb
    }
    pub fn tlb_mut(&mut self) -> &mut Tlb {
        &mut self.tlb
    }
    pub fn events(&self) -> &EventLog {
        &self.events
    }
    pub fn events_mut(&mut self) -> &mut EventLog {
        &mut self.events
    }
    pub fn mappings(&self) -> &BTreeMap<MappingKey, MappingRecord> {
        &self.mappings
    }
    pub fn spaces(&self) -> &BTreeMap<u16, AddressSpace> {
        &self.spaces
    }

    pub fn require_space(&self, asid: u16) -> DomainResult<&AddressSpace> {
        self.spaces.get(&asid).ok_or(DomainError::StateConflict(
            crate::error::ConflictKind::UnknownAsid { asid },
        ))
    }

    /// 创建地址空间：分配根页表帧、登记、并复刻现存全局映射。
    pub fn create_asid(&mut self, name: Option<String>) -> DomainResult<(u16, AddressSpace)> {
        let asid = self.pick_asid()?;
        let root_pa = self.frames.allocate_one()?;
        self.tables.create_table(root_pa);
        let space = AddressSpace {
            asid,
            name: name.unwrap_or_else(|| format!("asid-{asid}")),
            root_pa,
            l2_tables: BTreeMap::new(),
        };
        self.spaces.insert(asid, space.clone());

        // 复刻现存全局映射（全局 PTE 必须存在于每个页表中）。
        let globals: Vec<MappingRecord> = self
            .mappings
            .values()
            .filter(|m| m.global)
            .cloned()
            .collect();
        let global_copies = globals.len();
        if let Err(e) = globals
            .iter()
            .try_for_each(|g| self.install_global_copy(asid, g))
        {
            // 复刻失败（通常为帧耗尽）：回收半成品地址空间，避免帧泄漏。
            let _ = self.destroy_asid(asid);
            return Err(e);
        }

        self.events.record(
            EventKind::CreateAsid,
            format!("创建 ASID {asid}，根页表 @ {root_pa:#010x}"),
            vec![
                "ASID 从 1..=max_asids 中取最小未用编号".into(),
                "根页表占用 1 个物理帧".into(),
                format!("已复刻 {global_copies} 条现存全局映射"),
            ],
            serde_json::json!({
                "asid": asid, "root_pa": root_pa,
                "global_copies": global_copies,
                "frames_available": self.frames.available(),
            }),
            None,
        );
        Ok((asid, self.spaces[&asid].clone()))
    }

    fn pick_asid(&self) -> DomainResult<u16> {
        for candidate in 1..=self.config.max_asids {
            if !self.spaces.contains_key(&candidate) {
                return Ok(candidate);
            }
        }
        Err(DomainError::ResourceExhausted(ResourceKind::Asids {
            max: self.config.max_asids,
        }))
    }

    /// 回收地址空间：释放其私有数据帧与全部页表帧，清除其 TLB 条目。
    /// 全局映射的数据帧不随单个 ASID 回收。
    pub fn destroy_asid(&mut self, asid: u16) -> DomainResult<()> {
        let space = self.require_space(asid)?.clone();

        // 回收私有数据帧。
        let priv_records: Vec<MappingRecord> = self
            .mappings
            .values()
            .filter(|m| !m.global && m.owner_asid == asid)
            .cloned()
            .collect();
        for m in priv_records {
            self.frames.free(m.pa, m.frames)?;
            self.mappings.remove(&MappingKey::private(asid, m.va));
        }

        // 回收 L2 表帧与根帧。
        for info in space.l2_tables.values() {
            self.frames.free(info.pa, 1)?;
            self.tables.destroy_table(info.pa);
        }
        self.frames.free(space.root_pa, 1)?;
        self.tables.destroy_table(space.root_pa);

        let removed = self.tlb.invalidate_asid(asid);
        self.spaces.remove(&asid);

        self.events.record(
            EventKind::Reset,
            format!("回收 ASID {asid}（TLB 清除 {removed} 条）"),
            vec![
                "私有数据帧与页表帧全部归还帧池".into(),
                "全局映射帧不回收".into(),
            ],
            serde_json::json!({
                "asid": asid, "tlb_removed": removed,
                "frames_available": self.frames.available(),
            }),
            None,
        );
        Ok(())
    }

    /// 页数（4K 帧数）。
    pub fn frames_for(page: PageSize) -> u64 {
        match page {
            PageSize::Small => 1,
            PageSize::Large => crate::config::ENTRIES_PER_TABLE as u64,
        }
    }

    // ---- 快照 / 恢复（文件持久化在 persistence.rs 调用）----

    pub fn snapshot(&self) -> LabSnapshot {
        LabSnapshot {
            version: LabSnapshot::VERSION,
            config: self.config.clone(),
            frames: self.frames.snapshot(),
            tables: self.tables.clone(),
            tlb: self.tlb.snapshot(),
            tlb_next_seq: self.tlb.next_seq(),
            spaces: self.spaces.values().cloned().collect(),
            mappings: self.mappings.values().cloned().collect(),
        }
    }

    pub fn restore(snap: LabSnapshot) -> DomainResult<Self> {
        if snap.version != LabSnapshot::VERSION {
            return Err(DomainError::Input(format!(
                "快照版本 {} 不受支持（当前 {}）",
                snap.version,
                LabSnapshot::VERSION
            )));
        }
        snap.config.validate().map_err(DomainError::Input)?;
        let frames = FrameAllocator::restore(snap.frames);
        let tlb = Tlb::restore(snap.tlb, snap.tlb_next_seq);
        let mut spaces = BTreeMap::new();
        for s in snap.spaces {
            spaces.insert(s.asid, s);
        }
        let mut mappings = BTreeMap::new();
        for m in snap.mappings {
            let key = if m.global {
                MappingKey::global(m.va)
            } else {
                MappingKey::private(m.owner_asid, m.va)
            };
            mappings.insert(key, m);
        }
        Ok(Self {
            config: snap.config,
            frames,
            tables: snap.tables,
            tlb,
            spaces,
            mappings,
            events: EventLog::new(2048),
        })
    }
}
