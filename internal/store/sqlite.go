// Package store persists audit records in SQLite. It is the only package that
// imports a database driver, keeping the protocol modules driver-free.
package store

import (
	"database/sql"
	"fmt"

	_ "modernc.org/sqlite" // pure-Go SQLite; no cgo toolchain required.

	"localstun/internal/audit"
)

// SQLiteSink writes audit.Record rows into a SQLite database.
type SQLiteSink struct {
	db *sql.DB
}

// Schema is the audit table definition. It is created idempotently on open.
const Schema = `
CREATE TABLE IF NOT EXISTS audit_events (
	id          INTEGER PRIMARY KEY AUTOINCREMENT,
	run_id      TEXT    NOT NULL,
	seq         INTEGER NOT NULL,
	ts          TEXT    NOT NULL,
	component   TEXT    NOT NULL,
	event       TEXT    NOT NULL,
	kind        TEXT    NOT NULL DEFAULT '',
	tx_id_hex   TEXT    NOT NULL DEFAULT '',
	src_addr    TEXT    NOT NULL DEFAULT '',
	dst_addr    TEXT    NOT NULL DEFAULT '',
	detail      TEXT    NOT NULL DEFAULT '',
	wire_hex    TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_audit_run_seq ON audit_events(run_id, seq);
CREATE INDEX IF NOT EXISTS idx_audit_kind    ON audit_events(kind);
`

// OpenSQLite opens (creating if needed) the database at dsn and ensures the
// schema exists. Use dsn "file:path?mode=rwc" style accepted by modernc.
func OpenSQLite(dsn string) (*SQLiteSink, error) {
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, fmt.Errorf("audit: open %s: %w", dsn, err)
	}
	// Single connection keeps the on-disk file coherent under concurrent
	// writes in this low-throughput test harness.
	db.SetMaxOpenConns(1)
	if _, err := db.Exec(Schema); err != nil {
		_ = db.Close()
		return nil, fmt.Errorf("audit: schema: %w", err)
	}
	return &SQLiteSink{db: db}, nil
}

// Write inserts one record.
func (s *SQLiteSink) Write(r audit.Record) error {
	_, err := s.db.Exec(
		`INSERT INTO audit_events
		 (run_id, seq, ts, component, event, kind, tx_id_hex, src_addr, dst_addr, detail, wire_hex)
		 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		r.RunID, r.Seq, r.Timestamp.UTC().Format("2006-01-02T15:04:05.000000000Z"),
		r.Component, r.Event, r.Kind, r.TxID, r.SrcAddr, r.DstAddr, r.Detail, r.WireHex,
	)
	if err != nil {
		return fmt.Errorf("audit: insert: %w", err)
	}
	return nil
}

// Close releases the database handle.
func (s *SQLiteSink) Close() error { return s.db.Close() }

// RunSummary is one row of the per-run statistics report.
type RunSummary struct {
	RunID   string
	Total   int
	ByEvent map[string]int
	ByKind  map[string]int
}

// SummarizeRun returns counts per event and per failure kind for one run.
func (s *SQLiteSink) SummarizeRun(runID string) (RunSummary, error) {
	out := RunSummary{RunID: runID, ByEvent: map[string]int{}, ByKind: map[string]int{}}
	rows, err := s.db.Query(
		`SELECT event, kind, COUNT(*) FROM audit_events WHERE run_id = ? GROUP BY event, kind`, runID)
	if err != nil {
		return out, fmt.Errorf("audit: summarize: %w", err)
	}
	defer rows.Close()
	for rows.Next() {
		var event, kind string
		var n int
		if err := rows.Scan(&event, &kind, &n); err != nil {
			return out, err
		}
		out.Total += n
		out.ByEvent[event] += n
		if kind != "" {
			out.ByKind[kind] += n
		}
	}
	return out, rows.Err()
}

// ListRuns returns distinct run ids with first/last timestamps.
type RunInfo struct {
	RunID string
	First string
	Last  string
	N     int
}

// ListRuns lists captured runs newest-first.
func (s *SQLiteSink) ListRuns() ([]RunInfo, error) {
	rows, err := s.db.Query(
		`SELECT run_id, MIN(ts), MAX(ts), COUNT(*) FROM audit_events GROUP BY run_id ORDER BY MIN(ts) DESC`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []RunInfo
	for rows.Next() {
		var ri RunInfo
		if err := rows.Scan(&ri.RunID, &ri.First, &ri.Last, &ri.N); err != nil {
			return nil, err
		}
		out = append(out, ri)
	}
	return out, rows.Err()
}
