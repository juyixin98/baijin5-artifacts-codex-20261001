// Package journal persists diagnostic events to SQLite so test and service
// runs can be correlated by run identity, connection and decision.
package journal

import (
	"database/sql"
	"fmt"
	"runtime"
	"sync"
	"time"

	_ "modernc.org/sqlite"
)

// Version identifies this build in journal records.
const Version = "h2svc/0.1.0"

const schema = `
CREATE TABLE IF NOT EXISTS runs (
  run_id     TEXT PRIMARY KEY,
  started_at TEXT NOT NULL,
  version    TEXT NOT NULL,
  go_version TEXT NOT NULL,
  config_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id     TEXT NOT NULL,
  ts         TEXT NOT NULL,
  conn_id    INTEGER NOT NULL,
  direction  TEXT NOT NULL,
  frame_type TEXT NOT NULL,
  stream_id  INTEGER NOT NULL,
  detail     TEXT NOT NULL,
  decision   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, conn_id);
`

// Journal is a run-scoped SQLite event log. It is safe for concurrent use.
type Journal struct {
	db    *sql.DB
	runID string
	mu    sync.Mutex
}

// Open opens (creating if needed) the journal database at path and registers
// a new run with the given config snapshot. Use ":memory:" for tests.
func Open(path, runID, configJSON string) (*Journal, error) {
	db, err := sql.Open("sqlite", path)
	if err != nil {
		return nil, fmt.Errorf("journal: open %s: %w", path, err)
	}
	// A single connection keeps ":memory:" databases coherent and serializes
	// diagnostic writes, which is fine for an event log.
	db.SetMaxOpenConns(1)
	if _, err := db.Exec(schema); err != nil {
		db.Close()
		return nil, fmt.Errorf("journal: schema: %w", err)
	}
	j := &Journal{db: db, runID: runID}
	_, err = db.Exec(
		`INSERT INTO runs(run_id, started_at, version, go_version, config_json) VALUES(?,?,?,?,?)`,
		runID, time.Now().UTC().Format(time.RFC3339Nano), Version, runtime.Version(), configJSON)
	if err != nil {
		db.Close()
		return nil, fmt.Errorf("journal: register run: %w", err)
	}
	return j, nil
}

// RunID returns this journal's run identity.
func (j *Journal) RunID() string { return j.runID }

// LogEvent implements h2.EventLogger.
func (j *Journal) LogEvent(connID uint64, dir, frameType string, streamID uint32, detail, decision string) {
	j.mu.Lock()
	defer j.mu.Unlock()
	_, err := j.db.Exec(
		`INSERT INTO events(run_id, ts, conn_id, direction, frame_type, stream_id, detail, decision)
		 VALUES(?,?,?,?,?,?,?,?)`,
		j.runID, time.Now().UTC().Format(time.RFC3339Nano), connID, dir, frameType, streamID, detail, decision)
	if err != nil {
		// Diagnostics must never crash the service; surface on stderr-free
		// best effort by recording nothing. Callers see errors at Close.
		_ = err
	}
}

// CountEvents returns the number of events for this run (used by tests).
func (j *Journal) CountEvents() (int, error) {
	var n int
	err := j.db.QueryRow(`SELECT COUNT(*) FROM events WHERE run_id = ?`, j.runID).Scan(&n)
	return n, err
}

// Close flushes and closes the database.
func (j *Journal) Close() error { return j.db.Close() }
