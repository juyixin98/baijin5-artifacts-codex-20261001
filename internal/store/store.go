// Package store persists resource representations in SQLite. Each stored
// representation carries an integer version and an ETag bound to its exact
// bytes; a PUT bumps the version and recomputes the ETag, so block-wise
// downloads spanning an update detect the change and refuse to splice.
package store

import (
	"database/sql"
	"errors"
	"fmt"
	"sync"
	"sync/atomic"

	"coaplab/internal/etag"

	_ "modernc.org/sqlite"
)

func atomicAdd() int64 { return atomic.AddInt64(&memSeq, 1) }

// Resource is one stored representation.
type Resource struct {
	Path          string
	ContentFormat uint16
	Body          []byte
	ETag          []byte
	Version       int64
}

// ErrNotFound is the 4.04 condition.
var ErrNotFound = errors.New("store: resource not found")

// Store is the resource database. A single mutex serialises writers in
// addition to SQLite's own locking; this is a local lab subset, not a
// throughput benchmark.
type Store struct {
	db *sql.DB
	mu sync.Mutex
}

var memSeq int64

// Open opens (creating the schema in) the SQLite file. Use ":memory:" for
// tests; each call gets its OWN private in-memory database (a unique
// mode=memory DSN, so parallel test stores never share rows).
func Open(path string) (*Store, error) {
	dsn := path
	if path == ":memory:" {
		dsn = fmt.Sprintf("file:coaplab_mem_%d?mode=memory&cache=shared", atomicAdd())
	}
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, fmt.Errorf("store: open: %w", err)
	}
	// modernc.org/sqlite is safe for concurrent use; keep pool modest.
	db.SetMaxOpenConns(4)
	s := &Store{db: db}
	if err := s.init(); err != nil {
		_ = db.Close()
		return nil, err
	}
	return s, nil
}

func (s *Store) init() error {
	stmts := []string{
		`CREATE TABLE IF NOT EXISTS resources (
			path           TEXT PRIMARY KEY,
			content_format INTEGER NOT NULL,
			body           BLOB   NOT NULL,
			etag           BLOB   NOT NULL,
			version        INTEGER NOT NULL,
			updated_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
		)`,
		`CREATE INDEX IF NOT EXISTS idx_resources_version ON resources(version)`,
	}
	for _, q := range stmts {
		if _, err := s.db.Exec(q); err != nil {
			return fmt.Errorf("store: schema: %w", err)
		}
	}
	return nil
}

// Close releases the database.
func (s *Store) Close() error { return s.db.Close() }

// Put atomically creates or replaces a representation and bumps version.
// The ETag is computed from the exact bytes, never supplied by the caller.
func (s *Store) Put(path string, contentFormat uint16, body []byte) (Resource, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	tx, err := s.db.Begin()
	if err != nil {
		return Resource{}, err
	}
	defer func() { _ = tx.Rollback() }()

	var prevVersion int64
	row := tx.QueryRow(`SELECT version FROM resources WHERE path = ?`, path)
	switch err := row.Scan(&prevVersion); {
	case err == nil:
	case errors.Is(err, sql.ErrNoRows):
		prevVersion = 0
	default:
		return Resource{}, err
	}

	tag := etag.Compute(contentFormat, body)
	res := Resource{
		Path:          path,
		ContentFormat: contentFormat,
		Body:          append([]byte(nil), body...),
		ETag:          tag,
		Version:       prevVersion + 1,
	}
	if len(res.Body) == 0 {
		res.Body = []byte{}
	}
	if _, err := tx.Exec(
		`INSERT INTO resources(path, content_format, body, etag, version)
		 VALUES(?, ?, ?, ?, ?)
		 ON CONFLICT(path) DO UPDATE SET
		   content_format=excluded.content_format,
		   body=excluded.body,
		   etag=excluded.etag,
		   version=excluded.version,
		   updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')`,
		res.Path, res.ContentFormat, res.Body, res.ETag, res.Version,
	); err != nil {
		return Resource{}, err
	}
	if err := tx.Commit(); err != nil {
		return Resource{}, err
	}
	return res, nil
}

// Get fetches a representation.
func (s *Store) Get(path string) (Resource, error) {
	var r Resource
	var cf int64
	err := s.db.QueryRow(
		`SELECT path, content_format, body, etag, version FROM resources WHERE path = ?`, path,
	).Scan(&r.Path, &cf, &r.Body, &r.ETag, &r.Version)
	if errors.Is(err, sql.ErrNoRows) {
		return Resource{}, ErrNotFound
	}
	if err != nil {
		return Resource{}, err
	}
	r.ContentFormat = uint16(cf)
	return r, nil
}

// Delete removes a representation, reporting whether it existed.
func (s *Store) Delete(path string) (bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	res, err := s.db.Exec(`DELETE FROM resources WHERE path = ?`, path)
	if err != nil {
		return false, err
	}
	n, _ := res.RowsAffected()
	return n > 0, nil
}

// Paths lists resource paths (diagnostics / .well-known sanity checks).
func (s *Store) Paths() ([]string, error) {
	rows, err := s.db.Query(`SELECT path FROM resources ORDER BY path`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []string
	for rows.Next() {
		var p string
		if err := rows.Scan(&p); err != nil {
			return nil, err
		}
		out = append(out, p)
	}
	return out, rows.Err()
}
