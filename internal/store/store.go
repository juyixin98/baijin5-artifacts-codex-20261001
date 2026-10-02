// Package store persists STUN lab evidence in SQLite: run metadata, structured
// events, and one row per Binding exchange. It is deliberately a thin
// boundary over database/sql; all callers go through the typed methods so the
// data/error contract stays explicit.
package store

import (
	"database/sql"
	"fmt"

	_ "github.com/mattn/go-sqlite3"
)

// Exchange is one Binding request/response pair (or attempted pair).
type Exchange struct {
	RunID      string
	TxnID      string // hex
	RemoteAddr string
	Family     string // "ipv4" | "ipv6" | "unknown"
	Outcome    string // success | error_response | timeout | source_mismatch | integrity_failure | bad_request | stale | dropped
	MappedIP   string
	MappedPort int
	ErrorCode  int
	Detail     string
}

// Store is an open SQLite evidence database.
type Store struct {
	db *sql.DB
}

// Open opens (creating if needed) the SQLite file at path and applies the
// schema. An empty path opens an in-memory database.
func Open(path string) (*Store, error) {
	dsn := path
	if dsn == "" {
		dsn = ":memory:"
	}
	dsn += "?_busy_timeout=5000&_foreign_keys=on"
	db, err := sql.Open("sqlite3", dsn)
	if err != nil {
		return nil, fmt.Errorf("store: open %q: %w", path, err)
	}
	db.SetMaxOpenConns(1) // avoid SQLITE_BUSY under the test workloads
	if err := db.Ping(); err != nil {
		db.Close()
		return nil, fmt.Errorf("store: ping %q: %w", path, err)
	}
	s := &Store{db: db}
	if err := s.migrate(); err != nil {
		db.Close()
		return nil, err
	}
	return s, nil
}

func (s *Store) migrate() error {
	const schema = `
CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    component   TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    note        TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL,
    ts          TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    component   TEXT NOT NULL,
    level       TEXT NOT NULL,
    event       TEXT NOT NULL,
    fields_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS exchanges (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT NOT NULL,
    ts            TEXT NOT NULL,
    txn_id       TEXT NOT NULL,
    remote_addr  TEXT NOT NULL DEFAULT '',
    family       TEXT NOT NULL DEFAULT '',
    outcome      TEXT NOT NULL,
    mapped_ip    TEXT NOT NULL DEFAULT '',
    mapped_port  INTEGER NOT NULL DEFAULT 0,
    error_code   INTEGER NOT NULL DEFAULT 0,
    detail       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_exchanges_run ON exchanges(run_id);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id);
`
	if _, err := s.db.Exec(schema); err != nil {
		return fmt.Errorf("store: migrate: %w", err)
	}
	return nil
}

// Close releases the database handle.
func (s *Store) Close() error { return s.db.Close() }

// EnsureRun records run metadata, ignoring duplicate registration.
func (s *Store) EnsureRun(runID, component, startedAt, note string) error {
	_, err := s.db.Exec(
		`INSERT OR IGNORE INTO runs(run_id, component, started_at, note) VALUES(?,?,?,?)`,
		runID, component, startedAt, note)
	if err != nil {
		return fmt.Errorf("store: ensure run: %w", err)
	}
	return nil
}

// InsertEvent persists one structured log event.
func (s *Store) InsertEvent(runID, ts string, seq int64, component, level, event, fieldsJSON string) error {
	_, err := s.db.Exec(
		`INSERT INTO events(run_id, ts, seq, component, level, event, fields_json)
		 VALUES(?,?,?,?,?,?,?)`,
		runID, ts, seq, component, level, event, fieldsJSON)
	if err != nil {
		return fmt.Errorf("store: insert event: %w", err)
	}
	return nil
}

// RecordExchange persists one Binding exchange row.
func (s *Store) RecordExchange(ts string, x Exchange) error {
	_, err := s.db.Exec(
		`INSERT INTO exchanges(run_id, ts, txn_id, remote_addr, family, outcome,
		    mapped_ip, mapped_port, error_code, detail)
		 VALUES(?,?,?,?,?,?,?,?,?,?)`,
		x.RunID, ts, x.TxnID, x.RemoteAddr, x.Family, x.Outcome,
		x.MappedIP, x.MappedPort, x.ErrorCode, x.Detail)
	if err != nil {
		return fmt.Errorf("store: record exchange: %w", err)
	}
	return nil
}

// ExchangeCount returns the number of exchange rows for a run, used by the
// evidence tests to prove records were actually written.
func (s *Store) ExchangeCount(runID string) (int, error) {
	var n int
	if err := s.db.QueryRow(
		`SELECT COUNT(*) FROM exchanges WHERE run_id = ?`, runID).Scan(&n); err != nil {
		return 0, fmt.Errorf("store: exchange count: %w", err)
	}
	return n, nil
}
