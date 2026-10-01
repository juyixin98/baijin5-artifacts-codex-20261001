package storage

import (
	"crypto/rand"
	"database/sql"
	"encoding/hex"
	"fmt"
	"os"
	"path/filepath"
	"time"

	_ "modernc.org/sqlite"
)

// SQLiteStore persists messages as .eml files under <dir>/messages and
// indexes them in a SQLite database at <dir>/sink.db. Both must succeed
// before Commit reports success.
type SQLiteStore struct {
	db     *sql.DB
	tmpDir string
	msgDir string
}

// Message is one stored message as seen by local inspection tooling.
type Message struct {
	ID         string
	MailFrom   string
	RcptTo     []string
	SizeBytes  int64
	Path       string
	ReceivedAt time.Time
}

// OpenSQLite opens (creating if needed) a store rooted at dir.
func OpenSQLite(dir string) (*SQLiteStore, error) {
	s := &SQLiteStore{
		tmpDir: filepath.Join(dir, "tmp"),
		msgDir: filepath.Join(dir, "messages"),
	}
	for _, d := range []string{s.tmpDir, s.msgDir} {
		if err := os.MkdirAll(d, 0o755); err != nil {
			return nil, fmt.Errorf("storage: create %s: %w", d, err)
		}
	}
	dsn := fmt.Sprintf("file:%s?_pragma=busy_timeout(5000)&_pragma=journal_mode(WAL)&_pragma=synchronous(FULL)",
		filepath.Join(dir, "sink.db"))
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, fmt.Errorf("storage: open db: %w", err)
	}
	s.db = db
	if err := s.migrate(); err != nil {
		db.Close()
		return nil, err
	}
	return s, nil
}

func (s *SQLiteStore) migrate() error {
	const schema = `
CREATE TABLE IF NOT EXISTS messages (
  id          TEXT PRIMARY KEY,
  mail_from   TEXT NOT NULL,
  size_bytes  INTEGER NOT NULL,
  path        TEXT NOT NULL,
  received_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recipients (
  message_id TEXT NOT NULL REFERENCES messages(id),
  address    TEXT NOT NULL,
  PRIMARY KEY (message_id, address)
);`
	if _, err := s.db.Exec(schema); err != nil {
		return fmt.Errorf("storage: migrate: %w", err)
	}
	return nil
}

// Close releases the database handle.
func (s *SQLiteStore) Close() error { return s.db.Close() }

// Begin creates a temporary spool file for a new message.
func (s *SQLiteStore) Begin(env Envelope) (Pending, error) {
	f, err := os.CreateTemp(s.tmpDir, "spool-*.tmp")
	if err != nil {
		return nil, fmt.Errorf("storage: spool: %w", err)
	}
	return &pending{store: s, env: env, tmp: f, tmpPath: f.Name()}, nil
}

type pending struct {
	store   *SQLiteStore
	env     Envelope
	tmp     *os.File
	tmpPath string
	size    int64
	done    bool
}

func (p *pending) Write(b []byte) (int, error) {
	if p.done {
		return 0, fmt.Errorf("storage: write after finalize")
	}
	n, err := p.tmp.Write(b)
	p.size += int64(n)
	return n, err
}

// Commit fsyncs the spool file, atomically renames it into the messages
// directory, fsyncs the directory, and only then indexes the message in
// SQLite inside one transaction. Any failure cleans up and reports error.
func (p *pending) Commit() (string, error) {
	if p.done {
		return "", fmt.Errorf("storage: commit twice")
	}
	p.done = true
	id := newMessageID()
	final := filepath.Join(p.store.msgDir, id+".eml")

	cleanup := func(err error) (string, error) {
		p.tmp.Close()
		os.Remove(p.tmpPath)
		os.Remove(final)
		return "", err
	}

	if err := p.tmp.Sync(); err != nil {
		return cleanup(fmt.Errorf("storage: fsync spool: %w", err))
	}
	if err := p.tmp.Close(); err != nil {
		return cleanup(fmt.Errorf("storage: close spool: %w", err))
	}
	if err := os.Rename(p.tmpPath, final); err != nil {
		return cleanup(fmt.Errorf("storage: rename into place: %w", err))
	}
	if err := syncDir(p.store.msgDir); err != nil {
		return cleanup(fmt.Errorf("storage: fsync dir: %w", err))
	}

	tx, err := p.store.db.Begin()
	if err != nil {
		return cleanup(fmt.Errorf("storage: begin tx: %w", err))
	}
	defer tx.Rollback()
	if _, err := tx.Exec(
		`INSERT INTO messages (id, mail_from, size_bytes, path, received_at) VALUES (?,?,?,?,?)`,
		id, p.env.MailFrom, p.size, final, time.Now().UTC().Format(time.RFC3339Nano),
	); err != nil {
		return cleanup(fmt.Errorf("storage: index message: %w", err))
	}
	// Repeated RCPT to the same address is accepted at the protocol layer
	// but delivered once: the delivery scope is the deduplicated set.
	for _, rcpt := range p.env.RcptTo {
		if _, err := tx.Exec(`INSERT OR IGNORE INTO recipients (message_id, address) VALUES (?,?)`,
			id, rcpt); err != nil {
			return cleanup(fmt.Errorf("storage: index recipient: %w", err))
		}
	}
	if err := tx.Commit(); err != nil {
		return cleanup(fmt.Errorf("storage: commit tx: %w", err))
	}
	return id, nil
}

// Abort discards the spool file.
func (p *pending) Abort() error {
	if p.done {
		return nil
	}
	p.done = true
	p.tmp.Close()
	return os.Remove(p.tmpPath)
}

// GetMessage loads one indexed message for local inspection.
func (s *SQLiteStore) GetMessage(id string) (Message, error) {
	var m Message
	var received string
	row := s.db.QueryRow(`SELECT id, mail_from, size_bytes, path, received_at FROM messages WHERE id = ?`, id)
	if err := row.Scan(&m.ID, &m.MailFrom, &m.SizeBytes, &m.Path, &received); err != nil {
		return Message{}, fmt.Errorf("storage: get %s: %w", id, err)
	}
	t, err := time.Parse(time.RFC3339Nano, received)
	if err != nil {
		return Message{}, fmt.Errorf("storage: parse received_at: %w", err)
	}
	m.ReceivedAt = t
	rows, err := s.db.Query(`SELECT address FROM recipients WHERE message_id = ? ORDER BY address`, id)
	if err != nil {
		return Message{}, fmt.Errorf("storage: get recipients: %w", err)
	}
	defer rows.Close()
	for rows.Next() {
		var a string
		if err := rows.Scan(&a); err != nil {
			return Message{}, err
		}
		m.RcptTo = append(m.RcptTo, a)
	}
	return m, rows.Err()
}

// ListMessages returns all indexed messages, oldest first.
func (s *SQLiteStore) ListMessages() ([]Message, error) {
	rows, err := s.db.Query(`SELECT id FROM messages ORDER BY received_at, id`)
	if err != nil {
		return nil, fmt.Errorf("storage: list: %w", err)
	}
	defer rows.Close()
	var out []Message
	for rows.Next() {
		var id string
		if err := rows.Scan(&id); err != nil {
			return nil, err
		}
		m, err := s.GetMessage(id)
		if err != nil {
			return nil, err
		}
		out = append(out, m)
	}
	return out, rows.Err()
}

// TmpDir and MsgDir expose the spool and message directories so tests and
// operators can verify no partial artifacts remain after failures.
func (s *SQLiteStore) TmpDir() string { return s.tmpDir }
func (s *SQLiteStore) MsgDir() string { return s.msgDir }

func newMessageID() string {
	var b [8]byte
	if _, err := rand.Read(b[:]); err != nil {
		panic(fmt.Sprintf("storage: crypto/rand unavailable: %v", err))
	}
	return fmt.Sprintf("msg-%d-%s", time.Now().UTC().UnixNano(), hex.EncodeToString(b[:]))
}

func syncDir(dir string) error {
	d, err := os.Open(dir)
	if err != nil {
		return err
	}
	defer d.Close()
	return d.Sync()
}
