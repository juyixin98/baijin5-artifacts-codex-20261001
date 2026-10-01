//! Shared resources and mutable state.
//!
//! - [`Catalog`] holds named relations loaded from local fixtures; requests
//!   can reference them by name instead of inlining data.
//! - [`CursorStore`] keeps opaque resumption tokens with TTL, capacity-based
//!   eviction and a request fingerprint, so a cursor can never resume a
//!   different query. Tokens are unguessable UUIDs; the actual frontier stays
//!   server side and is never exposed to callers.
//! - [`AppState`]] is the single object handed to Axum handlers.

use std::collections::{HashMap, VecDeque};
use std::hash::{Hash, Hasher};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use crate::config::Config;
use crate::error::{ErrorCode, Result, ServiceError};
use crate::lftj::ResumePoint;
use crate::schema::Relation;

/// Named-relation registry (synthetic/local fixtures only).
#[derive(Default)]
pub struct Catalog {
    relations: HashMap<String, Relation>,
}

impl Catalog {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn register(&mut self, relation: Relation) -> Result<()> {
        if self.relations.contains_key(&relation.name) {
            return Err(ServiceError::new(
                ErrorCode::InvalidRequest,
                format!("catalog relation '{}' already registered", relation.name),
            ));
        }
        self.relations.insert(relation.name.clone(), relation);
        Ok(())
    }

    pub fn get(&self, name: &str) -> Option<&Relation> {
        self.relations.get(name)
    }

    pub fn names(&self) -> Vec<String> {
        let mut names: Vec<String> = self.relations.keys().cloned().collect();
        names.sort();
        names
    }

    pub fn len(&self) -> usize {
        self.relations.len()
    }

    pub fn is_empty(&self) -> bool {
        self.relations.is_empty()
    }
}

/// One stored resumption entry.
struct CursorEntry {
    /// Fingerprint of the query the frontier belongs to.
    fingerprint: u64,
    frontier: ResumePoint,
    inserted_at: Instant,
}

/// Token-indexed, bounded, expiring cursor store.
pub struct CursorStore {
    entries: HashMap<String, CursorEntry>,
    /// Insertion order for FIFO eviction.
    order: VecDeque<String>,
    ttl: Duration,
    capacity: usize,
}

impl CursorStore {
    pub fn new(ttl: Duration, capacity: usize) -> Self {
        Self {
            entries: HashMap::new(),
            order: VecDeque::new(),
            ttl,
            capacity,
        }
    }

    /// Store a frontier against a fingerprint, returning the opaque token.
    pub fn put(&mut self, fingerprint: u64, frontier: ResumePoint) -> String {
        self.sweep_expired();
        if self.entries.len() >= self.capacity {
            if let Some(oldest) = self.order.pop_front() {
                self.entries.remove(&oldest);
            }
        }
        let token = uuid::Uuid::new_v4().simple().to_string();
        self.entries.insert(
            token.clone(),
            CursorEntry {
                fingerprint,
                frontier,
                inserted_at: Instant::now(),
            },
        );
        self.order.push_back(token.clone());
        token
    }

    /// Look up and **consume** a token (single use), enforcing fingerprint and
    /// TTL. A consumed or unknown token is removed.
    pub fn take(&mut self, token: &str, fingerprint: u64) -> Result<ResumePoint> {
        self.sweep_expired();
        let valid = match self.entries.get(token) {
            Some(entry) => {
                entry.fingerprint == fingerprint && entry.inserted_at.elapsed() < self.ttl
            }
            None => false,
        };
        if !valid {
            // Consume anything present; an unknown token is simply absent.
            if self.entries.remove(token).is_some() {
                self.order.retain(|t| t != token);
            }
            return Err(ServiceError::new(
                ErrorCode::InvalidCursor,
                "cursor is unknown, expired, or does not match this query",
            ));
        }
        let entry = self.entries.remove(token).expect("validated entry");
        self.order.retain(|t| t != token);
        Ok(entry.frontier)
    }

    pub fn len(&self) -> usize {
        self.entries.len()
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    fn sweep_expired(&mut self) {
        let mut alive = VecDeque::new();
        for token in self.order.drain(..) {
            let expired = self
                .entries
                .get(&token)
                .map(|e| e.inserted_at.elapsed() >= self.ttl)
                .unwrap_or(true);
            if expired {
                self.entries.remove(&token);
            } else {
                alive.push_back(token);
            }
        }
        self.order = alive;
    }
}

/// Compute a stable-enough fingerprint of the join-relevant request parts.
/// This guards against cursor reuse across different queries; it is not a
/// cryptographic construction.
pub fn fingerprint_parts(relation_signatures: &[String], null_policy: &str) -> u64 {
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    relation_signatures.hash(&mut hasher);
    null_policy.hash(&mut hasher);
    hasher.finish()
}

/// All shared server resources.
pub struct AppState {
    pub config: Config,
    pub catalog: std::sync::RwLock<Catalog>,
    pub cursors: Mutex<CursorStore>,
}

impl AppState {
    pub fn new(config: Config) -> Self {
        let ttl = config.cursor_ttl;
        let capacity = config.max_cursors;
        AppState {
            config,
            catalog: std::sync::RwLock::new(Catalog::new()),
            cursors: Mutex::new(CursorStore::new(ttl, capacity)),
        }
    }

    /// Construct with a pre-populated catalog (e.g. bundled fixtures).
    pub fn from_parts(config: Config, catalog: Catalog) -> Self {
        let ttl = config.cursor_ttl;
        let capacity = config.max_cursors;
        AppState {
            config,
            catalog: std::sync::RwLock::new(catalog),
            cursors: Mutex::new(CursorStore::new(ttl, capacity)),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::value::{Cell, Scalar};

    #[test]
    fn cursor_round_trip_single_use_with_fingerprint_and_ttl() {
        let mut store = CursorStore::new(Duration::from_secs(60), 4);
        let point = ResumePoint {
            path: vec![crate::trie::TrieKey(Some(Scalar::Int(5)))],
            copies_delivered: 2,
        };
        let token = store.put(11, point.clone());

        // Wrong fingerprint rejected.
        let err = store.take(&token, 99).unwrap_err();
        assert_eq!(err.code, ErrorCode::InvalidCursor);
        // Failed attempt consumed the token (no replay across queries).
        assert!(store.entries.is_empty());

        let token = store.put(11, point.clone());
        let got = store.take(&token, 11).unwrap();
        assert_eq!(got, point);
        // Single use.
        assert_eq!(
            store.take(&token, 11).unwrap_err().code,
            ErrorCode::InvalidCursor
        );
    }

    #[test]
    fn cursor_store_respects_capacity() {
        let mut store = CursorStore::new(Duration::from_secs(60), 2);
        let point = || ResumePoint {
            path: vec![crate::trie::TrieKey(Some(Scalar::Int(1)))],
            copies_delivered: 0,
        };
        let t1 = store.put(1, point());
        let _t2 = store.put(1, point());
        let _t3 = store.put(1, point());
        assert_eq!(store.len(), 2);
        assert!(store.take(&t1, 1).is_err(), "oldest must have been evicted");
    }

    #[test]
    fn catalog_registers_and_rejects_duplicates() {
        let mut catalog = Catalog::new();
        let mut rel = Relation::new(
            "r",
            vec![crate::schema::Column::new(
                "a",
                crate::schema::ColumnType::Int,
            )],
        );
        rel.set_rows(vec![vec![Cell::Value(Scalar::Int(1))]])
            .unwrap();
        catalog.register(rel).unwrap();
        let mut dup = Relation::new(
            "r",
            vec![crate::schema::Column::new(
                "a",
                crate::schema::ColumnType::Int,
            )],
        );
        dup.set_rows(vec![]).unwrap();
        assert_eq!(
            catalog.register(dup).unwrap_err().code,
            ErrorCode::InvalidRequest
        );
        assert_eq!(catalog.names(), vec!["r".to_string()]);
    }
}
