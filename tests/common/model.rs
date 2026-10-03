//! Independent reference model of the documented ARC semantics.
//!
//! This implementation shares NO logic with `src/arc.rs`: it is written
//! naively (plain `Vec` lists, linear scans) directly from the rules in
//! README.md. The parity test drives the production engine and this model
//! with identical traces and compares full state after every step.

use std::collections::HashMap;
use std::sync::Arc;

use arc_cache::arc::{Class, Outcome, Stats};
use arc_cache::error::ErrorCategory;

pub type WbScript = Arc<dyn Fn(u64) -> bool + Send + Sync>;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Victim {
    Real(u64, Dest),
    GhostB1(u64),
    GhostB2(u64),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Dest {
    B1,
    B2,
    None,
}

pub struct Model {
    pub c: usize,
    pub p: usize,
    // Index 0 is the LRU end, last index is the MRU end.
    pub t1: Vec<u64>,
    pub t2: Vec<u64>,
    pub b1: Vec<u64>,
    pub b2: Vec<u64>,
    pub data: HashMap<u64, (Vec<u8>, bool)>,
    pub store: HashMap<u64, Vec<u8>>,
    pub wb_fail: WbScript,
    pub stats: Stats,
}

fn drop_id(list: &mut Vec<u64>, id: u64) {
    list.retain(|&x| x != id);
}

impl Model {
    pub fn new(c: usize, store: HashMap<u64, Vec<u8>>, wb_fail: WbScript) -> Self {
        Model {
            c,
            p: 0,
            t1: vec![],
            t2: vec![],
            b1: vec![],
            b2: vec![],
            data: HashMap::new(),
            store,
            wb_fail,
            stats: Stats::default(),
        }
    }

    pub fn classify(&self, id: u64) -> Class {
        if self.t1.contains(&id) {
            Class::T1
        } else if self.t2.contains(&id) {
            Class::T2
        } else if self.b1.contains(&id) {
            Class::B1
        } else if self.b2.contains(&id) {
            Class::B2
        } else {
            Class::Absent
        }
    }

    pub fn dirty_ids(&self) -> Vec<u64> {
        let mut ids: Vec<u64> = self
            .data
            .iter()
            .filter(|(_, (_, dirty))| *dirty)
            .map(|(id, _)| *id)
            .collect();
        ids.sort_unstable();
        ids
    }

    /// Write one dirty victim back through the scripted adapter.
    fn wb(&mut self, page: u64) -> Result<(), ErrorCategory> {
        if (self.wb_fail)(page) {
            self.stats.writeback_failures += 1;
            return Err(ErrorCategory::Writeback);
        }
        self.stats.writebacks += 1;
        let (data, _) = self.data[&page].clone();
        self.store.insert(page, data);
        Ok(())
    }

    pub fn read(&mut self, id: u64) -> Result<(Vec<u8>, Outcome), ErrorCategory> {
        self.stats.reads += 1;
        let class = self.classify(id);
        if self.c == 0 {
            return match self.store.get(&id) {
                Some(d) => {
                    self.stats.read_throughs += 1;
                    self.stats.store_fetches += 1;
                    Ok((d.clone(), Outcome::ReadThrough))
                }
                None => {
                    self.stats.store_misses += 1;
                    Err(ErrorCategory::NotFound)
                }
            };
        }
        let fetched = match class {
            Class::T1 | Class::T2 => None,
            _ => match self.store.get(&id) {
                Some(d) => {
                    self.stats.store_fetches += 1;
                    Some(d.clone())
                }
                None => {
                    self.stats.store_misses += 1;
                    return Err(ErrorCategory::NotFound);
                }
            },
        };
        let outcome = self.admit(id, class, fetched, None)?;
        Ok((self.data[&id].0.clone(), outcome))
    }

    pub fn write(&mut self, id: u64, data: Vec<u8>) -> Result<Outcome, ErrorCategory> {
        self.stats.writes += 1;
        let class = self.classify(id);
        if self.c == 0 {
            if (self.wb_fail)(id) {
                self.stats.writeback_failures += 1;
                return Err(ErrorCategory::Writeback);
            }
            self.stats.write_throughs += 1;
            self.store.insert(id, data);
            return Ok(Outcome::WriteThrough);
        }
        self.admit(id, class, None, Some(data))
    }

    fn admit(
        &mut self,
        id: u64,
        class: Class,
        fetched: Option<Vec<u8>>,
        written: Option<Vec<u8>>,
    ) -> Result<Outcome, ErrorCategory> {
        // Adapt p (applied only if the whole access succeeds).
        let p_eff = match class {
            Class::B1 => {
                let delta = (self.b2.len() / self.b1.len().max(1)).max(1);
                (self.p + delta).min(self.c)
            }
            Class::B2 => {
                let delta = (self.b1.len() / self.b2.len().max(1)).max(1);
                self.p.saturating_sub(delta)
            }
            _ => self.p,
        };

        let victims = self.plan(class, p_eff);

        // Write back dirty victims before mutating anything.
        for victim in &victims {
            if let Victim::Real(page, _) = victim {
                if self.data[page].1 {
                    self.wb(*page)?;
                }
            }
        }

        self.p = p_eff;
        for victim in victims {
            self.apply(victim);
        }

        let is_write = written.is_some();
        let outcome = match class {
            Class::T1 => {
                drop_id(&mut self.t1, id);
                self.t2.push(id);
                self.stats.hits_t1 += 1;
                Outcome::HitT1
            }
            Class::T2 => {
                drop_id(&mut self.t2, id);
                self.t2.push(id);
                self.stats.hits_t2 += 1;
                Outcome::HitT2
            }
            Class::B1 => {
                drop_id(&mut self.b1, id);
                let content = written.clone().or(fetched).unwrap_or_default();
                self.data.insert(id, (content, is_write));
                self.t2.push(id);
                self.stats.ghost_hits_b1 += 1;
                Outcome::GhostHitB1
            }
            Class::B2 => {
                drop_id(&mut self.b2, id);
                let content = written.clone().or(fetched).unwrap_or_default();
                self.data.insert(id, (content, is_write));
                self.t2.push(id);
                self.stats.ghost_hits_b2 += 1;
                Outcome::GhostHitB2
            }
            Class::Absent => {
                let content = written.clone().or(fetched).unwrap_or_default();
                self.data.insert(id, (content, is_write));
                self.t1.push(id);
                self.stats.misses += 1;
                Outcome::MissFill
            }
        };
        // A write always (re)sets the content and marks the page dirty,
        // including on the hit path.
        if let Some(new_data) = written {
            let entry = self.data.get_mut(&id).unwrap();
            entry.0 = new_data;
            entry.1 = true;
        }
        Ok(outcome)
    }

    fn plan(&self, class: Class, p_eff: usize) -> Vec<Victim> {
        let c = self.c;
        let resident = self.t1.len() + self.t2.len();
        let mut victims = Vec::new();
        match class {
            Class::T1 | Class::T2 => {}
            Class::B1 | Class::B2 => {
                if resident >= c {
                    if let Some(v) = self.replace_plan(class == Class::B2, p_eff) {
                        victims.push(v);
                    }
                }
            }
            Class::Absent => {
                let l1 = self.t1.len() + self.b1.len();
                let total = l1 + self.t2.len() + self.b2.len();
                if l1 == c {
                    if self.t1.len() < c {
                        victims.push(Victim::GhostB1(self.b1[0]));
                        if let Some(v) = self.replace_plan(false, p_eff) {
                            victims.push(v);
                        }
                    } else {
                        victims.push(Victim::Real(self.t1[0], Dest::None));
                    }
                } else if total >= c {
                    if total == 2 * c {
                        victims.push(Victim::GhostB2(self.b2[0]));
                    }
                    if resident >= c {
                        if let Some(v) = self.replace_plan(false, p_eff) {
                            victims.push(v);
                        }
                    }
                }
            }
        }
        victims
    }

    fn replace_plan(&self, x_in_b2: bool, p_eff: usize) -> Option<Victim> {
        let use_t1 =
            !self.t1.is_empty() && ((x_in_b2 && self.t1.len() == p_eff) || self.t1.len() > p_eff);
        if use_t1 {
            return Some(Victim::Real(self.t1[0], Dest::B1));
        }
        if !self.t2.is_empty() {
            return Some(Victim::Real(self.t2[0], Dest::B2));
        }
        self.t1.first().map(|&v| Victim::Real(v, Dest::B1))
    }

    fn apply(&mut self, victim: Victim) {
        match victim {
            Victim::Real(page, dest) => {
                drop_id(&mut self.t1, page);
                drop_id(&mut self.t2, page);
                let (_, dirty) = self.data.remove(&page).unwrap();
                match dest {
                    Dest::B1 => self.b1.push(page),
                    Dest::B2 => self.b2.push(page),
                    Dest::None => self.stats.dropped_t1 += 1,
                }
                if dirty {
                    self.stats.evictions_dirty += 1;
                } else {
                    self.stats.evictions_clean += 1;
                }
            }
            Victim::GhostB1(page) => {
                drop_id(&mut self.b1, page);
                self.stats.ghost_evictions += 1;
            }
            Victim::GhostB2(page) => {
                drop_id(&mut self.b2, page);
                self.stats.ghost_evictions += 1;
            }
        }
    }

    pub fn resize(&mut self, new_c: usize) -> Result<(), ErrorCategory> {
        if new_c == self.c {
            return Ok(());
        }
        let p_eff = self.p.min(new_c);
        let (mut t1, mut t2, mut b1, mut b2) =
            (self.t1.clone(), self.t2.clone(), self.b1.clone(), self.b2.clone());
        let mut victims = Vec::new();
        while t1.len() + t2.len() > new_c {
            if !t1.is_empty() && (t1.len() > p_eff || t2.is_empty()) {
                let v = t1.remove(0);
                victims.push(Victim::Real(v, Dest::B1));
                b1.push(v);
            } else {
                let v = t2.remove(0);
                victims.push(Victim::Real(v, Dest::B2));
                b2.push(v);
            }
        }
        while t1.len() + b1.len() > new_c {
            victims.push(Victim::GhostB1(b1.remove(0)));
        }
        while t1.len() + t2.len() + b1.len() + b2.len() > 2 * new_c && !b2.is_empty() {
            victims.push(Victim::GhostB2(b2.remove(0)));
        }
        for victim in &victims {
            if let Victim::Real(page, _) = victim {
                if self.data[page].1 {
                    self.wb(*page)?;
                }
            }
        }
        self.c = new_c;
        self.p = p_eff;
        for victim in victims {
            self.apply(victim);
        }
        Ok(())
    }
}
