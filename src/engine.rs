//! Run model: drives requests through the ARC cache, assigns request ids,
//! records one diagnostic decision per request, and samples adaptive state.
//! This is the single entry point used by the HTTP API and the offline
//! trace replayer.

use std::path::Path;

use crate::arc::{AccessReport, ArcCache, ListSnapshot, Outcome, PageId, Stats};
use crate::diag::{key_ref, Decision, DiagLog, DiagRecord, ListSizes, Op};
use crate::error::ArcError;
use crate::state::{Sampler, Snapshot, StatsSample};
use crate::store::PageStore;
use crate::writeback::Writeback;

#[derive(Debug, Clone)]
pub struct Request {
    /// Optional caller-supplied id; the engine generates `req-<seq>` when
    /// absent so every diagnostic record is addressable.
    pub request_id: Option<String>,
    pub op: Op,
    pub page: PageId,
    /// Required for writes, ignored for reads.
    pub data: Option<Vec<u8>>,
}

#[derive(Debug)]
pub struct AccessOk {
    pub outcome: Outcome,
    /// Page content for reads.
    pub data: Option<Vec<u8>>,
    pub report: AccessReport,
}

#[derive(Debug)]
pub struct Response {
    pub seq: u64,
    pub request_id: String,
    pub result: Result<AccessOk, ArcError>,
}

pub struct Engine {
    cache: ArcCache,
    store: Box<dyn PageStore>,
    writeback: Box<dyn Writeback>,
    diag: DiagLog,
    sampler: Sampler,
    seq: u64,
    raw_keys: bool,
    snapshot_path: std::path::PathBuf,
}

impl Engine {
    pub fn new(
        capacity: usize,
        store: Box<dyn PageStore>,
        writeback: Box<dyn Writeback>,
        diag_capacity: usize,
        sample_interval: u64,
        raw_keys: bool,
    ) -> Self {
        Engine {
            cache: ArcCache::new(capacity),
            store,
            writeback,
            diag: DiagLog::new(diag_capacity),
            sampler: Sampler::new(sample_interval, 256),
            seq: 0,
            raw_keys,
            snapshot_path: std::path::PathBuf::from("snapshot.json"),
        }
    }

    pub fn set_snapshot_path(&mut self, path: std::path::PathBuf) {
        self.snapshot_path = path;
    }

    pub fn snapshot_path(&self) -> &std::path::Path {
        &self.snapshot_path
    }

    pub fn cache(&self) -> &ArcCache {
        &self.cache
    }

    fn sizes(&self) -> ListSizes {
        let lists = self.cache.lists();
        ListSizes {
            t1: lists.t1.len(),
            t2: lists.t2.len(),
            b1: lists.b1.len(),
            b2: lists.b2.len(),
            dirty: self.cache.dirty_pages().len(),
        }
    }

    /// Submit one read/write request. Always produces a diagnostic record.
    pub fn submit(&mut self, req: Request) -> Response {
        self.seq += 1;
        let seq = self.seq;
        let request_id = req
            .request_id
            .clone()
            .unwrap_or_else(|| format!("req-{seq}"));

        let p_before = self.cache.p();
        let sizes_before = self.sizes();

        let result: Result<AccessOk, ArcError> = match req.op {
            Op::Read => self
                .cache
                .read(req.page, &*self.store, &*self.writeback)
                .map(|(data, report)| AccessOk {
                    outcome: report.outcome,
                    data: Some(data),
                    report,
                }),
            Op::Write => match req.data.clone() {
                None => Err(ArcError::BadRequest {
                    message: "write request without payload".into(),
                }),
                Some(data) => self
                    .cache
                    .write(req.page, data, &*self.writeback)
                    .map(|report| AccessOk {
                        outcome: report.outcome,
                        data: None,
                        report,
                    }),
            },
            other => Err(ArcError::BadRequest {
                message: format!("op {other:?} is not a page access"),
            }),
        };

        let decision = match &result {
            Ok(ok) => Decision::Accepted {
                outcome: Some(ok.outcome),
            },
            Err(e) => e.decision(),
        };
        let reason = match &result {
            Ok(ok) => explain_outcome(ok.outcome, req.op),
            Err(e) => e.to_string(),
        };

        self.diag.push(DiagRecord {
            seq,
            request_id: request_id.clone(),
            op: req.op,
            key: Some(key_ref(req.page, self.raw_keys)),
            decision,
            reason,
            p_before,
            p_after: self.cache.p(),
            capacity: self.cache.capacity(),
            sizes_before,
            sizes_after: self.sizes(),
        });
        self.sampler.maybe_sample(seq, &self.cache);

        Response {
            seq,
            request_id,
            result,
        }
    }

    /// Replay a trace of requests; returns per-request responses.
    pub fn replay<I>(&mut self, requests: I) -> Vec<Response>
    where
        I: IntoIterator<Item = Request>,
    {
        requests.into_iter().map(|r| self.submit(r)).collect()
    }

    /// Dynamic resize. Atomic: a write-back failure keeps the old capacity.
    pub fn resize(&mut self, new_capacity: usize) -> Result<(), ArcError> {
        self.seq += 1;
        let seq = self.seq;
        let p_before = self.cache.p();
        let sizes_before = self.sizes();
        let result = self.cache.resize(new_capacity, &*self.writeback);
        let decision = match &result {
            Ok(_) => Decision::Accepted { outcome: None },
            Err(e) => e.decision(),
        };
        let reason = match &result {
            Ok(r) => format!(
                "resized {} -> {}, {} victims",
                r.old_capacity,
                r.new_capacity,
                r.victims.len()
            ),
            Err(e) => e.to_string(),
        };
        self.diag.push(DiagRecord {
            seq,
            request_id: format!("req-{seq}"),
            op: Op::Resize,
            key: None,
            decision,
            reason,
            p_before,
            p_after: self.cache.p(),
            capacity: self.cache.capacity(),
            sizes_before,
            sizes_after: self.sizes(),
        });
        result.map(|_| ())
    }

    /// Flush dirty pages, then persist a metadata snapshot.
    pub fn snapshot(&mut self, path: &Path) -> Result<(), ArcError> {
        self.cache.flush_dirty(&*self.writeback)?;
        let snap = Snapshot::capture(&self.cache)?;
        snap.save(path)
    }

    /// Restore from a snapshot, replacing cache state in place. Resident
    /// pages are reloaded from the backing store; a page missing there makes
    /// the restore fail with a snapshot-category error.
    pub fn restore(&mut self, path: &Path) -> Result<(), ArcError> {
        let snap = Snapshot::load(path)?;
        let mut pages = std::collections::HashMap::new();
        for id in snap.t1.iter().chain(snap.t2.iter()) {
            match self.store.load(*id) {
                Ok(Some(data)) => {
                    pages.insert(
                        *id,
                        crate::arc::Page {
                            data,
                            dirty: false,
                        },
                    );
                }
                Ok(None) => {
                    return Err(ArcError::Snapshot {
                        message: format!("resident page {id} missing from backing store"),
                    })
                }
                Err(e) => {
                    return Err(ArcError::Snapshot {
                        message: format!("load page {id}: {e}"),
                    })
                }
            }
        }
        self.cache = ArcCache::from_parts(
            snap.capacity,
            snap.p,
            ListSnapshot {
                t1: snap.t1,
                t2: snap.t2,
                b1: snap.b1,
                b2: snap.b2,
            },
            pages,
            snap.stats,
        )?;
        Ok(())
    }

    pub fn stats(&self) -> &Stats {
        self.cache.stats()
    }

    pub fn diagnostics(&self, limit: usize, decision_kind: Option<&str>) -> Vec<&DiagRecord> {
        self.diag.query(limit, decision_kind)
    }

    pub fn samples(&self) -> Vec<&StatsSample> {
        self.sampler.samples()
    }
}

fn explain_outcome(outcome: Outcome, op: Op) -> String {
    let is_write = matches!(op, Op::Write);
    match outcome {
        Outcome::HitT1 => "page resident in T1; promoted to T2".into(),
        Outcome::HitT2 => "page resident in T2; moved to MRU".into(),
        Outcome::GhostHitB1 if is_write => {
            "id found in ghost list B1 (content NOT cached); new content inserted, p increased"
                .into()
        }
        Outcome::GhostHitB1 => {
            "id found in ghost list B1 (content NOT cached); fetched from store, p increased".into()
        }
        Outcome::GhostHitB2 if is_write => {
            "id found in ghost list B2 (content NOT cached); new content inserted, p decreased"
                .into()
        }
        Outcome::GhostHitB2 => {
            "id found in ghost list B2 (content NOT cached); fetched from store, p decreased".into()
        }
        Outcome::MissFill if is_write => {
            "page absent everywhere; new content inserted into T1 (dirty)".into()
        }
        Outcome::MissFill => "page absent everywhere; fetched from store into T1".into(),
        Outcome::ReadThrough => "capacity is zero; read served from store, nothing cached".into(),
        Outcome::WriteThrough => {
            "capacity is zero; write pushed through the write-back adapter".into()
        }
    }
}
