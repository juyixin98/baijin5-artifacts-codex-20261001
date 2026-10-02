package fixture

import (
	"database/sql"
	"fmt"
	"time"

	_ "github.com/mattn/go-sqlite3" // register the sqlite3 driver with database/sql
)

// AuditRecord is one row of the request audit trail. It correlates a wire
// exchange with its identity (connection, transaction id, unit id) and
// records the failure category separately from any exception code.
type AuditRecord struct {
	ID         int64  `json:"id"`
	TS         string `json:"ts"`
	ConnID     string `json:"conn_id"`
	RemoteAddr string `json:"remote_addr"`
	TxnID      uint16 `json:"txn_id"`
	UnitID     byte   `json:"unit_id"`
	Function   byte   `json:"function"`
	Address    *int   `json:"address,omitempty"`
	Quantity   *int   `json:"quantity,omitempty"`
	Exception  byte   `json:"exception"`
	Category   string `json:"category"`
	DurationUS int64  `json:"duration_us"`
	RawPDUHex  string `json:"raw_pdu_hex"`
}

// AuditStore persists every exchange to SQLite. A single underlying
// connection serializes writes (local fixture scale, microsecond inserts);
// WAL keeps control-plane reads from blocking on checkpoints.
type AuditStore struct {
	db *sql.DB
}

// NewAuditStore opens (creating if needed) the SQLite database at path and
// creates the schema. busy_timeout absorbs any rare lock contention.
func NewAuditStore(path string) (*AuditStore, error) {
	dsn := fmt.Sprintf(
		"file:%s?_busy_timeout=5000&_journal_mode=WAL&_synchronous=NORMAL",
		path)
	db, err := sql.Open("sqlite3", dsn)
	if err != nil {
		return nil, fmt.Errorf("open sqlite %q: %w", path, err)
	}
	// One connection: SQLite writes take a database-level lock, and a
	// single conn removes "database is locked" races entirely.
	db.SetMaxOpenConns(1)

	if err := db.Ping(); err != nil {
		_ = db.Close()
		return nil, fmt.Errorf("ping sqlite %q: %w", path, err)
	}
	st := &AuditStore{db: db}
	if err := st.migrate(); err != nil {
		_ = db.Close()
		return nil, err
	}
	return st, nil
}

func (s *AuditStore) migrate() error {
	const schema = `
CREATE TABLE IF NOT EXISTS requests (
	id          INTEGER PRIMARY KEY AUTOINCREMENT,
	ts          TEXT    NOT NULL,
	conn_id     TEXT    NOT NULL,
	remote_addr TEXT    NOT NULL,
	txn_id      INTEGER NOT NULL,
	unit_id     INTEGER NOT NULL,
	function    INTEGER NOT NULL,
	address     INTEGER,
	quantity    INTEGER,
	exception   INTEGER NOT NULL,
	category    TEXT    NOT NULL,
	duration_us INTEGER NOT NULL,
	raw_pdu_hex TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_requests_txn ON requests(conn_id, txn_id);
CREATE INDEX IF NOT EXISTS idx_requests_ts  ON requests(ts);
`
	if _, err := s.db.Exec(schema); err != nil {
		return fmt.Errorf("create audit schema: %w", err)
	}
	return nil
}

// Insert persists one processed exchange.
func (s *AuditStore) Insert(r AuditRecord) error {
	_, err := s.db.Exec(`
INSERT INTO requests
  (ts, conn_id, remote_addr, txn_id, unit_id, function, address, quantity,
   exception, category, duration_us, raw_pdu_hex)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		r.TS, r.ConnID, r.RemoteAddr, int64(r.TxnID), int64(r.UnitID),
		int64(r.Function), addrToSQL(r.Address), qtyToSQL(r.Quantity),
		int64(r.Exception), r.Category, r.DurationUS, r.RawPDUHex)
	if err != nil {
		return fmt.Errorf("insert audit row: %w", err)
	}
	return nil
}

func addrToSQL(v *int) any {
	if v == nil {
		return nil
	}
	return int64(*v)
}
func qtyToSQL(v *int) any { return addrToSQL(v) }

// Recent returns up to limit newest audit rows (newest first by insertion
// order; IDs are monotonic).
func (s *AuditStore) Recent(limit int) ([]AuditRecord, error) {
	if limit <= 0 || limit > 10_000 {
		limit = 100
	}
	rows, err := s.db.Query(`
SELECT id, ts, conn_id, remote_addr, txn_id, unit_id, function,
       address, quantity, exception, category, duration_us, raw_pdu_hex
FROM requests
ORDER BY id DESC
LIMIT ?`, limit)
	if err != nil {
		return nil, fmt.Errorf("query audit: %w", err)
	}
	defer rows.Close()

	var out []AuditRecord
	for rows.Next() {
		var (
			r                      AuditRecord
			addr, qty              sql.NullInt64
			txnID, unitID, fn, exc int64
		)
		if err := rows.Scan(&r.ID, &r.TS, &r.ConnID, &r.RemoteAddr,
			&txnID, &unitID, &fn, &addr, &qty, &exc,
			&r.Category, &r.DurationUS, &r.RawPDUHex); err != nil {
			return nil, fmt.Errorf("scan audit row: %w", err)
		}
		r.TxnID, r.UnitID, r.Function, r.Exception =
			uint16(txnID), byte(unitID), byte(fn), byte(exc)
		if addr.Valid {
			a := int(addr.Int64)
			r.Address = &a
		}
		if qty.Valid {
			q := int(qty.Int64)
			r.Quantity = &q
		}
		out = append(out, r)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate audit rows: %w", err)
	}
	return out, nil
}

// Close releases the database handle.
func (s *AuditStore) Close() error { return s.db.Close() }

// nowTS is a single timestamp formatting point (UTC RFC3339 with nanos).
func nowTS() string { return time.Now().UTC().Format(time.RFC3339Nano) }
