// Package store persists per-request HPACK observations in a local SQLite
// database. It is deliberately write-mostly and small: the point is an
// auditable, queryable record correlated by request identity, not a
// general-purpose data layer.
package store

import (
	"context"
	"database/sql"
	"fmt"
	"time"

	_ "modernc.org/sqlite"
)

// Store wraps one SQLite database.
type Store struct {
	db *sql.DB
}

// Open opens (creating if needed) the SQLite database at dsn and applies
// the schema. Use ":memory:" for tests.
func Open(ctx context.Context, dsn string) (*Store, error) {
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, fmt.Errorf("open sqlite: %w", err)
	}
	// modernc.org/sqlite is safe with one writer; keep the pool at 1 to
	// avoid SQLITE_BUSY under concurrent connections.
	db.SetMaxOpenConns(1)
	s := &Store{db: db}
	if err := s.init(ctx); err != nil {
		db.Close()
		return nil, err
	}
	return s, nil
}

func (s *Store) init(ctx context.Context) error {
	stmts := []string{
		`CREATE TABLE IF NOT EXISTS requests (
			id            TEXT PRIMARY KEY,
			conn_id       TEXT NOT NULL,
			stream_id     INTEGER NOT NULL,
			received_at   TEXT NOT NULL,
			block_bytes   INTEGER NOT NULL,
			field_count   INTEGER NOT NULL,
			emitted_bytes INTEGER NOT NULL,
			status        TEXT NOT NULL,
			error_kind    TEXT,
			error_detail  TEXT
		)`,
		`CREATE TABLE IF NOT EXISTS headers (
			request_id TEXT NOT NULL,
			seq        INTEGER NOT NULL,
			name       TEXT NOT NULL,
			value      TEXT NOT NULL,
			sensitive  INTEGER NOT NULL,
			FOREIGN KEY(request_id) REFERENCES requests(id)
		)`,
		`CREATE INDEX IF NOT EXISTS idx_requests_conn ON requests(conn_id, received_at)`,
	}
	for _, q := range stmts {
		if _, err := s.db.ExecContext(ctx, q); err != nil {
			return fmt.Errorf("apply schema: %w", err)
		}
	}
	return nil
}

// Close releases the database handle.
func (s *Store) Close() error { return s.db.Close() }

// Result is one decoded request to persist.
type Result struct {
	ID           string
	ConnID       string
	StreamID     uint32
	BlockBytes   int
	EmittedBytes int
	Fields       []Field
	// Failed marks a decoding error; Kind/Detail explain the category.
	Failed bool
	Kind   string
	Detail string
}

// Field is one emitted header.
type Field struct {
	Name      string
	Value     string
	Sensitive bool
}

// RecordResult persists one request atomically (request row + header rows).
func (s *Store) RecordResult(ctx context.Context, r Result) error {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("begin tx: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	status := "ok"
	if r.Failed {
		status = "error"
	}
	_, err = tx.ExecContext(ctx,
		`INSERT INTO requests(id, conn_id, stream_id, received_at, block_bytes,
			field_count, emitted_bytes, status, error_kind, error_detail)
		 VALUES(?,?,?,?,?,?,?,?,?,?)`,
		r.ID, r.ConnID, int64(r.StreamID), time.Now().UTC().Format(time.RFC3339Nano),
		int64(r.BlockBytes), int64(len(r.Fields)), int64(r.EmittedBytes),
		status, nullIfEmpty(r.Kind), nullIfEmpty(r.Detail),
	)
	if err != nil {
		return fmt.Errorf("insert request: %w", err)
	}
	for i, f := range r.Fields {
		sens := 0
		if f.Sensitive {
			sens = 1
		}
		_, err = tx.ExecContext(ctx,
			`INSERT INTO headers(request_id, seq, name, value, sensitive)
			 VALUES(?,?,?,?,?)`, r.ID, i, f.Name, f.Value, sens)
		if err != nil {
			return fmt.Errorf("insert header %d: %w", i, err)
		}
	}
	return tx.Commit()
}

func nullIfEmpty(s string) any {
	if s == "" {
		return nil
	}
	return s
}

// RequestRow is one row from ListRequests.
type RequestRow struct {
	ID           string
	ConnID       string
	StreamID     uint32
	ReceivedAt   string
	BlockBytes   int64
	FieldCount   int64
	EmittedBytes int64
	Status       string
	ErrorKind    sql.NullString
	ErrorDetail  sql.NullString
}

// ListRequests returns the most recent recorded requests, newest first.
func (s *Store) ListRequests(ctx context.Context, limit int) ([]RequestRow, error) {
	if limit <= 0 {
		limit = 50
	}
	rows, err := s.db.QueryContext(ctx,
		`SELECT id, conn_id, stream_id, received_at, block_bytes, field_count,
		        emitted_bytes, status, error_kind, error_detail
		 FROM requests ORDER BY received_at DESC, id DESC LIMIT ?`, limit)
	if err != nil {
		return nil, fmt.Errorf("query requests: %w", err)
	}
	defer rows.Close()
	var out []RequestRow
	for rows.Next() {
		var r RequestRow
		if err := rows.Scan(&r.ID, &r.ConnID, &r.StreamID, &r.ReceivedAt,
			&r.BlockBytes, &r.FieldCount, &r.EmittedBytes,
			&r.Status, &r.ErrorKind, &r.ErrorDetail); err != nil {
			return nil, fmt.Errorf("scan request: %w", err)
		}
		out = append(out, r)
	}
	return out, rows.Err()
}

// CountByStatus returns how many requests were recorded per status.
func (s *Store) CountByStatus(ctx context.Context) (map[string]int, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT status, COUNT(*) FROM requests GROUP BY status`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := map[string]int{}
	for rows.Next() {
		var k string
		var n int
		if err := rows.Scan(&k, &n); err != nil {
			return nil, err
		}
		out[k] = n
	}
	return out, rows.Err()
}
