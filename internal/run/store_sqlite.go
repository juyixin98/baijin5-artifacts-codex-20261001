package run

import (
	"database/sql"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"ntpsim/internal/core"

	_ "modernc.org/sqlite"
)

// SQLiteStore persists runs into a local SQLite database (pure-Go driver:
// no CGO, no system dependency). Schema version is stored explicitly so a
// future migration path exists.
type SQLiteStore struct {
	db *sql.DB
}

const schema = `
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rounds (
    run_id        TEXT NOT NULL,
    round         INTEGER NOT NULL,
    virtual_time  TEXT NOT NULL,
    status        TEXT NOT NULL,
    reason        TEXT NOT NULL,
    selected      TEXT NOT NULL DEFAULT '',
    offset_ns     INTEGER NOT NULL DEFAULT 0,
    lower_ns      INTEGER NOT NULL DEFAULT 0,
    upper_ns      INTEGER NOT NULL DEFAULT 0,
    accepted      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, round)
);
CREATE TABLE IF NOT EXISTS samples (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        TEXT NOT NULL,
    round         INTEGER NOT NULL,
    source        TEXT NOT NULL,
    status        TEXT NOT NULL,
    reason        TEXT NOT NULL DEFAULT '',
    offset_ns     INTEGER NOT NULL,
    rtt_ns        INTEGER NOT NULL,
    lower_ns      INTEGER NOT NULL,
    upper_ns      INTEGER NOT NULL,
    stratum       INTEGER NOT NULL,
    collected_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_samples_run ON samples(run_id, round);
`

// OpenSQLite opens (creating if needed) the database at path and migrates the
// schema. Use path ":memory:" for tests.
func OpenSQLite(path string) (*SQLiteStore, error) {
	// _txlock=serializable makes the single-writer behavior deterministic.
	if path != ":memory:" {
		if dir := filepath.Dir(path); dir != "." && dir != "" {
			if err := os.MkdirAll(dir, 0o755); err != nil {
				return nil, fmt.Errorf("store: mkdir %s: %w", dir, err)
			}
		}
	}
	db, err := sql.Open("sqlite", path+"?_pragma=busy_timeout(5000)")
	if err != nil {
		return nil, fmt.Errorf("store: open: %w", err)
	}
	if _, err := db.Exec(schema); err != nil {
		_ = db.Close()
		return nil, fmt.Errorf("store: schema: %w", err)
	}
	if _, err := db.Exec(
		`INSERT INTO meta(key,value) VALUES('schema_version','1')
		 ON CONFLICT(key) DO NOTHING`); err != nil {
		_ = db.Close()
		return nil, fmt.Errorf("store: meta: %w", err)
	}
	return &SQLiteStore{db: db}, nil
}

// Close closes the database.
func (s *SQLiteStore) Close() error { return s.db.Close() }

// SaveRound writes the round and all samples in one transaction.
func (s *SQLiteStore) SaveRound(runID string, round int, at time.Time, samples []core.Sample, sel core.SelectionResult) error {
	tx, err := s.db.Begin()
	if err != nil {
		return fmt.Errorf("store: begin: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	_, err = tx.Exec(`INSERT INTO rounds
		(run_id, round, virtual_time, status, reason, selected, offset_ns, lower_ns, upper_ns, accepted)
		VALUES (?,?,?,?,?,?,?,?,?,?)`,
		runID, round, at.UTC().Format(time.RFC3339Nano),
		string(sel.Status), sel.Reason, sel.SourceID,
		int64(sel.Offset), int64(sel.Lower), int64(sel.Upper), sel.AcceptedCount)
	if err != nil {
		return fmt.Errorf("store: insert round: %w", err)
	}

	for _, sm := range samples {
		_, err = tx.Exec(`INSERT INTO samples
			(run_id, round, source, status, reason, offset_ns, rtt_ns, lower_ns, upper_ns, stratum, collected_at)
			VALUES (?,?,?,?,?,?,?,?,?,?,?)`,
			runID, round, sm.SourceID, string(sm.Status), sm.Reason,
			int64(sm.Offset), int64(sm.RTT), int64(sm.Lower), int64(sm.Upper),
			int(sm.Stratum), sm.CollectedAt.UTC().Format(time.RFC3339Nano))
		if err != nil {
			return fmt.Errorf("store: insert sample: %w", err)
		}
	}
	if err := tx.Commit(); err != nil {
		return fmt.Errorf("store: commit: %w", err)
	}
	return nil
}

// RoundRow is a persisted round summary (used by the CLI "query" command).
type RoundRow struct {
	Round       int
	VirtualTime string
	Status      string
	Reason      string
	Selected    string
	Offset      time.Duration
	Lower       time.Duration
	Upper       time.Duration
	Accepted    int
}

// QueryRounds returns persisted rounds for a run, oldest first.
func (s *SQLiteStore) QueryRounds(runID string) ([]RoundRow, error) {
	rows, err := s.db.Query(`SELECT round, virtual_time, status, reason, selected,
		offset_ns, lower_ns, upper_ns, accepted FROM rounds
		WHERE run_id=? ORDER BY round`, runID)
	if err != nil {
		return nil, fmt.Errorf("store: query rounds: %w", err)
	}
	defer rows.Close()
	var out []RoundRow
	for rows.Next() {
		var r RoundRow
		var off, lo, hi int64
		if err := rows.Scan(&r.Round, &r.VirtualTime, &r.Status, &r.Reason,
			&r.Selected, &off, &lo, &hi, &r.Accepted); err != nil {
			return nil, err
		}
		r.Offset = time.Duration(off)
		r.Lower = time.Duration(lo)
		r.Upper = time.Duration(hi)
		out = append(out, r)
	}
	return out, rows.Err()
}
