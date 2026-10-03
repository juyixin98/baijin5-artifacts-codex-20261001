package service

import (
	"database/sql"
	"fmt"
	"time"

	_ "modernc.org/sqlite"
)

// AuditRow is one persisted log line: a processing step, an outcome, or
// a failure, correlated by RequestID and ConnID.
type AuditRow struct {
	RequestID string
	ConnID    string
	Phase     string // "step", "outcome", "failure"
	Detail    string
}

// Audit is a SQLite-backed audit log. Use OpenAudit; Close when done.
type Audit struct {
	db *sql.DB
}

// OpenAudit opens (creating if needed) the audit store at path. Use
// ":memory:" for an ephemeral store.
func OpenAudit(path string) (*Audit, error) {
	db, err := sql.Open("sqlite", path)
	if err != nil {
		return nil, fmt.Errorf("audit: open %q: %w", path, err)
	}
	// A single connection keeps ":memory:" databases consistent: with a
	// pool, each new connection would see a fresh, schema-less database.
	db.SetMaxOpenConns(1)
	const schema = `CREATE TABLE IF NOT EXISTS audit (
		id INTEGER PRIMARY KEY AUTOINCREMENT,
		ts TEXT NOT NULL,
		request_id TEXT NOT NULL,
		conn_id TEXT NOT NULL,
		phase TEXT NOT NULL,
		detail TEXT NOT NULL
	)`
	if _, err := db.Exec(schema); err != nil {
		db.Close()
		return nil, fmt.Errorf("audit: schema: %w", err)
	}
	return &Audit{db: db}, nil
}

// Write appends rows in one transaction.
func (a *Audit) Write(rows []AuditRow) error {
	if len(rows) == 0 {
		return nil
	}
	tx, err := a.db.Begin()
	if err != nil {
		return fmt.Errorf("audit: begin: %w", err)
	}
	stmt, err := tx.Prepare(
		`INSERT INTO audit (ts, request_id, conn_id, phase, detail) VALUES (?, ?, ?, ?, ?)`)
	if err != nil {
		tx.Rollback()
		return fmt.Errorf("audit: prepare: %w", err)
	}
	defer stmt.Close()
	now := time.Now().UTC().Format(time.RFC3339Nano)
	for _, r := range rows {
		if _, err := stmt.Exec(now, r.RequestID, r.ConnID, r.Phase, r.Detail); err != nil {
			tx.Rollback()
			return fmt.Errorf("audit: insert: %w", err)
		}
	}
	if err := tx.Commit(); err != nil {
		return fmt.Errorf("audit: commit: %w", err)
	}
	return nil
}

// RowsFor returns all audit rows for one request, oldest first.
func (a *Audit) RowsFor(requestID string) ([]AuditRow, error) {
	rows, err := a.db.Query(
		`SELECT request_id, conn_id, phase, detail FROM audit WHERE request_id = ? ORDER BY id`,
		requestID)
	if err != nil {
		return nil, fmt.Errorf("audit: query: %w", err)
	}
	defer rows.Close()
	var out []AuditRow
	for rows.Next() {
		var r AuditRow
		if err := rows.Scan(&r.RequestID, &r.ConnID, &r.Phase, &r.Detail); err != nil {
			return nil, fmt.Errorf("audit: scan: %w", err)
		}
		out = append(out, r)
	}
	return out, rows.Err()
}

// Failures returns the detail of every row with phase "failure".
func (a *Audit) Failures() ([]AuditRow, error) {
	rows, err := a.db.Query(
		`SELECT request_id, conn_id, phase, detail FROM audit WHERE phase = 'failure' ORDER BY id`)
	if err != nil {
		return nil, fmt.Errorf("audit: query: %w", err)
	}
	defer rows.Close()
	var out []AuditRow
	for rows.Next() {
		var r AuditRow
		if err := rows.Scan(&r.RequestID, &r.ConnID, &r.Phase, &r.Detail); err != nil {
			return nil, fmt.Errorf("audit: scan: %w", err)
		}
		out = append(out, r)
	}
	return out, rows.Err()
}

// Close closes the underlying store.
func (a *Audit) Close() error { return a.db.Close() }
