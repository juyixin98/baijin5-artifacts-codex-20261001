// Package store is the persistence and mailbox-state core. It owns the
// sequence-number / UID / UIDVALIDITY rules and talks only in domain types:
// it returns data and typed errors, never protocol bytes. SQLite is the sole
// system of record; the network layer is disposable.
package store

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"
	"time"

	_ "modernc.org/sqlite"
)

// ErrNotFound marks a missing mailbox (state/resource lookup failures).
var ErrNotFound = errors.New("store: mailbox not found")

// Store wraps the SQLite database.
type Store struct {
	db *sql.DB
}

// SeedMessage is one mailbox seed entry: raw RFC 5322 bytes plus metadata.
// UIDs are assigned in slice order starting at 1, so seed order *is* the
// declared identity contract for fixtures.
type SeedMessage struct {
	Raw          []byte
	Flags        []string
	InternalDate time.Time
}

// Message is a stored message as seen through the current mailbox epoch.
type Message struct {
	UID          uint32
	Flags        []string
	InternalDate time.Time
	Raw          []byte
}

// MailboxInfo is the SELECT response data.
type MailboxInfo struct {
	Name        string
	Exists      int
	UIDNext     uint32
	UIDValidity uint32
	FirstUnseen uint32 // 0 when every message is \Seen
}

// Removed records one EXPUNGEd message. Seq is the sequence number at the
// moment of removal (numbers shift down after each removal, RFC 9051).
type Removed struct {
	Seq int
	UID uint32
}

// Open opens (creating the schema in) the SQLite database at dsn.
func Open(ctx context.Context, dsn string) (*Store, error) {
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, fmt.Errorf("open sqlite: %w", err)
	}
	// Single-process server; a small pool with WAL keeps writers from
	// seeing database-is-locked under concurrent observers.
	db.SetMaxOpenConns(4)
	s := &Store{db: db}
	if err := s.init(ctx); err != nil {
		db.Close()
		return nil, err
	}
	return s, nil
}

// Close releases the database handle.
func (s *Store) Close() error { return s.db.Close() }

func (s *Store) init(ctx context.Context) error {
	pragmas := []string{
		"PRAGMA journal_mode=WAL",
		"PRAGMA busy_timeout=5000",
		"PRAGMA foreign_keys=ON",
	}
	for _, p := range pragmas {
		if _, err := s.db.ExecContext(ctx, p); err != nil {
			return fmt.Errorf("init %q: %w", p, err)
		}
	}
	const schema = `
CREATE TABLE IF NOT EXISTS mailboxes (
    id           INTEGER PRIMARY KEY,
    name         TEXT    NOT NULL UNIQUE,
    uidvalidity  INTEGER NOT NULL,
    uidnext      INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id           INTEGER PRIMARY KEY,
    mailbox_id   INTEGER NOT NULL REFERENCES mailboxes(id) ON DELETE CASCADE,
    uid          INTEGER NOT NULL,
    flags        TEXT    NOT NULL DEFAULT '',
    internaldate INTEGER NOT NULL,
    raw          BLOB    NOT NULL,
    UNIQUE(mailbox_id, uid)
);
CREATE INDEX IF NOT EXISTS messages_mb_uid ON messages(mailbox_id, uid);
`
	if _, err := s.db.ExecContext(ctx, schema); err != nil {
		return fmt.Errorf("create schema: %w", err)
	}
	return nil
}

// SeedMailbox creates or fully replaces mailbox name with a fresh epoch whose
// UIDVALIDITY is fixed and declared in the seed manifest. UIDs run 1..n in
// manifest order, which is what tests anchor identities to.
func (s *Store) SeedMailbox(ctx context.Context, name string, uidValidity uint32, msgs []SeedMessage) error {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("seed begin: %w", err)
	}
	defer tx.Rollback()

	if _, err := tx.ExecContext(ctx, `DELETE FROM mailboxes WHERE name = ?`, name); err != nil {
		return fmt.Errorf("seed reset: %w", err)
	}
	res, err := tx.ExecContext(ctx,
		`INSERT INTO mailboxes(name, uidvalidity, uidnext) VALUES(?, ?, ?)`,
		name, int64(uidValidity), int64(len(msgs)+1))
	if err != nil {
		return fmt.Errorf("seed mailbox: %w", err)
	}
	mbID, err := res.LastInsertId()
	if err != nil {
		return err
	}
	for i, m := range msgs {
		uid := uint32(i + 1)
		if _, err := tx.ExecContext(ctx,
			`INSERT INTO messages(mailbox_id, uid, flags, internaldate, raw)
			 VALUES(?, ?, ?, ?, ?)`,
			mbID, int64(uid), encodeFlags(m.Flags), m.InternalDate.Unix(), m.Raw); err != nil {
			return fmt.Errorf("seed message uid=%d: %w", uid, err)
		}
	}
	return tx.Commit()
}

// InsertMessage appends one message to an existing mailbox, assigning it the
// current UIDNEXT. It is used by the control/test paths to add data (e.g. a
// deliberately malformed message) without replacing the whole mailbox.
func (s *Store) InsertMessage(ctx context.Context, name string, m SeedMessage) (uint32, error) {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return 0, err
	}
	defer tx.Rollback()
	var mbID, uidnext int64
	err = tx.QueryRowContext(ctx,
		`SELECT id, uidnext FROM mailboxes WHERE name=?`, name).Scan(&mbID, &uidnext)
	if errors.Is(err, sql.ErrNoRows) {
		return 0, ErrNotFound
	}
	if err != nil {
		return 0, err
	}
	if _, err := tx.ExecContext(ctx,
		`INSERT INTO messages(mailbox_id, uid, flags, internaldate, raw)
		 VALUES(?, ?, ?, ?, ?)`,
		mbID, uidnext, encodeFlags(m.Flags), m.InternalDate.Unix(), m.Raw); err != nil {
		return 0, err
	}
	if _, err := tx.ExecContext(ctx,
		`UPDATE mailboxes SET uidnext=? WHERE id=?`, uidnext+1, mbID); err != nil {
		return 0, err
	}
	if err := tx.Commit(); err != nil {
		return 0, err
	}
	return uint32(uidnext), nil
}

// MailboxInfo returns the SELECT-time counters for a mailbox.
func (s *Store) MailboxInfo(ctx context.Context, name string) (MailboxInfo, error) {
	var info MailboxInfo
	var uidv, uidnext int64
	err := s.db.QueryRowContext(ctx,
		`SELECT uidvalidity, uidnext FROM mailboxes WHERE name = ?`, name).
		Scan(&uidv, &uidnext)
	if errors.Is(err, sql.ErrNoRows) {
		return MailboxInfo{}, ErrNotFound
	}
	if err != nil {
		return MailboxInfo{}, fmt.Errorf("lookup mailbox: %w", err)
	}
	exists, first, err := s.counts(ctx, nil, name)
	if err != nil {
		return MailboxInfo{}, err
	}
	info.Name, info.UIDValidity, info.UIDNext, info.Exists, info.FirstUnseen =
		name, uint32(uidv), uint32(uidnext), exists, first
	return info, nil
}

// counts returns (existing messages, UID of first non-\\Seen or 0).
func (s *Store) counts(ctx context.Context, tx *sql.Tx, name string) (int, uint32, error) {
	q := `
SELECT m.uid, m.flags FROM messages m
JOIN mailboxes b ON b.id = m.mailbox_id
WHERE b.name = ? ORDER BY m.uid`
	rows, err := queryRows(ctx, s.db, tx, q, name)
	if err != nil {
		return 0, 0, fmt.Errorf("count messages: %w", err)
	}
	defer rows.Close()
	n := 0
	var firstUnseen uint32
	for rows.Next() {
		var uid int64
		var flags string
		if err := rows.Scan(&uid, &flags); err != nil {
			return 0, 0, err
		}
		n++
		if firstUnseen == 0 && !hasFlag(flags, `\Seen`) {
			firstUnseen = uint32(uid)
		}
	}
	return n, firstUnseen, rows.Err()
}

// FetchBySeq returns messages at the given 1-based sequence positions, in the
// order requested positions resolve (ascending). Missing positions are simply
// absent; callers decide whether that is an error.
func (s *Store) FetchBySeq(ctx context.Context, name string, seqs []int) ([]Message, error) {
	uids, err := s.seqToUIDs(ctx, name, seqs)
	if err != nil {
		return nil, err
	}
	return s.fetchByUIDs(ctx, name, uids)
}

// FetchByUID returns messages matching uids, ascending, skipping stale UIDs.
func (s *Store) FetchByUID(ctx context.Context, name string, uids []uint32) ([]Message, error) {
	return s.fetchByUIDs(ctx, name, uids)
}

// seqToUIDs maps current sequence positions to UIDs. Sequence numbers are
// *derived*: the position among all existing messages ordered by UID, so they
// shift the moment a lower-UID message is expunged, while UIDs never move.
func (s *Store) seqToUIDs(ctx context.Context, name string, seqs []int) ([]uint32, error) {
	rows, err := s.db.QueryContext(ctx,
		`SELECT m.uid FROM messages m JOIN mailboxes b ON b.id=m.mailbox_id
		 WHERE b.name=? ORDER BY m.uid`, name)
	if err != nil {
		return nil, fmt.Errorf("map sequences: %w", err)
	}
	defer rows.Close()
	want := make(map[int]struct{}, len(seqs))
	for _, n := range seqs {
		want[n] = struct{}{}
	}
	var out []uint32
	pos := 0
	for rows.Next() {
		var uid int64
		if err := rows.Scan(&uid); err != nil {
			return nil, err
		}
		pos++
		if _, ok := want[pos]; ok {
			out = append(out, uint32(uid))
		}
	}
	return out, rows.Err()
}

func (s *Store) fetchByUIDs(ctx context.Context, name string, uids []uint32) ([]Message, error) {
	if len(uids) == 0 {
		return nil, nil
	}
	placeholders := strings.Repeat("?,", len(uids))
	args := make([]any, 0, len(uids)+1)
	args = append(args, name)
	for _, u := range uids {
		args = append(args, int64(u))
	}
	q := `SELECT m.uid, m.flags, m.internaldate, m.raw
	      FROM messages m JOIN mailboxes b ON b.id=m.mailbox_id
	      WHERE b.name=? AND m.uid IN (` + placeholders[:len(placeholders)-1] + `)
	      ORDER BY m.uid`
	rows, err := s.db.QueryContext(ctx, q, args...)
	if err != nil {
		return nil, fmt.Errorf("fetch messages: %w", err)
	}
	defer rows.Close()
	var out []Message
	for rows.Next() {
		var m Message
		var uid, epoch int64
		var flags string
		if err := rows.Scan(&uid, &flags, &epoch, &m.Raw); err != nil {
			return nil, err
		}
		m.UID = uint32(uid)
		m.Flags = decodeFlags(flags)
		m.InternalDate = time.Unix(epoch, 0).UTC()
		out = append(out, m)
	}
	return out, rows.Err()
}

// FlagMode selects STORE flag replacement semantics.
type FlagMode int

const (
	FlagReplace FlagMode = iota
	FlagAdd
	FlagRemove
)

// FlagChange is one message whose flags changed; Seq is its current position.
type FlagChange struct {
	Seq   int
	UID   uint32
	Flags []string
}

// SetFlags applies a flag change to messages selected by sequence position or
// by UID. It returns the post-change state of every matched message.
func (s *Store) SetFlags(ctx context.Context, name string, byUID bool, ids []uint32,
	mode FlagMode, flags []string) ([]FlagChange, error) {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("store begin: %w", err)
	}
	defer tx.Rollback()

	uids := ids
	if !byUID {
		seqs := make([]int, len(ids))
		for i, v := range ids {
			seqs[i] = int(v)
		}
		mapped, err := s.seqToUIDsTx(ctx, tx, name, seqs)
		if err != nil {
			return nil, err
		}
		uids = mapped
	}

	var changes []FlagChange
	current, err := s.flagMapTx(ctx, tx, name)
	if err != nil {
		return nil, err
	}
	for _, uid := range uids {
		old := current[uid]
		next := mergeFlags(old, flags, mode)
		if _, err := tx.ExecContext(ctx,
			`UPDATE messages SET flags=? WHERE uid=? AND mailbox_id=
			    (SELECT id FROM mailboxes WHERE name=?)`,
			encodeFlags(next), int64(uid), name); err != nil {
			return nil, fmt.Errorf("update flags: %w", err)
		}
		current[uid] = next
	}
	if err := tx.Commit(); err != nil {
		return nil, err
	}

	// Sequence numbers for the responses must reflect post-commit state.
	seqs, err := s.seqMap(ctx, name)
	if err != nil {
		return nil, err
	}
	for _, uid := range uids {
		if pos, ok := seqs[uid]; ok {
			changes = append(changes, FlagChange{Seq: pos, UID: uid, Flags: current[uid]})
		}
	}
	return changes, nil
}

// Expunge permanently removes every \Deleted message. Removals are reported
// in ascending sequence order with the renumbering rule applied after *each*
// removal, exactly as RFC 9051 prescribes.
func (s *Store) Expunge(ctx context.Context, name string) ([]Removed, int, error) {
	return s.expunge(ctx, name, nil)
}

// ExpungeUID removes \Deleted messages restricted to onlyUIDs (UID EXPUNGE,
// RFC 9051 §6.4.9), with the same per-step renumbering reporting.
func (s *Store) ExpungeUID(ctx context.Context, name string, onlyUIDs []uint32) ([]Removed, int, error) {
	return s.expunge(ctx, name, onlyUIDs)
}

func (s *Store) expunge(ctx context.Context, name string, onlyUIDs []uint32) (removed []Removed, exists int, err error) {
	mbID, err := s.mailboxID(ctx, name)
	if err != nil {
		return nil, 0, err
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return nil, 0, fmt.Errorf("expunge begin: %w", err)
	}
	defer tx.Rollback()

	// Live UIDs ordered ascending. We delete flagged UIDs in ascending
	// order against this shrinking set, which reproduces the RFC 9051 rule
	// "report the current sequence number, then renumber after each removal"
	// without re-querying the database per step.
	live, err := s.liveUIDsTx(ctx, tx, mbID)
	if err != nil {
		return nil, 0, err
	}
	allow := make(map[uint32]bool, len(onlyUIDs))
	for _, u := range onlyUIDs {
		allow[u] = true
	}
	var flagged []uint32
	{
		rows, err := tx.QueryContext(ctx,
			`SELECT uid, flags FROM messages WHERE mailbox_id=? ORDER BY uid`, mbID)
		if err != nil {
			return nil, 0, err
		}
		for rows.Next() {
			var uid int64
			var fl string
			if err := rows.Scan(&uid, &fl); err != nil {
				rows.Close()
				return nil, 0, err
			}
			if hasFlag(fl, `\Deleted`) && (onlyUIDs == nil || allow[uint32(uid)]) {
				flagged = append(flagged, uint32(uid))
			}
		}
		rows.Close()
	}

	// Position each flagged UID inside the progressively shrinking live set.
	livePos := make(map[uint32]int, len(live)) // uid -> current seq
	for i, uid := range live {
		livePos[uid] = i + 1
	}
	for _, uid := range flagged {
		removed = append(removed, Removed{Seq: livePos[uid], UID: uid})
		// Shift every still-live UID above this one down by one.
		for u, pos := range livePos {
			if pos > livePos[uid] {
				livePos[u] = pos - 1
			}
		}
		delete(livePos, uid)
		if _, err := tx.ExecContext(ctx,
			`DELETE FROM messages WHERE mailbox_id=? AND uid=?`, mbID, int64(uid)); err != nil {
			return nil, 0, fmt.Errorf("delete uid %d: %w", uid, err)
		}
	}
	if err := tx.Commit(); err != nil {
		return nil, 0, err
	}
	exists = len(livePos)
	return removed, exists, nil
}

// RotateUIDValidity starts a fresh epoch: new UIDVALIDITY, all messages
// cleared, UIDNEXT back to 1. Any UID a client cached under the old
// UIDVALIDITY is now stale and must not be honoured.
func (s *Store) RotateUIDValidity(ctx context.Context, name string) (uint32, error) {
	current, err := s.MailboxInfo(ctx, name)
	if err != nil {
		return 0, err
	}
	if current.UIDValidity == ^uint32(0) {
		// Bumping would wrap to a *lower* value, which would corrupt the
		// monotonicity contract: a computation failure, not a client error.
		return 0, fmt.Errorf("UIDVALIDITY exhausted at %d", ^uint32(0))
	}
	next := current.UIDValidity + 1
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return 0, err
	}
	defer tx.Rollback()
	if _, err := tx.ExecContext(ctx,
		`UPDATE mailboxes SET uidvalidity=?, uidnext=1 WHERE name=?`,
		int64(next), name); err != nil {
		return 0, err
	}
	if _, err := tx.ExecContext(ctx,
		`DELETE FROM messages WHERE mailbox_id=(SELECT id FROM mailboxes WHERE name=?)`,
		name); err != nil {
		return 0, err
	}
	if err := tx.Commit(); err != nil {
		return 0, err
	}
	return next, nil
}

// SetUIDValidityForTest pins UIDVALIDITY (used to exercise overflow logic).
func (s *Store) SetUIDValidityForTest(ctx context.Context, name string, v uint32) error {
	res, err := s.db.ExecContext(ctx,
		`UPDATE mailboxes SET uidvalidity=? WHERE name=?`, int64(v), name)
	if err != nil {
		return err
	}
	if n, _ := res.RowsAffected(); n == 0 {
		return ErrNotFound
	}
	return nil
}

func (s *Store) mailboxID(ctx context.Context, name string) (int64, error) {
	var id int64
	err := s.db.QueryRowContext(ctx, `SELECT id FROM mailboxes WHERE name=?`, name).Scan(&id)
	if errors.Is(err, sql.ErrNoRows) {
		return 0, ErrNotFound
	}
	return id, err
}

func (s *Store) liveUIDsTx(ctx context.Context, tx *sql.Tx, mbID int64) ([]uint32, error) {
	rows, err := tx.QueryContext(ctx,
		`SELECT uid FROM messages WHERE mailbox_id=? ORDER BY uid`, mbID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	return scanUIDs(rows)
}

func scanUIDs(rows *sql.Rows) ([]uint32, error) {
	var out []uint32
	for rows.Next() {
		var uid int64
		if err := rows.Scan(&uid); err != nil {
			return nil, err
		}
		out = append(out, uint32(uid))
	}
	return out, rows.Err()
}

func (s *Store) seqMap(ctx context.Context, name string) (map[uint32]int, error) {
	rows, err := s.db.QueryContext(ctx,
		`SELECT m.uid FROM messages m JOIN mailboxes b ON b.id=m.mailbox_id
		 WHERE b.name=? ORDER BY m.uid`, name)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := map[uint32]int{}
	pos := 0
	for rows.Next() {
		var uid int64
		if err := rows.Scan(&uid); err != nil {
			return nil, err
		}
		pos++
		out[uint32(uid)] = pos
	}
	return out, rows.Err()
}

func (s *Store) seqToUIDsTx(ctx context.Context, tx *sql.Tx, name string, seqs []int) ([]uint32, error) {
	rows, err := tx.QueryContext(ctx,
		`SELECT m.uid FROM messages m JOIN mailboxes b ON b.id=m.mailbox_id
		 WHERE b.name=? ORDER BY m.uid`, name)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	want := make(map[int]struct{}, len(seqs))
	for _, n := range seqs {
		want[n] = struct{}{}
	}
	var out []uint32
	pos := 0
	for rows.Next() {
		var uid int64
		if err := rows.Scan(&uid); err != nil {
			return nil, err
		}
		pos++
		if _, ok := want[pos]; ok {
			out = append(out, uint32(uid))
		}
	}
	return out, rows.Err()
}

func (s *Store) flagMapTx(ctx context.Context, tx *sql.Tx, name string) (map[uint32][]string, error) {
	rows, err := tx.QueryContext(ctx,
		`SELECT m.uid, m.flags FROM messages m JOIN mailboxes b ON b.id=m.mailbox_id
		 WHERE b.name=? ORDER BY m.uid`, name)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := map[uint32][]string{}
	for rows.Next() {
		var uid int64
		var fl string
		if err := rows.Scan(&uid, &fl); err != nil {
			return nil, err
		}
		out[uint32(uid)] = decodeFlags(fl)
	}
	return out, rows.Err()
}

// SequenceOfUID returns the current 1-based sequence position of uid, or 0
// if the UID does not exist in the mailbox.
func (s *Store) SequenceOfUID(ctx context.Context, name string, uid uint32) (int, error) {
	rows, err := s.db.QueryContext(ctx,
		`SELECT m.uid,
		        (SELECT COUNT(*) FROM messages m2
		           JOIN mailboxes b2 ON b2.id=m2.mailbox_id
		          WHERE b2.name=? AND m2.uid <= m.uid) AS pos
		   FROM messages m JOIN mailboxes b ON b.id=m.mailbox_id
		  WHERE b.name=? AND m.uid=?`, name, name, int64(uid))
	if err != nil {
		return 0, err
	}
	defer rows.Close()
	if !rows.Next() {
		return 0, rows.Err()
	}
	var got, pos int64
	if err := rows.Scan(&got, &pos); err != nil {
		return 0, err
	}
	return int(pos), nil
}

// TimedUIDValidity derives a fresh UIDVALIDITY from the wall clock.
func TimedUIDValidity(now time.Time) uint32 { return uint32(now.Unix()) }

// queryRows runs through tx when non-nil, otherwise the pool.
func queryRows(ctx context.Context, db *sql.DB, tx *sql.Tx, q string, name string) (*sql.Rows, error) {
	if tx != nil {
		return tx.QueryContext(ctx, q, name)
	}
	return db.QueryContext(ctx, q, name)
}
