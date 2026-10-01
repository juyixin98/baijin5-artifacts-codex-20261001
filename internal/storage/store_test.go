package storage_test

import (
	"context"
	"database/sql"
	"errors"
	"path/filepath"
	"strings"
	"testing"
	"time"

	_ "modernc.org/sqlite"

	"smtpsink/internal/protocol"
	"smtpsink/internal/storage"
)

// openIndependent returns a *separate* sql.DB on the same file. The test uses
// it to assert committed state without trusting any Store read method, so the
// "reference answer" is not produced by the system under test.
func openIndependent(t *testing.T, path string) *sql.DB {
	t.Helper()
	db, err := sql.Open("sqlite", "file:"+filepath.ToSlash(path)+"?_pragma=busy_timeout(5000)&mode=ro")
	if err != nil {
		t.Fatalf("independent open: %v", err)
	}
	t.Cleanup(func() { db.Close() })
	if err := db.Ping(); err != nil {
		t.Fatalf("independent ping: %v", err)
	}
	return db
}

func sampleMsg(id string, rcpts ...string) protocol.Message {
	return protocol.Message{
		ID:         id,
		From:       "alice@localhost",
		Recipients: rcpts,
		Data:       []byte("Subject: t\r\n\r\nbody dot-test .x ..y\r\n"),
		ReceivedAt: time.Unix(1700000000, 0),
	}
}

func TestDeliver_PersistsMessageAndOneCopyPerRecipient(t *testing.T) {
	path := filepath.Join(t.TempDir(), "sink.db")
	ctx := context.Background()
	st, err := storage.Open(ctx, storage.Options{Path: path})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { st.Close() })

	msg := sampleMsg("m-1", "bob@sink.local", "carol@localhost")
	rec, err := st.Deliver(ctx, msg)
	if err != nil {
		t.Fatalf("deliver: %v", err)
	}
	if rec.MessageID != "m-1" || len(rec.DeliveredTo) != 2 {
		t.Fatalf("receipt = %+v", rec)
	}

	db := openIndependent(t, path)
	var sender string
	var body []byte
	var n int
	err = db.QueryRow(`SELECT sender, body, body_bytes FROM messages WHERE id = ?`, "m-1").
		Scan(&sender, &body, &n)
	if err != nil {
		t.Fatalf("committed message not independently visible: %v", err)
	}
	if sender != "alice@localhost" {
		t.Fatalf("sender = %q", sender)
	}
	if string(body) != string(msg.Data) || n != len(msg.Data) {
		t.Fatalf("body/bytes mismatch: %q (%d) vs %q (%d)", body, n, msg.Data, len(msg.Data))
	}

	rows, err := db.Query(`SELECT mailbox FROM recipient_copies WHERE message_id = ? ORDER BY mailbox`, "m-1")
	if err != nil {
		t.Fatal(err)
	}
	var got []string
	for rows.Next() {
		var m string
		if err := rows.Scan(&m); err != nil {
			t.Fatal(err)
		}
		got = append(got, m)
	}
	rows.Close()
	if strings.Join(got, ",") != "bob@sink.local,carol@localhost" {
		t.Fatalf("recipient copies = %v", got)
	}
}

func TestDeliver_AtomicOnSecondRecipientFailure(t *testing.T) {
	// A duplicate (message_id, mailbox) primary-key violation on the second
	// recipient forces the whole transaction to roll back: no message row and
	// no first copy may remain.
	path := filepath.Join(t.TempDir(), "sink.db")
	ctx := context.Background()
	st, err := storage.Open(ctx, storage.Options{Path: path})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { st.Close() })

	first := sampleMsg("m-2", "bob@sink.local")
	if _, err := st.Deliver(ctx, first); err != nil {
		t.Fatal(err)
	}

	// Same message id reused with an extra recipient: second copy insert
	// collides for bob and rolls back the entire attempt.
	bad := sampleMsg("m-2", "bob@sink.local", "carol@localhost")
	_, err = st.Deliver(ctx, bad)
	if err == nil {
		t.Fatal("expected deliver failure on PK collision")
	}
	var de *protocol.DeliveryError
	if !errors.As(err, &de) || de.Kind != protocol.KindTemporary {
		t.Fatalf("error category = %v, want temporary", err)
	}

	db := openIndependent(t, path)
	var copies int
	if err := db.QueryRow(
		`SELECT COUNT(*) FROM recipient_copies WHERE message_id = 'm-2'`).Scan(&copies); err != nil {
		t.Fatal(err)
	}
	if copies != 1 {
		t.Fatalf("copies after rollback = %d, want 1 (no partial second transaction)", copies)
	}
}

func TestDeliver_AfterClose_TemporaryFailure(t *testing.T) {
	path := filepath.Join(t.TempDir(), "sink.db")
	ctx := context.Background()
	st, err := storage.Open(ctx, storage.Options{Path: path})
	if err != nil {
		t.Fatal(err)
	}
	if err := st.Close(); err != nil {
		t.Fatal(err)
	}
	_, err = st.Deliver(ctx, sampleMsg("m-3", "x@sink.local"))
	var de *protocol.DeliveryError
	if !errors.As(err, &de) {
		t.Fatalf("error = %v, want *DeliveryError", err)
	}
	if de.Kind != protocol.KindTemporary {
		t.Fatalf("kind = %v, want temporary", de.Kind)
	}
}

func TestQuota_RejectsWithInsufficientStorage(t *testing.T) {
	path := filepath.Join(t.TempDir(), "sink.db")
	ctx := context.Background()
	st, err := storage.Open(ctx, storage.Options{Path: path, MaxTotalBytes: 50})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { st.Close() })

	small := sampleMsg("m-4", "x@sink.local")
	small.Data = []byte("ten bytes!") // len 10
	if _, err := st.Deliver(ctx, small); err != nil {
		t.Fatalf("within quota should succeed: %v", err)
	}

	big := sampleMsg("m-5", "x@sink.local")
	big.Data = make([]byte, 60)
	_, err = st.Deliver(ctx, big)
	var de *protocol.DeliveryError
	if !errors.As(err, &de) {
		t.Fatalf("error = %v", err)
	}
	if de.Kind != protocol.KindInsufficient {
		t.Fatalf("kind = %v, want insufficient", de.Kind)
	}

	db := openIndependent(t, path)
	var n int
	if err := db.QueryRow(`SELECT COUNT(*) FROM messages`).Scan(&n); err != nil {
		t.Fatal(err)
	}
	if n != 1 {
		t.Fatalf("messages on disk = %d, quota-rejected message must not persist", n)
	}
}

func TestOpen_ErrorsAndDBHandle(t *testing.T) {
	ctx := context.Background()
	if _, err := storage.Open(ctx, storage.Options{Path: ""}); err == nil {
		t.Fatal("empty path must fail")
	}
	// A path under a non-existent directory fails to open.
	if _, err := storage.Open(ctx, storage.Options{Path: filepath.Join(t.TempDir(), "no-dir", "x.db")}); err == nil {
		t.Fatal("unopenable path must fail")
	}

	path := filepath.Join(t.TempDir(), "sink.db")
	st, err := storage.Open(ctx, storage.Options{Path: path})
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()
	var n int
	if err := st.DB().QueryRow(`SELECT 1`).Scan(&n); err != nil || n != 1 {
		t.Fatalf("DB() handle not usable: %v", err)
	}
}

func TestReopen_SeesPreviouslyCommittedData(t *testing.T) {
	path := filepath.Join(t.TempDir(), "sink.db")
	ctx := context.Background()
	st, err := storage.Open(ctx, storage.Options{Path: path})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := st.Deliver(ctx, sampleMsg("m-6", "persist@sink.local")); err != nil {
		t.Fatal(err)
	}
	if err := st.Close(); err != nil {
		t.Fatal(err)
	}

	st2, err := storage.Open(ctx, storage.Options{Path: path})
	if err != nil {
		t.Fatal(err)
	}
	defer st2.Close()
	db := openIndependent(t, path)
	var mbox string
	if err := db.QueryRow(`SELECT mailbox FROM recipient_copies WHERE message_id = 'm-6'`).Scan(&mbox); err != nil {
		t.Fatalf("data did not survive reopen: %v", err)
	}
	if mbox != "persist@sink.local" {
		t.Fatalf("mailbox = %q", mbox)
	}
}
