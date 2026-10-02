//! map / unmap / protect：映射建立、覆盖冲突规则与显式 TLB 失效协议。

use crate::config::split_vpn;
use crate::error::{ConflictKind, DomainError, DomainResult};
use crate::events::EventKind;
use crate::store::{L2Info, Lab, MappingKey, MappingRecord};
use crate::types::{MapRequest, OverlapInfo, PageSize, Permissions, Pte};
use serde::Serialize;

/// map 成功的结构化结果。
#[derive(Debug, Clone, Serialize)]
pub struct MapOutcome {
    pub run_id: String,
    pub asid: u16,
    pub va: u64,
    pub pa: u64,
    pub page: PageSize,
    pub permissions: Permissions,
    pub global: bool,
    pub frames_consumed: u64,
    /// 实际写入了哪些地址空间的页表（全局映射会写入全部现存空间）。
    pub installed_in: Vec<u16>,
    /// 按协议从 TLB 失效的条目数（invalidate=false 时恒为 0）。
    pub tlb_invalidated: usize,
    pub frames_available: u64,
}

/// unmap / protect 等操作的通用结果。
#[derive(Debug, Clone, Serialize)]
pub struct MutationOutcome {
    pub run_id: String,
    pub detail: String,
    pub tlb_invalidated: usize,
    pub frames_available: u64,
}

/// install_leaf 的叶子规格（用结构体把参数数量压到可读范围）。
#[derive(Debug, Clone, Copy)]
struct LeafSpec {
    l1_idx: usize,
    l2_idx: usize,
    pa: u64,
    page: PageSize,
    perms: Permissions,
    global: bool,
}

fn validate_addresses(req: &MapRequest) -> DomainResult<(usize, usize)> {
    if req.asid == 0 {
        return Err(DomainError::Input("ASID 必须 >= 1".into()));
    }
    let size = req.page.size_bytes();
    if req.va >= crate::config::MAX_VIRTUAL {
        return Err(DomainError::Input(format!(
            "虚拟地址 {:#x} 超出 32 位空间",
            req.va
        )));
    }
    if !req.va.is_multiple_of(size) {
        return Err(DomainError::Input(format!(
            "VA {:#x} 未按 {}（{} 字节）对齐",
            req.va,
            req.page.as_str(),
            size
        )));
    }
    req.va
        .checked_add(size)
        .filter(|end| *end <= crate::config::MAX_VIRTUAL)
        .ok_or_else(|| {
            DomainError::Computation(format!("VA {:#x}+{:#x} 溢出 32 位", req.va, size))
        })?;
    if let Some(pa) = req.pa {
        if pa >= crate::config::MAX_PHYSICAL {
            return Err(DomainError::Input(format!(
                "物理地址 {pa:#x} 超出 32 位空间"
            )));
        }
        if pa % size != 0 {
            return Err(DomainError::Input(format!(
                "PA {pa:#x} 未按 {} 对齐",
                req.page.as_str()
            )));
        }
        pa.checked_add(size)
            .filter(|end| *end <= crate::config::MAX_PHYSICAL)
            .ok_or_else(|| DomainError::Computation("物理区间端址溢出".into()))?;
    }
    if req.permissions == Permissions::none() {
        return Err(DomainError::Input(
            "叶子映射必须至少授予 R/W/X 中的一种权限（全 0 是分支 PTE 编码）".into(),
        ));
    }
    let split = split_vpn(req.va);
    Ok((split.l1, split.l2))
}

/// 覆盖冲突检测。返回冲突时构造 [`ConflictKind`]。
fn detect_overlap(lab: &Lab, req: &MapRequest) -> DomainResult<()> {
    let size = req.page.size_bytes();
    let req_end = req.va + size;

    // 决定需要检查的记录集合：
    // - 私有映射：与本 ASID 私有映射 + 全部全局映射比较；
    // - 全局映射：与所有现存映射比较（它要写入每个地址空间）。
    for rec in lab.mappings().values() {
        if !req.global && !rec.global && rec.owner_asid != req.asid {
            continue;
        }
        let rec_end = rec.va + rec.page.size_bytes();
        let ranges_overlap = req.va < rec_end && rec.va < req_end;
        if !ranges_overlap {
            continue;
        }

        // 虚拟范围相交。
        if rec.page == req.page && rec.va == req.va {
            return Err(DomainError::StateConflict(ConflictKind::MappingExists {
                va: req.va,
                page: req.page,
            }));
        }
        if rec.page != req.page {
            return Err(DomainError::StateConflict(ConflictKind::LargeSmallOverlap(
                OverlapInfo {
                    existing_va: rec.va,
                    existing_pa: rec.pa,
                    existing_page: rec.page,
                    requested_va: req.va,
                    requested_page: req.page,
                },
            )));
        }
        // 同尺寸但起点不同仍相交，只可能发生在大页之间（对齐后不应出现），
        // 小页对齐更不可能；属于防御性分支。
        return Err(DomainError::StateConflict(ConflictKind::MappingExists {
            va: rec.va,
            page: rec.page,
        }));
    }
    Ok(())
}

impl Lab {
    /// 建立映射。
    pub fn map(&mut self, req: &MapRequest) -> DomainResult<MapOutcome> {
        self.require_space(req.asid)?;
        let (l1_idx, l2_idx) = validate_addresses(req)?;
        detect_overlap(self, req)?;

        let frames_needed = Lab::frames_for(req.page);

        // 1) 数据帧：显式 PA 则预留，否则按页大小对齐分配。
        let pa = match req.pa {
            Some(pa) => {
                self.frames_mut().reserve_range(pa, frames_needed)?;
                pa
            }
            None => {
                let align = match req.page {
                    PageSize::Small => 1,
                    PageSize::Large => crate::config::ENTRIES_PER_TABLE as u64,
                };
                self.frames_mut().allocate_aligned(frames_needed, align)?
            }
        };

        // 2) 写入受影响地址空间的页表。
        let targets: Vec<u16> = if req.global {
            self.spaces().keys().copied().collect()
        } else {
            vec![req.asid]
        };
        if let Err(e) = self.install_leaf(
            &targets,
            &LeafSpec {
                l1_idx,
                l2_idx,
                pa,
                page: req.page,
                perms: req.permissions,
                global: req.global,
            },
        ) {
            // 页表写入失败时回滚数据帧，避免记账泄漏。
            let _ = self.frames_mut().free(pa, frames_needed);
            return Err(e);
        }

        // 3) 映射记账（全局映射帧只归一份，owner=0）。
        let record = MappingRecord {
            owner_asid: if req.global { 0 } else { req.asid },
            global: req.global,
            va: req.va,
            pa,
            page: req.page,
            permissions: req.permissions,
            dirty: req.permissions.write,
            frames: frames_needed,
        };
        let key = if req.global {
            MappingKey::global(req.va)
        } else {
            MappingKey::private(req.asid, req.va)
        };
        self.mappings.insert(key, record);

        // 4) 显式失效协议：默认失效；调用方显式 invalidate=false 时保留旧条目，
        //    用于构造 TLB 过期夹具（模型不代劳刷新）。
        let tlb_invalidated = if req.invalidate {
            self.tlb_mut()
                .invalidate_va(req.asid, req.va, req.page, req.global)
        } else {
            0
        };

        let rationale = vec![
            format!(
                "按 {} 切分：L1[{}]/L2[{}]",
                req.page.as_str(),
                l1_idx,
                l2_idx
            ),
            format!(
                "数据帧 {} 个，物理基址 {pa:#010x}（{}）",
                frames_needed,
                if req.pa.is_some() {
                    "显式指定"
                } else {
                    "帧池分配"
                }
            ),
            format!(
                "失效协议：{}",
                if req.invalidate {
                    "已按页大小精确失效相关 TLB 条目"
                } else {
                    "调用方要求跳过失效，旧 TLB 条目可能继续命中（过期夹具）"
                }
            ),
        ];
        let state = serde_json::json!({
            "request": req, "pa": pa, "targets": targets,
            "tlb_invalidated": tlb_invalidated,
            "frames_available": self.frames().available(),
        });
        let run_id = self.events_mut().record(
            EventKind::Map,
            format!(
                "{}映射 VA {:#010x} -> PA {:#010x}（{}，{:?}）",
                if req.global { "全局" } else { "" },
                req.va,
                pa,
                req.page.as_str(),
                req.permissions
            ),
            rationale,
            state,
            None,
        );

        Ok(MapOutcome {
            run_id,
            asid: req.asid,
            va: req.va,
            pa,
            page: req.page,
            permissions: req.permissions,
            global: req.global,
            frames_consumed: frames_needed,
            installed_in: targets,
            tlb_invalidated,
            frames_available: self.frames().available(),
        })
    }

    /// 向给定地址空间集合写入叶子 PTE，必要时分配 L2 页表帧。
    fn install_leaf(&mut self, targets: &[u16], spec: &LeafSpec) -> DomainResult<()> {
        let LeafSpec {
            l1_idx,
            l2_idx,
            pa,
            page,
            perms,
            global,
        } = *spec;
        // 先收集每个目标空间需要的 L2 新表帧（小页且该 L1 槽还没有 L2 表）。
        let need_l2: Vec<(u16, u64)> = if page == PageSize::Small {
            let mut out = Vec::new();
            for &asid in targets {
                let space = self
                    .spaces()
                    .get(&asid)
                    .ok_or(DomainError::Computation("目标地址空间在操作中消失".into()))?;
                if !space.l2_tables.contains_key(&l1_idx) {
                    let table_pa = self.frames_mut().allocate_one()?;
                    out.push((asid, table_pa));
                }
            }
            out
        } else {
            Vec::new()
        };

        for (asid, table_pa) in &need_l2 {
            self.tables_mut().create_table(*table_pa);
            let root_pa = self.spaces()[asid].root_pa;
            self.tables_mut()
                .write(root_pa, l1_idx, Pte::branch(*table_pa).raw)
                .map_err(DomainError::Computation)?;
            self.space_set_l2(
                *asid,
                l1_idx,
                L2Info {
                    pa: *table_pa,
                    leaves: 0,
                },
            );
        }

        for &asid in targets {
            let pte = Pte::leaf(pa, perms, page == PageSize::Large, perms.write, global).raw;
            match page {
                PageSize::Large => {
                    let root_pa = self.spaces()[&asid].root_pa;
                    self.tables_mut()
                        .write(root_pa, l1_idx, pte)
                        .map_err(DomainError::Computation)?;
                }
                PageSize::Small => {
                    let l2_pa = self.spaces()[&asid].l2_tables[&l1_idx].pa;
                    self.tables_mut()
                        .write(l2_pa, l2_idx, pte)
                        .map_err(DomainError::Computation)?;
                    self.space_inc_l2_leaves(asid, l1_idx, 1);
                }
            }
        }
        Ok(())
    }

    /// 在新建地址空间中复刻一条全局映射（页表帧按需新分配，数据帧不重复分配）。
    pub(crate) fn install_global_copy(&mut self, asid: u16, g: &MappingRecord) -> DomainResult<()> {
        let (l1_idx, l2_idx) = {
            let s = split_vpn(g.va);
            (s.l1, s.l2)
        };
        let targets = [asid];
        self.install_leaf(
            &targets,
            &LeafSpec {
                l1_idx,
                l2_idx,
                pa: g.pa,
                page: g.page,
                perms: g.permissions,
                global: true,
            },
        )?;
        // install_leaf 对小页新建的 L2 表 leaves 已计 1，记账无需再动。
        Ok(())
    }

    /// 拆除映射。私有映射要求 `global=false`；全局映射要求 `global=true`。
    pub fn unmap(
        &mut self,
        asid: u16,
        va: u64,
        global: bool,
        invalidate: bool,
    ) -> DomainResult<MutationOutcome> {
        if asid == 0 {
            return Err(DomainError::Input("ASID 必须 >= 1".into()));
        }
        self.require_space(asid)?;
        let key = if global {
            MappingKey::global(va)
        } else {
            MappingKey::private(asid, va)
        };
        let record =
            self.mappings().get(&key).cloned().ok_or_else(|| {
                DomainError::NotFound(format!("VA {va:#010x} 上不存在匹配的映射"))
            })?;

        let l1_idx = split_vpn(va).l1;
        let l2_idx = split_vpn(va).l2;
        let targets: Vec<u16> = if global {
            self.spaces().keys().copied().collect()
        } else {
            vec![asid]
        };

        for &a in &targets {
            match record.page {
                PageSize::Large => {
                    let root_pa = self.spaces()[&a].root_pa;
                    self.tables_mut()
                        .write(root_pa, l1_idx, 0)
                        .map_err(DomainError::Computation)?;
                }
                PageSize::Small => {
                    let l2_pa = self.spaces()[&a].l2_tables[&l1_idx].pa;
                    self.tables_mut()
                        .write(l2_pa, l2_idx, 0)
                        .map_err(DomainError::Computation)?;
                    self.space_inc_l2_leaves(a, l1_idx, -1);
                    if self.spaces()[&a].l2_tables[&l1_idx].leaves == 0 {
                        let root_pa = self.spaces()[&a].root_pa;
                        self.tables_mut()
                            .write(root_pa, l1_idx, 0)
                            .map_err(DomainError::Computation)?;
                        self.tables_mut().destroy_table(l2_pa);
                        self.frames_mut().free(l2_pa, 1)?;
                        self.space_remove_l2(a, l1_idx);
                    }
                }
            }
        }

        // 数据帧归账：全局映射只有一份帧（owner=0）。
        self.frames_mut().free(record.pa, record.frames)?;
        self.mappings.remove(&key);

        let tlb_invalidated = if invalidate {
            self.tlb_mut().invalidate_va(asid, va, record.page, global)
        } else {
            0
        };

        let run_id = self.events_mut().record(
            EventKind::Unmap,
            format!("拆除{}映射 VA {va:#010x}", if global { "全局" } else { "" }),
            vec![
                format!("归还数据帧 {} 个", record.frames),
                "叶子清零；小页 L2 表为空时连带回收 L2 表帧并清零 L1 分支".into(),
            ],
            serde_json::json!({
                "asid": asid, "va": va, "global": global, "page": record.page,
                "frames_freed": record.frames, "tlb_invalidated": tlb_invalidated,
            }),
            None,
        );
        Ok(MutationOutcome {
            run_id,
            detail: "unmap 完成".into(),
            tlb_invalidated,
            frames_available: self.frames().available(),
        })
    }

    /// 修改权限（权限降级夹具使用）。
    pub fn protect(
        &mut self,
        asid: u16,
        va: u64,
        global: bool,
        perms: Permissions,
        invalidate: bool,
    ) -> DomainResult<MutationOutcome> {
        if asid == 0 {
            return Err(DomainError::Input("ASID 必须 >= 1".into()));
        }
        if perms == Permissions::none() {
            return Err(DomainError::Input(
                "protect 不能把权限降为全 0（那会使叶子编码退化为分支项）；请用 unmap".into(),
            ));
        }
        self.require_space(asid)?;
        let key = if global {
            MappingKey::global(va)
        } else {
            MappingKey::private(asid, va)
        };
        let record = self
            .mappings()
            .get(&key)
            .cloned()
            .ok_or_else(|| DomainError::NotFound(format!("VA {va:#010x} 上不存在映射")))?;

        let l1_idx = split_vpn(va).l1;
        let l2_idx = split_vpn(va).l2;
        let targets: Vec<u16> = if global {
            self.spaces().keys().copied().collect()
        } else {
            vec![asid]
        };
        for &a in &targets {
            let pte = Pte::leaf(
                record.pa,
                perms,
                record.page == PageSize::Large,
                perms.write || record.dirty,
                global,
            )
            .raw;
            match record.page {
                PageSize::Large => {
                    let root_pa = self.spaces()[&a].root_pa;
                    self.tables_mut()
                        .write(root_pa, l1_idx, pte)
                        .map_err(DomainError::Computation)?;
                }
                PageSize::Small => {
                    let l2_pa = self.spaces()[&a].l2_tables[&l1_idx].pa;
                    self.tables_mut()
                        .write(l2_pa, l2_idx, pte)
                        .map_err(DomainError::Computation)?;
                }
            }
        }

        let old_perms = record.permissions;
        if let Some(rec) = self.mappings.get_mut(&key) {
            rec.permissions = perms;
            rec.dirty = rec.dirty || perms.write;
        }

        let tlb_invalidated = if invalidate {
            self.tlb_mut().invalidate_va(asid, va, record.page, global)
        } else {
            0
        };

        let downgrade = matches!((old_perms.write, perms.write), (true, false))
            || !perms.read && old_perms.read;
        let run_id = self.events_mut().record(
            EventKind::Protect,
            format!(
                "{} VA {va:#010x} 权限 {:?} -> {:?}",
                if downgrade { "降级" } else { "变更" },
                old_perms,
                perms
            ),
            vec![format!(
                "失效协议：{}",
                if invalidate {
                    "已失效旧权限的 TLB 条目"
                } else {
                    "跳过失效：旧权限条目仍可命中（权限降级过期夹具）"
                }
            )],
            serde_json::json!({
                "asid": asid, "va": va, "global": global,
                "old": old_perms, "new": perms,
                "tlb_invalidated": tlb_invalidated,
            }),
            None,
        );
        Ok(MutationOutcome {
            run_id,
            detail: "protect 完成".into(),
            tlb_invalidated,
            frames_available: self.frames().available(),
        })
    }

    /// 显式失效入口（诊断/教学 API 可直接调用协议，而不修改映射）。
    pub fn invalidate(
        &mut self,
        asid: Option<u16>,
        va: Option<u64>,
        page: Option<PageSize>,
    ) -> DomainResult<usize> {
        let n = match (asid, va, page) {
            (Some(a), Some(va), Some(page)) => {
                self.require_space(a)?;
                // 显式失效同时按全局与非全局范围清除，保证协议调用足够直观。
                let n1 = self.tlb_mut().invalidate_va(a, va, page, false);
                let n2 = self.tlb_mut().invalidate_va(a, va, page, true);
                n1 + n2
            }
            (Some(a), None, None) => {
                self.require_space(a)?;
                self.tlb_mut().invalidate_asid(a)
            }
            (None, None, None) => self.tlb_mut().clear(),
            _ => {
                return Err(DomainError::Input(
                    "失效参数必须是 (asid,va,page) / (asid) / 空（全局冲刷）三者之一".into(),
                ))
            }
        };
        self.events_mut().record(
            EventKind::Invalidate,
            format!("显式 TLB 失效，清除 {n} 条"),
            vec!["页表修改后由软件负责的显式 shootdown".into()],
            serde_json::json!({"asid": asid, "va": va, "page": page, "removed": n}),
            None,
        );
        Ok(n)
    }

    // 下列小方法集中处理 AddressSpace 的内部可变性，避免到处直接拿 BTreeMap。
    fn space_set_l2(&mut self, asid: u16, l1_idx: usize, info: L2Info) {
        if let Some(s) = self.spaces.get_mut(&asid) {
            s.l2_tables.insert(l1_idx, info);
        }
    }
    fn space_inc_l2_leaves(&mut self, asid: u16, l1_idx: usize, delta: i32) {
        if let Some(s) = self.spaces.get_mut(&asid) {
            if let Some(info) = s.l2_tables.get_mut(&l1_idx) {
                info.leaves = (info.leaves as i64 + delta as i64).max(0) as u32;
            }
        }
    }
    fn space_remove_l2(&mut self, asid: u16, l1_idx: usize) {
        if let Some(s) = self.spaces.get_mut(&asid) {
            s.l2_tables.remove(&l1_idx);
        }
    }
}
