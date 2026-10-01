package policy

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"net/netip"
	"strings"
	"time"
)

// RequestRecord is one durable, explainable record per client request.
// Fields are exported so the server layer and independent tests can build
// and inspect records without reaching into SQL.
type RequestRecord struct {
	RequestID   string
	StartedAt   time.Time
	FinishedAt  time.Time
	ClientAddr  string
	Username    string
	Stage       string
	TargetHost  string
	TargetPort  int
	Resolved    []netip.Addr
	Attempts    []map[string]string
	OutcomeKind string
	Detail      string
	BytesUp     int64
	BytesDown   int64
	DurationMS  int64
}

// LogRequest inserts a record at request start. The returned row id lets
// FinishRequest update the same row. Unknown/aborted sessions therefore
// always leave a trace rather than vanishing silently.
func (s *Store) LogRequest(ctx context.Context, rec RequestRecord) (int64, error) {
	if rec.StartedAt.IsZero() {
		rec.StartedAt = time.Now().UTC()
	}
	res, err := s.db.ExecContext(ctx,
		`INSERT INTO requests(request_id, started_at, finished_at, client_addr, username, stage,
			target_host, target_port, resolved, attempts, outcome_kind, detail,
			bytes_up, bytes_down, duration_ms)
		 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
		rec.RequestID,
		rec.StartedAt.Format(time.RFC3339Nano),
		nilTime(rec.FinishedAt),
		rec.ClientAddr, rec.Username, rec.Stage,
		rec.TargetHost, rec.TargetPort,
		addrsJSON(rec.Resolved), attemptsJSON(rec.Attempts),
		rec.OutcomeKind, rec.Detail,
		rec.BytesUp, rec.BytesDown, rec.DurationMS)
	if err != nil {
		return 0, fmt.Errorf("policy: log request: %w", err)
	}
	id, err := res.LastInsertId()
	if err != nil {
		return 0, fmt.Errorf("policy: log request id: %w", err)
	}
	return id, nil
}

// FinishRequest updates the terminal fields of a previously logged request.
func (s *Store) FinishRequest(ctx context.Context, rowID int64, rec RequestRecord) error {
	if rec.FinishedAt.IsZero() {
		rec.FinishedAt = time.Now().UTC()
	}
	if !rec.StartedAt.IsZero() {
		rec.DurationMS = rec.FinishedAt.Sub(rec.StartedAt).Milliseconds()
	}
	_, err := s.db.ExecContext(ctx,
		`UPDATE requests SET finished_at=?, stage=?, outcome_kind=?, detail=?,
			resolved=?, attempts=?, bytes_up=?, bytes_down=?, duration_ms=?,
			target_host=?, target_port=?, username=?
		 WHERE id=?`,
		rec.FinishedAt.Format(time.RFC3339Nano),
		rec.Stage, rec.OutcomeKind, rec.Detail,
		addrsJSON(rec.Resolved), attemptsJSON(rec.Attempts),
		rec.BytesUp, rec.BytesDown, rec.DurationMS,
		rec.TargetHost, rec.TargetPort, rec.Username, rowID)
	if err != nil {
		return fmt.Errorf("policy: finish request: %w", err)
	}
	return nil
}

// LogRow is an independent read model used by tests and operators; it is
// decoded directly from SQLite, never produced by the code under test.
type LogRow struct {
	ID          int64
	RequestID   string
	StartedAt   string
	FinishedAt  sql.NullString
	ClientAddr  string
	Username    string
	Stage       string
	TargetHost  string
	TargetPort  int
	Resolved    string
	Attempts    string
	OutcomeKind string
	Detail      string
	BytesUp     int64
	BytesDown   int64
	DurationMS  int64
}

// QueryRequests runs an independent SELECT against the durable log.
func (s *Store) QueryRequests(ctx context.Context, where string, args ...any) ([]LogRow, error) {
	q := `SELECT id, request_id, started_at, finished_at, client_addr, username, stage,
			target_host, target_port, resolved, attempts, outcome_kind, detail,
			bytes_up, bytes_down, duration_ms
	      FROM requests ` + where
	if !strings.Contains(strings.ToUpper(where), "ORDER BY") {
		q += " ORDER BY id"
	}
	rows, err := s.db.QueryContext(ctx, q, args...)
	if err != nil {
		return nil, fmt.Errorf("policy: query requests: %w", err)
	}
	defer func() { _ = rows.Close() }()
	var out []LogRow
	for rows.Next() {
		var r LogRow
		if err := rows.Scan(&r.ID, &r.RequestID, &r.StartedAt, &r.FinishedAt, &r.ClientAddr,
			&r.Username, &r.Stage, &r.TargetHost, &r.TargetPort, &r.Resolved, &r.Attempts,
			&r.OutcomeKind, &r.Detail, &r.BytesUp, &r.BytesDown, &r.DurationMS); err != nil {
			return nil, fmt.Errorf("policy: scan log row: %w", err)
		}
		out = append(out, r)
	}
	return out, rows.Err()
}

func nilTime(t time.Time) any {
	if t.IsZero() {
		return nil
	}
	return t.Format(time.RFC3339Nano)
}

func addrsJSON(addrs []netip.Addr) string {
	if len(addrs) == 0 {
		return "[]"
	}
	s := make([]string, len(addrs))
	for i, a := range addrs {
		s[i] = a.String()
	}
	b, _ := json.Marshal(s)
	return string(b)
}

func attemptsJSON(attempts []map[string]string) string {
	if len(attempts) == 0 {
		return "[]"
	}
	b, _ := json.Marshal(attempts)
	return string(b)
}
