// Package storage persists accepted mail into a local SQLite database. It is
// the only component allowed to touch durable state: one transaction writes
// the message and one recipient-copy row per accepted mailbox, and the
// transaction's fsync commit is what authorizes the state machine's 250.
package storage

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"time"

	_ "modernc.org/sqlite"

	"smtpsink/internal/protocol"
)

// Store is a SQLite-backed protocol.Sink.
type Store struct {
	db       *sql.DB
	dsnPath  string
	maxBytes int64
	now      func() time.Time
}

// Options configures Open.
type Options struct {
	// Path is the SQLite database file. Use ":memory:" only for tests.
	Path string
	// MaxTotalBytes, when > 0, imposes a local quota across stored message
	// bodies; exceeding it yields protocol.ErrInsufficient.
	MaxTotalBytes int64
	// BusyTimeoutMS waits this long on database lock contention.
	BusyTimeoutMS int
}

// Open creates the schema and returns a ready Store.
func Open(ctx context.Context, opts Options) (*Store, error) {
	if opts.Path == "" {
		return nil, errors.New("storage: database path is required")
	}
	if opts.BusyTimeoutMS == 0 {
		opts.BusyTimeoutMS = 5000
	}
	// synchronous=FULL makes the commit wait for the fsync barrier; WAL keeps
	// readers/writers from blocking each other in tests.
	dsn := fmt.Sprintf(
		"file:%s?_pragma=busy_timeout(%d)&_pragma=journal_mode(WAL)&_pragma=synchronous(FULL)&_pragma=foreign_keys(ON)&_txlock=immediate",
		opts.Path, opts.BusyTimeoutMS)
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, fmt.Errorf("storage: open %s: %w", opts.Path, err)
	}
	// Single connection: SQLite writes are serialized anyway, and it makes
	// quota accounting race-free for this local single-process service.
	db.SetMaxOpenConns(1)
	if err := db.PingContext(ctx); err != nil {
		_ = db.Close()
		return nil, fmt.Errorf("storage: ping %s: %w", opts.Path, err)
	}
	st := &Store{
		db:       db,
		dsnPath:  opts.Path,
		maxBytes: opts.MaxTotalBytes,
		now:      time.Now,
	}
	if err := st.migrate(ctx); err != nil {
		_ = db.Close()
		return nil, err
	}
	return st, nil
}

func (s *Store) migrate(ctx context.Context) error {
	const schema = `
CREATE TABLE IF NOT EXISTS messages (
    id          TEXT PRIMARY KEY,
    sender      TEXT NOT NULL,
    body        BLOB NOT NULL,
    body_bytes  INTEGER NOT NULL,
    received_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS recipient_copies (
    message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    mailbox    TEXT NOT NULL,
    PRIMARY KEY (message_id, mailbox)
);
CREATE INDEX IF NOT EXISTS idx_copies_mailbox ON recipient_copies(mailbox);
`
	_, err := s.db.ExecContext(ctx, schema)
	if err != nil {
		return fmt.Errorf("storage: schema: %w", err)
	}
	return nil
}

// DB exposes the handle for read-only diagnostic/inspection helpers.
func (s *Store) DB() *sql.DB { return s.db }

// Close releases the database. Deliver after Close fails with a real driver
// error, which the state machine classifies as a temporary 451.
func (s *Store) Close() error { return s.db.Close() }

// Deliver implements protocol.Sink. The message row and every recipient copy
// go into one immediate transaction; on any error the transaction is rolled
// back so no partial copy is ever observable.
func (s *Store) Deliver(ctx context.Context, msg protocol.Message) (protocol.Receipt, error) {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return protocol.Receipt{}, protocol.ErrTemporary.Wrap(err)
	}
	committed := false
	defer func() {
		if !committed {
			_ = tx.Rollback()
		}
	}()

	if s.maxBytes > 0 {
		over, err := s.quotaExceeded(ctx, tx, int64(len(msg.Data)))
		if err != nil {
			return protocol.Receipt{}, protocol.ErrTemporary.Wrap(err)
		}
		if over {
			return protocol.Receipt{}, protocol.ErrInsufficient.Wrap(errors.New("local mailbox quota exceeded"))
		}
	}

	if _, err := tx.ExecContext(ctx,
		`INSERT INTO messages(id, sender, body, body_bytes, received_at) VALUES(?, ?, ?, ?, ?)`,
		msg.ID, msg.From, msg.Data, len(msg.Data), msg.ReceivedAt.UnixNano()); err != nil {
		return protocol.Receipt{}, protocol.ErrTemporary.Wrap(err)
	}

	stmt, err := tx.PrepareContext(ctx,
		`INSERT INTO recipient_copies(message_id, mailbox) VALUES(?, ?)`)
	if err != nil {
		return protocol.Receipt{}, protocol.ErrTemporary.Wrap(err)
	}
	defer stmt.Close()
	for _, rcpt := range msg.Recipients {
		if _, err := stmt.ExecContext(ctx, msg.ID, rcpt); err != nil {
			return protocol.Receipt{}, protocol.ErrTemporary.Wrap(err)
		}
	}

	if err := tx.Commit(); err != nil {
		// Commit (fsync) failed: nothing is durable, so the client must retry.
		return protocol.Receipt{}, protocol.ErrTemporary.Wrap(err)
	}
	committed = true
	return protocol.Receipt{MessageID: msg.ID, DeliveredTo: msg.Recipients}, nil
}

func (s *Store) quotaExceeded(ctx context.Context, tx *sql.Tx, incoming int64) (bool, error) {
	var used sql.NullInt64
	if err := tx.QueryRowContext(ctx,
		`SELECT COALESCE(SUM(body_bytes), 0) FROM messages`).Scan(&used); err != nil {
		return false, err
	}
	return used.Int64+incoming > s.maxBytes, nil
}
