// Package store persists an audit record of every service request in SQLite.
package store

import (
	"context"
	"database/sql"
	"fmt"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

// Entry is one audited request.
type Entry struct {
	RunID       string
	RequestID   string
	Time        time.Time
	Remote      string
	Op          string
	InputSHA256 string
	InputBytes  int
	OK          bool
	Category    string // error category when !OK, else ""
	Offset      int    // error offset when !OK, else -1
	Message     string
	DurationUs  int64
}

const schema = `
CREATE TABLE IF NOT EXISTS audit_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT NOT NULL,
    request_id   TEXT NOT NULL,
    ts           TEXT NOT NULL,
    remote       TEXT NOT NULL,
    op           TEXT NOT NULL,
    input_sha256 TEXT NOT NULL,
    input_bytes  INTEGER NOT NULL,
    ok           INTEGER NOT NULL,
    category     TEXT NOT NULL,
    offset       INTEGER NOT NULL,
    message      TEXT NOT NULL,
    duration_us  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_run ON audit_log(run_id);
`

// Store writes audit entries to SQLite.
type Store struct {
	db *sql.DB
}

// Open opens (creating if necessary) the audit database at path.
func Open(path string) (*Store, error) {
	db, err := sql.Open("sqlite3", path+"?_journal_mode=WAL&_busy_timeout=5000")
	if err != nil {
		return nil, fmt.Errorf("open sqlite %s: %w", path, err)
	}
	if _, err := db.Exec(schema); err != nil {
		db.Close()
		return nil, fmt.Errorf("init schema: %w", err)
	}
	return &Store{db: db}, nil
}

// Log inserts one audit entry.
func (s *Store) Log(ctx context.Context, e Entry) error {
	_, err := s.db.ExecContext(ctx, `INSERT INTO audit_log
		(run_id, request_id, ts, remote, op, input_sha256, input_bytes,
		 ok, category, offset, message, duration_us)
		VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`,
		e.RunID, e.RequestID, e.Time.UTC().Format(time.RFC3339Nano), e.Remote,
		e.Op, e.InputSHA256, e.InputBytes, boolToInt(e.OK), e.Category,
		e.Offset, e.Message, e.DurationUs)
	if err != nil {
		return fmt.Errorf("insert audit entry: %w", err)
	}
	return nil
}

// Count returns the number of audit rows, optionally filtered by run ID.
func (s *Store) Count(ctx context.Context, runID string) (int, error) {
	var n int
	var err error
	if runID == "" {
		err = s.db.QueryRowContext(ctx, `SELECT COUNT(*) FROM audit_log`).Scan(&n)
	} else {
		err = s.db.QueryRowContext(ctx,
			`SELECT COUNT(*) FROM audit_log WHERE run_id = ?`, runID).Scan(&n)
	}
	return n, err
}

// Close closes the database.
func (s *Store) Close() error { return s.db.Close() }

func boolToInt(b bool) int {
	if b {
		return 1
	}
	return 0
}
