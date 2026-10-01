// Package store persists the whitelist ruleset and the per-request audit
// trail in SQLite. The pure-Go modernc.org/sqlite driver is used so the
// build needs no C toolchain at runtime.
package store

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"os"
	"path/filepath"

	_ "modernc.org/sqlite"

	"socks5d.local/socks5d/internal/config"
)

// Store is the rule and audit database.
type Store struct {
	db *sql.DB
}

// AuditRow is one explainable request outcome.
type AuditRow struct {
	ReqID     string
	TS        string
	Client    string
	Target    string
	Stage     string
	Result    string
	Reason    string
	Detail    string
	BytesUp   int64
	BytesDown int64
}

// Open opens (creating the schema in) the database at path.
func Open(ctx context.Context, path string) (*Store, error) {
	// SQLite does not create parent directories; create them with tight
	// permissions so a configured nested path (e.g. data/socks5d.db) works.
	if dir := filepath.Dir(path); dir != "." && dir != "" {
		if err := os.MkdirAll(dir, 0o700); err != nil {
			return nil, fmt.Errorf("create database directory %s: %w", dir, err)
		}
	}
	// _txlock=immediate avoids SQLITE_BUSY during concurrent writers.
	dsn := "file:" + path + "?_pragma=busy_timeout(5000)&_pragma=journal_mode(WAL)&_txlock=immediate"
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, fmt.Errorf("open sqlite %s: %w", path, err)
	}
	// SQLite permits one writer; serialize writes through a single connection
	// while reads remain cheap and local.
	db.SetMaxOpenConns(1)
	s := &Store{db: db}
	if err := s.migrate(ctx); err != nil {
		_ = db.Close()
		return nil, err
	}
	return s, nil
}

func (s *Store) migrate(ctx context.Context) error {
	stmts := []string{
		`CREATE TABLE IF NOT EXISTS schema_meta (
			key TEXT PRIMARY KEY,
			value TEXT NOT NULL
		)`,
		`CREATE TABLE IF NOT EXISTS rules (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			kind TEXT NOT NULL,
			value TEXT NOT NULL,
			mode TEXT NOT NULL DEFAULT '',
			note TEXT NOT NULL DEFAULT '',
			enabled INTEGER NOT NULL DEFAULT 1,
			UNIQUE(kind, value, mode)
		)`,
		`CREATE TABLE IF NOT EXISTS audit (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			req_id TEXT NOT NULL,
			ts TEXT NOT NULL,
			client TEXT NOT NULL DEFAULT '',
			target TEXT NOT NULL DEFAULT '',
			stage TEXT NOT NULL,
			result TEXT NOT NULL,
			reason TEXT NOT NULL DEFAULT '',
			detail TEXT NOT NULL DEFAULT '',
			bytes_up INTEGER NOT NULL DEFAULT 0,
			bytes_down INTEGER NOT NULL DEFAULT 0
		)`,
		`CREATE INDEX IF NOT EXISTS idx_audit_req ON audit(req_id)`,
		`CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit(ts)`,
	}
	for _, q := range stmts {
		if _, err := s.db.ExecContext(ctx, q); err != nil {
			return fmt.Errorf("migrate: %w", err)
		}
	}
	return nil
}

// Close releases the database handle.
func (s *Store) Close() error { return s.db.Close() }

// ReplaceRules atomically replaces the enabled ruleset with rules.
func (s *Store) ReplaceRules(ctx context.Context, rules []config.Rule) error {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("begin rules tx: %w", err)
	}
	defer func() { _ = tx.Rollback() }()
	if _, err := tx.ExecContext(ctx, `DELETE FROM rules`); err != nil {
		return fmt.Errorf("clear rules: %w", err)
	}
	stmt, err := tx.PrepareContext(ctx,
		`INSERT INTO rules(kind, value, mode, note, enabled) VALUES(?, ?, ?, ?, 1)`)
	if err != nil {
		return fmt.Errorf("prepare rules insert: %w", err)
	}
	defer stmt.Close()
	for _, r := range rules {
		if _, err := stmt.ExecContext(ctx, r.Kind, r.Value, r.Mode, r.Note); err != nil {
			return fmt.Errorf("insert rule %s %s: %w", r.Kind, r.Value, err)
		}
	}
	if err := tx.Commit(); err != nil {
		return fmt.Errorf("commit rules: %w", err)
	}
	return nil
}

// ListRules returns the persisted ruleset.
func (s *Store) ListRules(ctx context.Context) ([]config.Rule, error) {
	rows, err := s.db.QueryContext(ctx,
		`SELECT kind, value, mode, note FROM rules WHERE enabled = 1 ORDER BY id`)
	if err != nil {
		return nil, fmt.Errorf("query rules: %w", err)
	}
	defer rows.Close()
	var out []config.Rule
	for rows.Next() {
		var r config.Rule
		if err := rows.Scan(&r.Kind, &r.Value, &r.Mode, &r.Note); err != nil {
			return nil, fmt.Errorf("scan rule: %w", err)
		}
		out = append(out, r)
	}
	return out, rows.Err()
}

// InsertAudit writes one audit record.
func (s *Store) InsertAudit(ctx context.Context, a AuditRow) error {
	_, err := s.db.ExecContext(ctx,
		`INSERT INTO audit(req_id, ts, client, target, stage, result, reason, detail, bytes_up, bytes_down)
		 VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		a.ReqID, a.TS, a.Client, a.Target, a.Stage, a.Result, a.Reason, a.Detail, a.BytesUp, a.BytesDown)
	if err != nil {
		return fmt.Errorf("insert audit: %w", err)
	}
	return nil
}

// AuditTrail returns recorded rows, optionally filtered by request id.
func (s *Store) AuditTrail(ctx context.Context, reqID string) ([]AuditRow, error) {
	q := `SELECT req_id, ts, client, target, stage, result, reason, detail, bytes_up, bytes_down FROM audit`
	args := []any{}
	if reqID != "" {
		q += ` WHERE req_id = ?`
		args = append(args, reqID)
	}
	q += ` ORDER BY id`
	rows, err := s.db.QueryContext(ctx, q, args...)
	if err != nil {
		return nil, fmt.Errorf("query audit: %w", err)
	}
	defer rows.Close()
	var out []AuditRow
	for rows.Next() {
		var a AuditRow
		if err := rows.Scan(&a.ReqID, &a.TS, &a.Client, &a.Target, &a.Stage,
			&a.Result, &a.Reason, &a.Detail, &a.BytesUp, &a.BytesDown); err != nil {
			return nil, fmt.Errorf("scan audit: %w", err)
		}
		out = append(out, a)
	}
	return out, rows.Err()
}

// ErrNotFound is returned by single-row helpers.
var ErrNotFound = errors.New("not found")
