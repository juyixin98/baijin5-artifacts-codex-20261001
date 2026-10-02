package compat_test

import (
	"database/sql"
	"testing"

	_ "github.com/mattn/go-sqlite3" // independent audit-reader handle
)

// TestAuditSQLitePersistedIndependently opens the fixture's database file
// with a SEPARATE database/sql handle (the fixture is not involved in the
// read path at all) and asserts raw rows, proving the audit trail is really
// persisted and correlated, not merely echoed by the in-process HTTP API.
func TestAuditSQLitePersistedIndependently(t *testing.T) {
	h := startHarness(t, configLatencyNone, 0)
	defer h.shutdown()

	// Generate one normal and one exceptional exchange.
	okConn := h.dial(t)
	writeFull(t, okConn, readReq(0x3344, 0x01, 0, 1), 0)
	readExactFrame(t, okConn)
	_ = okConn.Close()

	badConn := h.dial(t)
	writeFull(t, badConn, readReq(0x4455, 0x01, 999, 1), 0)
	readExactFrame(t, badConn)
	_ = badConn.Close()

	db, err := sql.Open("sqlite3", h.dbPath+"?_busy_timeout=5000&mode=ro")
	if err != nil {
		t.Fatalf("open audit db independently: %v", err)
	}
	defer db.Close()

	rows, err := db.Query(`
SELECT txn_id, unit_id, function, exception, category, raw_pdu_hex
FROM requests WHERE txn_id IN (13124, 17493) ORDER BY txn_id`)
	if err != nil {
		t.Fatalf("query: %v", err)
	}
	defer rows.Close()

	type row struct {
		txn, unit, fc, exception int
		category, pdu            string
	}
	var got []row
	for rows.Next() {
		var r row
		if err := rows.Scan(&r.txn, &r.unit, &r.fc, &r.exception,
			&r.category, &r.pdu); err != nil {
			t.Fatalf("scan: %v", err)
		}
		got = append(got, r)
	}
	if len(got) != 2 {
		t.Fatalf("independent read found %d rows, want 2", len(got))
	}

	// txn 0x3344 = 13124: FC03 normal.
	if got[0].txn != 0x3344 || got[0].fc != 0x03 || got[0].exception != 0 ||
		got[0].category != "" {
		t.Fatalf("normal row wrong: %+v", got[0])
	}
	if got[0].pdu != "0300000001" {
		t.Fatalf("normal raw pdu = %q, want 0300000001", got[0].pdu)
	}
	// txn 0x4455 = 17493: FC03 illegal address exception category.
	if got[1].txn != 0x4455 || got[1].exception != 0x02 ||
		got[1].category != "illegal_data_address" {
		t.Fatalf("exception row wrong: %+v", got[1])
	}

	// Audit schema sanity: the requests table must carry an identity
	// correlation index.
	var idxCount int
	if err := db.QueryRow(
		`SELECT count(*) FROM sqlite_master WHERE type='index' AND name='idx_requests_txn'`,
	).Scan(&idxCount); err != nil {
		t.Fatalf("index check: %v", err)
	}
	if idxCount != 1 {
		t.Fatalf("correlation index missing")
	}
}
