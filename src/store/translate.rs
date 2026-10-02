//! 翻译引擎：TLB 快路径 + 页表漫游慢路径。
//!
//! 跨页访问按“实际命中的页”拆分为多个段，逐段验证权限：
//! - TLB 大页条目优先，一次覆盖最多 4 MiB；
//! - 否则走页表，按叶子页大小确定本段边界；
//! - 任一段故障，整个翻译返回带类型的 [`PageFault`]（故障地址为该段首址），
//!   并在诊断事件中保留此前已成功的段，便于重放。
//!
//! 过期检测是**诊断旁路**：TLB 命中时额外走一次表比较 PTE 快照，仅用于置
//! `stale` 标记；命中地址与权限仍以缓存条目为准（与真实硬件一致——不刷新就
//! 继续用旧翻译）。

use crate::config::{valid_virtual, MAX_ACCESS_LEN, MAX_VIRTUAL};
use crate::error::{DomainError, DomainResult, FaultKind, PageFault};
use crate::events::{EventKind, FailureBuilder};
use crate::mmu::{walk, LeafHit, WalkOutcome};
use crate::store::Lab;
use crate::tlb::TlbEntry;
use crate::types::{vpn_tag, Segment, TranslateRequest, TranslateResult, WalkStep};

impl Lab {
    /// 翻译一次访问。页故障以 `Ok(TranslateOutcome::Fault)` 表达；
    /// 只有工程性错误（坏请求/未知 ASID/溢出）才是 `Err`。
    pub fn translate(&mut self, req: &TranslateRequest) -> DomainResult<TranslateOutcome> {
        self.validate_translate(req)?;

        let run_id = crate::events::new_run_id();
        let root_pa = self.require_space(req.asid)?.root_pa;
        let outcome = self.run_translate(req, root_pa, run_id.clone());

        match &outcome {
            TranslateOutcome::Ok(res) => {
                self.events.record_with_id(
                    res.run_id.clone(),
                    EventKind::TranslateOk,
                    format!(
                        "VA {:#010x} {} 翻译成功 -> PA {:#010x}（tlb_hit={}）",
                        req.va,
                        req.access.as_str(),
                        res.pa,
                        res.tlb_hit
                    ),
                    build_ok_rationale(req, res),
                    serde_json::to_value(res).unwrap_or(serde_json::json!({})),
                    None,
                );
            }
            TranslateOutcome::Fault(f) => {
                self.events.record_with_id(
                    f.run_id.clone(),
                    EventKind::TranslateFault,
                    format!(
                        "VA {:#010x} {} 页故障：{}",
                        f.va,
                        f.access.as_str(),
                        f.kind.code()
                    ),
                    vec![
                        f.kind.explain(),
                        "故障以类型化结果返回，不触发 panic".into(),
                    ],
                    serde_json::to_value(f).unwrap_or(serde_json::json!({})),
                    Some(FailureBuilder::fault(f.kind.code(), f.kind.explain())),
                );
            }
        }
        Ok(outcome)
    }

    fn validate_translate(&self, req: &TranslateRequest) -> DomainResult<()> {
        if req.asid == 0 {
            return Err(DomainError::Input("ASID 必须 >= 1".into()));
        }
        if !valid_virtual(req.va) {
            return Err(DomainError::Input(format!(
                "虚拟地址 {:#x} 超出 32 位空间",
                req.va
            )));
        }
        if req.len == 0 {
            return Err(DomainError::Input("访问长度 len 必须 >= 1".into()));
        }
        if req.len > MAX_ACCESS_LEN {
            return Err(DomainError::Input(format!(
                "访问长度 {} 超过上限 {}（一个大页）",
                req.len, MAX_ACCESS_LEN
            )));
        }
        let end = req
            .va
            .checked_add(req.len)
            .ok_or_else(|| DomainError::Computation(format!("VA {:#x}+len 加法溢出", req.va)))?;
        if end > MAX_VIRTUAL {
            return Err(DomainError::Input(format!(
                "访问区间 [{:#x}..{:#x}) 超出 32 位虚拟空间",
                req.va, end
            )));
        }
        // 未知 ASID 在 require_space 处报状态冲突。
        self.require_space(req.asid)?;
        Ok(())
    }

    fn run_translate(
        &mut self,
        req: &TranslateRequest,
        root_pa: u64,
        run_id: String,
    ) -> TranslateOutcome {
        let mut segments: Vec<Segment> = Vec::new();
        let mut evictions = Vec::new();
        let mut walk_levels: Vec<String> = Vec::new();
        let mut cur = req.va;
        let mut remaining = req.len;

        while remaining > 0 {
            // ---- 快路径：TLB 查找（大页优先）----
            if !req.fetch_pte {
                if let Some(entry) = self.tlb.lookup(req.asid, cur).cloned() {
                    if !entry.allows(req.access) {
                        return self.permission_fault(
                            &run_id,
                            req,
                            cur,
                            entry.permissions.read,
                            entry.permissions.write,
                            entry.permissions.execute,
                            segments,
                            Vec::new(),
                            true,
                        );
                    }
                    // 诊断旁路：比较当前 PTE 与装入快照，不改变命中语义。
                    let stale = match walk(&self.tables, root_pa, cur) {
                        WalkOutcome::Leaf(l) => l.pte_raw != entry.pte_snapshot,
                        WalkOutcome::Fault(..) => true,
                    };

                    let size = entry.page.size_bytes();
                    let chunk = chunk_len(cur, remaining, size);
                    segments.push(Segment {
                        va: cur,
                        pa: entry.pa_base + cur % size,
                        bytes: chunk,
                        page: entry.page,
                        readable: entry.permissions.read,
                        writable: entry.permissions.write,
                        executable: entry.permissions.execute,
                        source: "tlb".into(),
                        stale,
                    });
                    cur += chunk;
                    remaining -= chunk;
                    continue;
                }
            }

            // ---- 慢路径：页表漫游 ----
            let outcome = walk(&self.tables, root_pa, cur);
            let leaf: LeafHit = match outcome {
                WalkOutcome::Leaf(l) => l,
                WalkOutcome::Fault(kind, steps) => {
                    return TranslateOutcome::Fault(PageFault {
                        run_id,
                        asid: req.asid,
                        va: cur,
                        access: req.access,
                        kind,
                        walk: steps,
                        reason: format!(
                            "慢路径走表在 VA {cur:#010x} 判定故障；此前已完成 {} 段",
                            segments.len()
                        ),
                        completed_segments: segments,
                        fault_source: "walk".into(),
                    });
                }
            };
            if walk_levels.is_empty() {
                walk_levels = leaf.levels.clone();
            }

            if !leaf.perms.allows(req.access) {
                return self.permission_fault(
                    &run_id,
                    req,
                    cur,
                    leaf.perms.read,
                    leaf.perms.write,
                    leaf.perms.execute,
                    segments,
                    leaf.steps,
                    false,
                );
            }

            let size = leaf.page.size_bytes();
            let chunk = chunk_len(cur, remaining, size);

            // 回填 TLB（fetch_pte 也回填，符合“查到 PTE 后填充”的约定）。
            let entry = TlbEntry {
                asid: req.asid,
                tag: vpn_tag(cur, leaf.page),
                page: leaf.page,
                pa_base: leaf.phys_base,
                permissions: leaf.perms,
                global: leaf.global,
                pte_snapshot: leaf.pte_raw,
                seq: 0,
            };
            if let Some(ev) = self.tlb.fill(entry) {
                evictions.push(ev);
            }

            segments.push(Segment {
                va: cur,
                pa: leaf.phys_base + cur % size,
                bytes: chunk,
                page: leaf.page,
                readable: leaf.perms.read,
                writable: leaf.perms.write,
                executable: leaf.perms.execute,
                source: if req.fetch_pte {
                    "walk.fetch_pte"
                } else {
                    "walk"
                }
                .into(),
                stale: false,
            });
            cur += chunk;
            remaining -= chunk;
        }

        let first = &segments[0];
        let tlb_segments = segments.iter().filter(|s| s.source == "tlb").count();
        let stale_segments = segments.iter().filter(|s| s.stale).count();
        let res = TranslateResult {
            run_id,
            asid: req.asid,
            va: req.va,
            len: req.len,
            access: req.access,
            pa: first.pa,
            page: first.page,
            readable: first.readable,
            writable: first.writable,
            executable: first.executable,
            tlb_hit: tlb_segments == segments.len() && !req.fetch_pte,
            tlb_segments,
            walk_segments: segments.len() - tlb_segments,
            stale_segments,
            evictions,
            walk_levels,
            segments,
        };
        TranslateOutcome::Ok(res)
    }

    #[allow(clippy::too_many_arguments)]
    fn permission_fault(
        &self,
        run_id: &str,
        req: &TranslateRequest,
        fault_va: u64,
        readable: bool,
        writable: bool,
        executable: bool,
        completed: Vec<Segment>,
        walk: Vec<WalkStep>,
        from_tlb: bool,
    ) -> TranslateOutcome {
        let kind = FaultKind::PermissionDenied {
            need: req.access,
            readable,
            writable,
            executable,
        };
        TranslateOutcome::Fault(PageFault {
            run_id: run_id.to_string(),
            asid: req.asid,
            va: fault_va,
            access: req.access,
            kind,
            walk,
            reason: format!(
                "{} 在 VA {fault_va:#010x} 拒绝 {}（此前完成 {} 段）",
                if from_tlb {
                    "TLB 缓存权限"
                } else {
                    "叶子权限"
                },
                req.access.as_str(),
                completed.len()
            ),
            completed_segments: completed,
            fault_source: if from_tlb { "tlb" } else { "walk" }.into(),
        })
    }
}

/// 翻译结果或类型化页故障。
#[derive(Debug)]
pub enum TranslateOutcome {
    Ok(TranslateResult),
    Fault(PageFault),
}

fn chunk_len(cur: u64, remaining: u64, page_size: u64) -> u64 {
    let boundary = (cur / page_size + 1) * page_size;
    (boundary - cur).min(remaining)
}

fn build_ok_rationale(req: &TranslateRequest, res: &TranslateResult) -> Vec<String> {
    let mut v = vec![
        format!(
            "访问区间 [{:#x}..{:#x}) 拆为 {} 段，逐段验证 {:?} 权限",
            req.va,
            req.va + req.len,
            res.segments.len(),
            req.access
        ),
        format!(
            "TLB：{}",
            if res.tlb_hit {
                "全部段命中缓存"
            } else {
                "至少一段走页表（含 fetch_pte 强制）"
            }
        ),
    ];
    if res.segments.iter().any(|s| s.stale) {
        v.push("检测到 stale 段：TLB 快照与当前 PTE 不一致，但未失效前仍按缓存翻译".into());
    }
    if !res.evictions.is_empty() {
        v.push(format!("回填导致 {} 条 TLB 淘汰", res.evictions.len()));
    }
    v
}
