#![allow(dead_code)]
//! 独立参考模型：用与被测核心完全不同的代码路径实现 Sv32 语义，
//! 供集成测试交叉核对。本文件不 `use mmu_lab` 的任何翻译逻辑，
//! 仅用最简单的 BTreeMap 直接查“对齐基址”。

use std::collections::BTreeMap;

pub const PAGE: u64 = 4096;
pub const LARGE: u64 = 4 * 1024 * 1024;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RefAccess {
    Fetch,
    Read,
    Write,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RefPerms {
    pub r: bool,
    pub w: bool,
    pub x: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RefPage {
    Small,
    Large,
}

#[derive(Debug, Clone, Copy)]
struct RefMap {
    pa: u64,
    perms: RefPerms,
    page: RefPage,
    global: bool,
}

/// 参考故障分类（字符串编码，独立于被测的 FaultKind）。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RefFault {
    NotPresent,
    Permission,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RefHit {
    pub pa: u64,
    pub perms: RefPerms,
    pub page: RefPage,
}

/// 独立翻译器：大页优先，再小页；scope=0 为全局。
#[derive(Default)]
pub struct RefModel {
    maps: BTreeMap<(u16, u64), RefMap>,
}

impl RefModel {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn map_small(&mut self, asid: u16, va: u64, pa: u64, perms: RefPerms, global: bool) {
        assert!(va.is_multiple_of(PAGE) && pa.is_multiple_of(PAGE));
        self.maps.insert(
            (if global { 0 } else { asid }, va),
            RefMap {
                pa,
                perms,
                page: RefPage::Small,
                global,
            },
        );
    }

    pub fn map_large(&mut self, asid: u16, va: u64, pa: u64, perms: RefPerms, global: bool) {
        assert!(va.is_multiple_of(LARGE) && pa.is_multiple_of(LARGE));
        self.maps.insert(
            (if global { 0 } else { asid }, va),
            RefMap {
                pa,
                perms,
                page: RefPage::Large,
                global,
            },
        );
    }

    pub fn unmap(&mut self, asid: u16, va: u64, global: bool) {
        self.maps.remove(&(if global { 0 } else { asid }, va));
    }

    pub fn set_perms(&mut self, asid: u16, va: u64, global: bool, perms: RefPerms) {
        let key = (if global { 0 } else { asid }, va);
        if let Some(m) = self.maps.get_mut(&key) {
            m.perms = perms;
        }
    }

    fn lookup(&self, asid: u16, va: u64) -> Option<RefMap> {
        let lbase = va - va % LARGE;
        let sbase = va - va % PAGE;
        // 大页优先：先查私有再查全局。
        for scope in [asid, 0] {
            if let Some(m) = self.maps.get(&(scope, lbase)) {
                if m.page == RefPage::Large {
                    return Some(*m);
                }
            }
        }
        for scope in [asid, 0] {
            if let Some(m) = self.maps.get(&(scope, sbase)) {
                if m.page == RefPage::Small {
                    return Some(*m);
                }
            }
        }
        None
    }

    /// 单字节参考翻译。
    pub fn translate(&self, asid: u16, va: u64, access: RefAccess) -> Result<RefHit, RefFault> {
        let m = self.lookup(asid, va).ok_or(RefFault::NotPresent)?;
        let allowed = match access {
            RefAccess::Read => m.perms.r,
            RefAccess::Write => m.perms.w,
            RefAccess::Fetch => m.perms.x,
        };
        if !allowed {
            return Err(RefFault::Permission);
        }
        let page_size = if m.page == RefPage::Large {
            LARGE
        } else {
            PAGE
        };
        Ok(RefHit {
            pa: m.pa + va % page_size,
            perms: m.perms,
            page: m.page,
        })
    }

    /// 区间参考翻译：逐字节概念上逐页拆分，返回每页首个 PA（跨页验证的独立基准）。
    pub fn translate_range(
        &self,
        asid: u16,
        va: u64,
        len: u64,
        access: RefAccess,
    ) -> Result<Vec<(u64, u64, RefPage)>, RefFault> {
        let mut out = Vec::new();
        let mut cur = va;
        let end = va + len;
        while cur < end {
            let hit = self.translate(asid, cur, access)?;
            let size = if hit.page == RefPage::Large {
                LARGE
            } else {
                PAGE
            };
            let next_boundary = (cur / size + 1) * size;
            let chunk_end = next_boundary.min(end);
            out.push((cur, hit.pa, hit.page));
            cur = chunk_end;
        }
        Ok(out)
    }
}

// ---------------------------------------------------------------------------
// 被测系统工厂与 HTTP 测试助手（核心级与接口级测试共用）
// ---------------------------------------------------------------------------

pub mod harness {
    use axum::body::{to_bytes, Body};
    use axum::http::{Method, Request, StatusCode};
    use axum::Router;
    use mmu_lab::api::{router, AppState};
    use mmu_lab::config::Config;
    use mmu_lab::store::Lab;
    use serde_json::Value;
    use tower::ServiceExt;

    pub fn lab(total_frames: u64, tlb_capacity: usize) -> Lab {
        Lab::new(Config {
            total_frames,
            tlb_capacity,
            max_asids: 8,
            event_buffer: 256,
        })
    }

    pub fn app(total_frames: u64, tlb_capacity: usize) -> Router {
        router(AppState::new(
            lab(total_frames, tlb_capacity),
            "/tmp/mmu-lab-it.json".into(),
        ))
    }

    pub async fn call(
        app: Router,
        method: Method,
        uri: &str,
        body: Option<Value>,
    ) -> (StatusCode, Value) {
        let builder = Request::builder().method(method).uri(uri);
        let req = match body {
            Some(v) => builder
                .header("content-type", "application/json")
                .body(Body::from(v.to_string()))
                .unwrap(),
            None => builder.body(Body::empty()).unwrap(),
        };
        let resp = app.oneshot(req).await.unwrap();
        let status = resp.status();
        let bytes = to_bytes(resp.into_body(), 1 << 20).await.unwrap();
        let value: Value = serde_json::from_slice(&bytes).unwrap_or(Value::Null);
        (status, value)
    }
}
