//! SQLite-backed persistence: sessions, protocol messages, audit log.
//! A single `Mutex<Connection>` is plenty for a teaching-scale local service
//! and keeps the storage layer honest (ACID transactions for state moves).

use std::collections::BTreeSet;
use std::sync::Mutex;

use rusqlite::{params, Connection, OptionalExtension};

use crate::audit::{now_unix, AuditRecord};
use crate::error::PsiError;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SessionState {
    /// Session exists; A has not submitted yet.
    Created,
    /// A's blinded set is stored; B may respond.
    ASubmitted,
    /// B's response is stored; A may collect.
    Completed,
}

impl SessionState {
    pub fn as_str(&self) -> &'static str {
        match self {
            SessionState::Created => "created",
            SessionState::ASubmitted => "a_submitted",
            SessionState::Completed => "completed",
        }
    }

    fn parse(s: &str) -> Result<Self, PsiError> {
        match s {
            "created" => Ok(SessionState::Created),
            "a_submitted" => Ok(SessionState::ASubmitted),
            "completed" => Ok(SessionState::Completed),
            other => Err(PsiError::Internal(format!("unknown session state '{other}'"))),
        }
    }
}

/// Outcome of storing B's response, for audit detail.
pub struct StoreBOutcome {
    pub b_duplicates_removed: usize,
}

pub struct Store {
    conn: Mutex<Connection>,
}

impl Store {
    pub fn open(path: &str) -> Result<Self, PsiError> {
        let conn = Connection::open(path).map_err(|e| PsiError::Internal(e.to_string()))?;
        let store = Store {
            conn: Mutex::new(conn),
        };
        store.init()?;
        Ok(store)
    }

    pub fn in_memory() -> Result<Self, PsiError> {
        let conn = Connection::open_in_memory().map_err(|e| PsiError::Internal(e.to_string()))?;
        let store = Store {
            conn: Mutex::new(conn),
        };
        store.init()?;
        Ok(store)
    }

    fn init(&self) -> Result<(), PsiError> {
        let conn = self.lock()?;
        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS sessions(
                session_id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages(
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                kind TEXT NOT NULL,
                points_json TEXT NOT NULL,
                PRIMARY KEY(session_id, role, kind)
            );
            CREATE TABLE IF NOT EXISTS audit(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER NOT NULL,
                session_id TEXT,
                request_id TEXT NOT NULL,
                event TEXT NOT NULL,
                outcome TEXT NOT NULL,
                detail TEXT NOT NULL
            );",
        )
        .map_err(|e| PsiError::Internal(e.to_string()))
    }

    fn lock(&self) -> Result<std::sync::MutexGuard<'_, Connection>, PsiError> {
        self.conn
            .lock()
            .map_err(|_| PsiError::Internal("store lock poisoned".into()))
    }

    pub fn create_session(&self, session_id_hex: &str) -> Result<(), PsiError> {
        let conn = self.lock()?;
        conn.execute(
            "INSERT INTO sessions(session_id, state, created_at) VALUES (?1, ?2, ?3)",
            params![session_id_hex, SessionState::Created.as_str(), now_unix()],
        )
        .map_err(|e| PsiError::Internal(e.to_string()))?;
        Ok(())
    }

    pub fn session_state(&self, session_id_hex: &str) -> Result<SessionState, PsiError> {
        let conn = self.lock()?;
        let state: Option<String> = conn
            .query_row(
                "SELECT state FROM sessions WHERE session_id = ?1",
                params![session_id_hex],
                |row| row.get(0),
            )
            .optional()
            .map_err(|e| PsiError::Internal(e.to_string()))?;
        match state {
            Some(s) => SessionState::parse(&s),
            None => Err(PsiError::SessionNotFound),
        }
    }

    /// Store A's blinded set and move Created -> ASubmitted.
    ///
    /// ORDER IS PROTOCOL-CRITICAL: B's `a_doubly` response is index-aligned
    /// with this array, so the points are stored verbatim, in submission
    /// order, duplicates included. Set semantics are enforced client-side;
    /// the returned count is the number of duplicate points *observed*
    /// (reported in the audit log as a diagnostic, not removed).
    pub fn save_a_submission(
        &self,
        session_id_hex: &str,
        points: &[[u8; 32]],
    ) -> Result<usize, PsiError> {
        let conn = self.lock()?;
        let tx = conn
            .unchecked_transaction()
            .map_err(|e| PsiError::Internal(e.to_string()))?;
        require_state(&tx, session_id_hex, SessionState::Created)?;
        let unique: BTreeSet<[u8; 32]> = points.iter().copied().collect();
        let duplicates_observed = points.len() - unique.len();
        insert_message(&tx, session_id_hex, "a", "blinded", points)?;
        set_state(&tx, session_id_hex, SessionState::ASubmitted)?;
        tx.commit().map_err(|e| PsiError::Internal(e.to_string()))?;
        Ok(duplicates_observed)
    }

    /// Store B's response and move ASubmitted -> Completed.
    pub fn save_b_response(
        &self,
        session_id_hex: &str,
        b_blinded: &[[u8; 32]],
        a_doubly: &[[u8; 32]],
    ) -> Result<StoreBOutcome, PsiError> {
        let conn = self.lock()?;
        let tx = conn
            .unchecked_transaction()
            .map_err(|e| PsiError::Internal(e.to_string()))?;
        require_state(&tx, session_id_hex, SessionState::ASubmitted)?;
        let a_points = select_message(&tx, session_id_hex, "a", "blinded")?
            .ok_or_else(|| PsiError::Internal("A submission missing in a_submitted state".into()))?;
        if a_doubly.len() != a_points.len() {
            return Err(PsiError::CountMismatch {
                expected: a_points.len(),
                got: a_doubly.len(),
            });
        }
        let (b_deduped, b_removed) = dedupe_points(b_blinded);
        insert_message(&tx, session_id_hex, "b", "blinded", &b_deduped)?;
        insert_message(&tx, session_id_hex, "b", "a_doubly", a_doubly)?;
        set_state(&tx, session_id_hex, SessionState::Completed)?;
        tx.commit().map_err(|e| PsiError::Internal(e.to_string()))?;
        Ok(StoreBOutcome {
            b_duplicates_removed: b_removed,
        })
    }

    /// A's blinded set (what B needs to respond), if submitted.
    pub fn a_submission(&self, session_id_hex: &str) -> Result<Option<Vec<[u8; 32]>>, PsiError> {
        let conn = self.lock()?;
        select_message(&conn, session_id_hex, "a", "blinded")
    }

    /// B's response (what A needs to finish), if completed.
    #[allow(clippy::type_complexity)]
    pub fn b_response(
        &self,
        session_id_hex: &str,
    ) -> Result<Option<(Vec<[u8; 32]>, Vec<[u8; 32]>)>, PsiError> {
        let conn = self.lock()?;
        let b_blinded = select_message(&conn, session_id_hex, "b", "blinded")?;
        let a_doubly = select_message(&conn, session_id_hex, "b", "a_doubly")?;
        Ok(match (b_blinded, a_doubly) {
            (Some(b), Some(d)) => Some((b, d)),
            _ => None,
        })
    }

    pub fn add_audit(
        &self,
        session_id: Option<&str>,
        request_id: &str,
        event: &str,
        outcome: &str,
        detail: &str,
    ) -> Result<(), PsiError> {
        let conn = self.lock()?;
        conn.execute(
            "INSERT INTO audit(ts, session_id, request_id, event, outcome, detail)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            params![now_unix(), session_id, request_id, event, outcome, detail],
        )
        .map_err(|e| PsiError::Internal(e.to_string()))?;
        Ok(())
    }

    pub fn audit_for(&self, session_id_hex: &str) -> Result<Vec<AuditRecord>, PsiError> {
        let conn = self.lock()?;
        let mut stmt = conn
            .prepare(
                "SELECT id, ts, session_id, request_id, event, outcome, detail
                 FROM audit WHERE session_id = ?1 ORDER BY id",
            )
            .map_err(|e| PsiError::Internal(e.to_string()))?;
        let rows = stmt
            .query_map(params![session_id_hex], |row| {
                Ok(AuditRecord {
                    id: row.get(0)?,
                    ts_unix: row.get(1)?,
                    session_id: row.get(2)?,
                    request_id: row.get(3)?,
                    event: row.get(4)?,
                    outcome: row.get(5)?,
                    detail: row.get(6)?,
                })
            })
            .map_err(|e| PsiError::Internal(e.to_string()))?;
        let mut out = Vec::new();
        for row in rows {
            out.push(row.map_err(|e| PsiError::Internal(e.to_string()))?);
        }
        Ok(out)
    }
}

fn require_state(
    conn: &Connection,
    session_id_hex: &str,
    expected: SessionState,
) -> Result<(), PsiError> {
    let state: Option<String> = conn
        .query_row(
            "SELECT state FROM sessions WHERE session_id = ?1",
            params![session_id_hex],
            |row| row.get(0),
        )
        .optional()
        .map_err(|e| PsiError::Internal(e.to_string()))?;
    match state {
        None => Err(PsiError::SessionNotFound),
        Some(s) => {
            let found = SessionState::parse(&s)?;
            if found == expected {
                Ok(())
            } else {
                Err(PsiError::InvalidSessionState {
                    expected: expected.as_str().to_string(),
                    found: found.as_str().to_string(),
                })
            }
        }
    }
}

fn set_state(
    conn: &Connection,
    session_id_hex: &str,
    state: SessionState,
) -> Result<(), PsiError> {
    conn.execute(
        "UPDATE sessions SET state = ?2 WHERE session_id = ?1",
        params![session_id_hex, state.as_str()],
    )
    .map_err(|e| PsiError::Internal(e.to_string()))?;
    Ok(())
}

fn dedupe_points(points: &[[u8; 32]]) -> (Vec<[u8; 32]>, usize) {
    let set: BTreeSet<[u8; 32]> = points.iter().copied().collect();
    let removed = points.len() - set.len();
    (set.into_iter().collect(), removed)
}

fn insert_message(
    conn: &Connection,
    session_id_hex: &str,
    role: &str,
    kind: &str,
    points: &[[u8; 32]],
) -> Result<(), PsiError> {
    let json = points_to_json(points);
    conn.execute(
        "INSERT INTO messages(session_id, role, kind, points_json) VALUES (?1, ?2, ?3, ?4)",
        params![session_id_hex, role, kind, json],
    )
    .map_err(|e| PsiError::Internal(e.to_string()))?;
    Ok(())
}

fn select_message(
    conn: &Connection,
    session_id_hex: &str,
    role: &str,
    kind: &str,
) -> Result<Option<Vec<[u8; 32]>>, PsiError> {
    let json: Option<String> = conn
        .query_row(
            "SELECT points_json FROM messages WHERE session_id = ?1 AND role = ?2 AND kind = ?3",
            params![session_id_hex, role, kind],
            |row| row.get(0),
        )
        .optional()
        .map_err(|e| PsiError::Internal(e.to_string()))?;
    json.map(|j| points_from_json(&j)).transpose()
}

fn points_to_json(points: &[[u8; 32]]) -> String {
    let hexes: Vec<String> = points.iter().map(hex::encode).collect();
    serde_json::to_string(&hexes).unwrap_or_else(|_| "[]".to_string())
}

fn points_from_json(json: &str) -> Result<Vec<[u8; 32]>, PsiError> {
    let hexes: Vec<String> =
        serde_json::from_str(json).map_err(|e| PsiError::Internal(e.to_string()))?;
    let mut out = Vec::with_capacity(hexes.len());
    for h in hexes {
        let bytes = hex::decode(&h).map_err(|e| PsiError::Internal(e.to_string()))?;
        let arr: [u8; 32] = bytes
            .try_into()
            .map_err(|_| PsiError::Internal("stored point has wrong length".into()))?;
        out.push(arr);
    }
    Ok(out)
}
