// Package store persists request audit records to SQLite. Two drivers are
// supported: modernc.org/sqlite (pure Go, default) and
// github.com/mattn/go-sqlite3 (cgo, opt-in). All SQL uses parameters;
// the only identifier interpolated is the config-validated table name.
package store

import (
	"database/sql"
	"fmt"

	_ "modernc.org/sqlite"
)

const moderncDriver = "modernc-sqlite"

// Store is the request audit log.
type Store struct {
	db     *sql.DB
	table  string
	driver string
}

// Record is one audited codec request/result pair.
type Record struct {
	RunID          string
	Op             string // decode | encode | validate
	Mode           string // BER | DER
	Status         string // ok | error | rejected
	ErrorKind      string
	ErrorOffset    int64
	InputLen       int
	InputSHA256    string
	OutputLen      int
	DurationMicros int64
}

// Open opens the database (creating the data directory's DB file via DSN)
// and runs the schema migration.
func Open(driver, dsn, table string, maxOpen int) (*Store, error) {
	driverName := "sqlite"
	switch driver {
	case moderncDriver:
		driverName = "sqlite"
	case "mattn-sqlite3":
		// registered via a separate build-tagged file to keep cgo optional
		driverName = mattnDriverName
	default:
		return nil, fmt.Errorf("unknown driver %q", driver)
	}

	db, err := sql.Open(driverName, dsn)
	if err != nil {
		return nil, fmt.Errorf("open sqlite: %w", err)
	}
	if maxOpen > 0 {
		db.SetMaxOpenConns(maxOpen)
	}
	if err := db.Ping(); err != nil {
		_ = db.Close()
		return nil, fmt.Errorf("ping sqlite: %w", err)
	}
	s := &Store{db: db, table: table, driver: driver}
	if err := s.migrate(); err != nil {
		_ = db.Close()
		return nil, err
	}
	return s, nil
}

func (s *Store) migrate() error {
	ddl := fmt.Sprintf(`
CREATE TABLE IF NOT EXISTS %s (
	id INTEGER PRIMARY KEY AUTOINCREMENT,
	run_id TEXT NOT NULL,
	op TEXT NOT NULL,
	mode TEXT NOT NULL,
	status TEXT NOT NULL,
	error_kind TEXT NOT NULL DEFAULT '',
	error_offset INTEGER NOT NULL DEFAULT -1,
	input_len INTEGER NOT NULL,
	input_sha256 TEXT NOT NULL,
	output_len INTEGER NOT NULL DEFAULT 0,
	duration_micros INTEGER NOT NULL,
	created_at TEXT NOT NULL DEFAULT (strftime('%%Y-%%m-%%dT%%H:%%M:%%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_%s_run ON %s(run_id);
CREATE INDEX IF NOT EXISTS idx_%s_status ON %s(status, created_at);`,
		s.table, s.table, s.table, s.table, s.table)
	if _, err := s.db.Exec(ddl); err != nil {
		return fmt.Errorf("migrate: %w", err)
	}
	return nil
}

// Insert writes one audit record in a short transaction.
func (s *Store) Insert(r Record) error {
	q := fmt.Sprintf(`INSERT INTO %s
(run_id, op, mode, status, error_kind, error_offset, input_len, input_sha256, output_len, duration_micros)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`, s.table)
	_, err := s.db.Exec(q,
		r.RunID, r.Op, r.Mode, r.Status, r.ErrorKind, r.ErrorOffset,
		r.InputLen, r.InputSHA256, r.OutputLen, r.DurationMicros)
	if err != nil {
		return fmt.Errorf("insert record: %w", err)
	}
	return nil
}

// Recent returns up to limit recent records (newest first).
func (s *Store) Recent(limit int) ([]Record, error) {
	if limit <= 0 || limit > 1000 {
		limit = 100
	}
	q := fmt.Sprintf(`SELECT run_id, op, mode, status, error_kind, error_offset,
	input_len, input_sha256, output_len, duration_micros
	FROM %s ORDER BY id DESC LIMIT ?`, s.table)
	rows, err := s.db.Query(q, limit)
	if err != nil {
		return nil, fmt.Errorf("query records: %w", err)
	}
	defer rows.Close()
	var out []Record
	for rows.Next() {
		var r Record
		if err := rows.Scan(&r.RunID, &r.Op, &r.Mode, &r.Status, &r.ErrorKind,
			&r.ErrorOffset, &r.InputLen, &r.InputSHA256, &r.OutputLen, &r.DurationMicros); err != nil {
			return nil, err
		}
		out = append(out, r)
	}
	return out, rows.Err()
}

func (s *Store) Close() error { return s.db.Close() }
