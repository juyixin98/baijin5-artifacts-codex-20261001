package server_test

import (
	"bufio"
	"context"
	"database/sql"
	"net"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	_ "modernc.org/sqlite"

	"smtpsink/internal/config"
	"smtpsink/internal/protocol"
	"smtpsink/internal/server"
	"smtpsink/internal/storage"
)

// smtpClient is a minimal, independently written SMTP conversation client used
// only by tests. It is deliberately not shared with the implementation under
// test: the reference transcript is hard-coded here.
type smtpClient struct {
	t    *testing.T
	conn net.Conn
	br   *bufio.Reader
}

func dial(t *testing.T, addr string) *smtpClient {
	t.Helper()
	conn, err := net.DialTimeout("tcp", addr, 2*time.Second)
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	return &smtpClient{t: t, conn: conn, br: bufio.NewReader(conn)}
}

func (c *smtpClient) expectGreeting() {
	c.t.Helper()
	code, _ := c.readReply()
	if code != 220 {
		c.t.Fatalf("greeting code = %d, want 220", code)
	}
}

func (c *smtpClient) cmd(line string, want int) []string {
	c.t.Helper()
	if _, err := c.conn.Write([]byte(line + "\r\n")); err != nil {
		c.t.Fatalf("write %q: %v", line, err)
	}
	code, text := c.readReply()
	if code != want {
		c.t.Fatalf("command %q: code = %d, want %d (%v)", line, code, want, text)
	}
	return text
}

func (c *smtpClient) raw(data string) {
	c.t.Helper()
	if _, err := c.conn.Write([]byte(data)); err != nil {
		c.t.Fatalf("raw write: %v", err)
	}
}

func (c *smtpClient) readReply() (int, []string) {
	c.t.Helper()
	var lines []string
	var code int
	for {
		line, err := c.br.ReadString('\n')
		if err != nil {
			c.t.Fatalf("read reply: %v", err)
		}
		line = strings.TrimRight(line, "\r\n")
		if len(line) < 4 {
			c.t.Fatalf("malformed reply %q", line)
		}
		n, err := strconv.Atoi(line[:3])
		if err != nil {
			c.t.Fatalf("non-numeric reply code in %q", line)
		}
		code = n
		lines = append(lines, line[4:])
		if line[3] == ' ' {
			return code, lines
		}
	}
}

func (c *smtpClient) close() { _ = c.conn.Close() }

// failSink is an independent scripted sink for server-level failure tests.
type failSink struct{ err error }

func (f failSink) Deliver(context.Context, protocol.Message) (protocol.Receipt, error) {
	return protocol.Receipt{}, f.err
}

func testConfig(t *testing.T) config.Config {
	t.Helper()
	cfg := config.Default()
	cfg.Listen.Host = "127.0.0.1"
	cfg.Listen.Port = 0
	cfg.Storage.Path = filepath.Join(t.TempDir(), "sink.db")
	cfg.Timeouts.IdleMS = 3000
	cfg.Timeouts.SessionMS = 10000
	return cfg
}

func startServer(t *testing.T, cfg config.Config, sink protocol.Sink) *server.Server {
	t.Helper()
	srv := server.New(cfg, sink, testLogger())
	if err := srv.Start(context.Background()); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = srv.Close() })
	return srv
}

func independentDB(t *testing.T, path string) *sql.DB {
	t.Helper()
	db, err := sql.Open("sqlite", "file:"+filepath.ToSlash(path)+"?mode=ro")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { db.Close() })
	return db
}

func TestEndToEnd_FullTransactionWithDotLines(t *testing.T) {
	cfg := testConfig(t)
	st, err := storage.Open(context.Background(), storage.Options{Path: cfg.Storage.Path})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { st.Close() })
	srv := startServer(t, cfg, st)

	c := dial(t, srv.Addr().String())
	defer c.close()
	c.expectGreeting()
	c.cmd("EHLO testbox", 250)
	c.cmd("MAIL FROM:<sender@localhost>", 250)
	c.cmd("RCPT TO:<bob@sink.local>", 250)
	c.cmd("RCPT TO:<bob@sink.local>", 250) // duplicate tolerated
	c.cmd("RCPT TO:<carol@localhost>", 250)
	c.cmd("RCPT TO:<outsider@other.example>", 550)
	c.cmd("DATA", 354)
	c.raw("Subject: compat\r\n")
	c.raw(".leading-dot\r\n") // wire-escaped body dot
	c.raw("..\r\n")
	c.raw("\r\n")
	c.raw(".\r\n")
	c.readReply250(t)
	c.cmd("QUIT", 221)

	db := independentDB(t, cfg.Storage.Path)
	var body string
	var n int
	err = db.QueryRow(`SELECT body, body_bytes FROM messages`).Scan(&body, &n)
	if err != nil {
		t.Fatalf("no message persisted: %v", err)
	}
	want := "Subject: compat\r\nleading-dot\r\n.\r\n\r\n"
	if body != want {
		t.Fatalf("persisted body = %q, want %q", body, want)
	}
	if n != len(want) {
		t.Fatalf("body_bytes = %d, want %d", n, len(want))
	}
	rows, err := db.Query(`SELECT mailbox FROM recipient_copies ORDER BY mailbox`)
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	var got []string
	for rows.Next() {
		var m string
		if err := rows.Scan(&m); err != nil {
			t.Fatal(err)
		}
		got = append(got, m)
	}
	if strings.Join(got, ",") != "bob@sink.local,carol@localhost" {
		t.Fatalf("copies = %v; duplicate must not add a copy, remote must be absent", got)
	}
}

func (c *smtpClient) readReply250(t *testing.T) {
	t.Helper()
	code, text := c.readReply()
	if code != 250 {
		t.Fatalf("end-of-data code = %d %v, want 250", code, text)
	}
}

func TestEndToEnd_RsetDiscardsRecipients(t *testing.T) {
	cfg := testConfig(t)
	st, err := storage.Open(context.Background(), storage.Options{Path: cfg.Storage.Path})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { st.Close() })
	srv := startServer(t, cfg, st)

	c := dial(t, srv.Addr().String())
	defer c.close()
	c.expectGreeting()
	c.cmd("EHLO me", 250)
	c.cmd("MAIL FROM:<a@localhost>", 250)
	c.cmd("RCPT TO:<first@sink.local>", 250)
	c.cmd("RSET", 250)
	c.cmd("RCPT TO:<first@sink.local>", 503) // RSET wiped the transaction
	c.cmd("MAIL FROM:<a@localhost>", 250)
	c.cmd("RCPT TO:<second@sink.local>", 250)
	c.cmd("DATA", 354)
	c.raw("only second\r\n.\r\n")
	c.readReply250(t)

	db := independentDB(t, cfg.Storage.Path)
	var mbox string
	if err := db.QueryRow(`SELECT mailbox FROM recipient_copies`).Scan(&mbox); err != nil {
		t.Fatalf("no copy: %v", err)
	}
	if mbox != "second@sink.local" {
		t.Fatalf("copy went to %q, want second@sink.local only", mbox)
	}
	var copies int
	if err := db.QueryRow(`SELECT COUNT(*) FROM recipient_copies`).Scan(&copies); err != nil {
		t.Fatal(err)
	}
	if copies != 1 {
		t.Fatalf("copies = %d, want exactly 1 after RSET", copies)
	}
}

func TestEndToEnd_ConnectionAbortDeliversNothing(t *testing.T) {
	cfg := testConfig(t)
	st, err := storage.Open(context.Background(), storage.Options{Path: cfg.Storage.Path})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { st.Close() })
	srv := startServer(t, cfg, st)

	c := dial(t, srv.Addr().String())
	c.expectGreeting()
	c.cmd("EHLO me", 250)
	c.cmd("MAIL FROM:<a@localhost>", 250)
	c.cmd("RCPT TO:<b@sink.local>", 250)
	c.cmd("DATA", 354)
	c.raw("partial content without terminator")
	c.close() // hard disconnect mid-DATA

	// Give the server loop a moment to observe EOF and return.
	deadline := time.Now().Add(2 * time.Second)
	db := independentDB(t, cfg.Storage.Path)
	for time.Now().Before(deadline) {
		var n int
		if err := db.QueryRow(`SELECT COUNT(*) FROM messages`).Scan(&n); err == nil && n == 0 {
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatal("a message was persisted despite the connection aborting before the dot")
}

func TestEndToEnd_DurableFailureReturns451(t *testing.T) {
	cfg := testConfig(t)
	srv := startServer(t, cfg, failSink{err: protocol.ErrTemporary})

	c := dial(t, srv.Addr().String())
	defer c.close()
	c.expectGreeting()
	c.cmd("EHLO me", 250)
	c.cmd("MAIL FROM:<a@localhost>", 250)
	c.cmd("RCPT TO:<b@sink.local>", 250)
	c.cmd("DATA", 354)
	c.raw("x\r\n.\r\n")
	code, text := c.readReply()
	if code != 451 {
		t.Fatalf("durability failure code = %d %v, want 451", code, text)
	}
}

func TestEndToEnd_OverlongCommandLine(t *testing.T) {
	cfg := testConfig(t)
	cfg.Limits.CommandLineBytes = 32
	st, err := storage.Open(context.Background(), storage.Options{Path: cfg.Storage.Path})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { st.Close() })
	srv := startServer(t, cfg, st)

	c := dial(t, srv.Addr().String())
	defer c.close()
	c.expectGreeting()
	c.cmd("EHLO me", 250)
	// 33-byte MAIL path exceeds 32: the server resyncs and answers 500.
	long := "MAIL FROM:<" + strings.Repeat("a", 40) + "@localhost>"
	c.raw(long + "\r\n")
	code, _ := c.readReply()
	if code != 500 {
		t.Fatalf("over-long command code = %d, want 500", code)
	}
	// Connection stays usable after resynchronization.
	c.cmd("NOOP", 250)
}

func TestEndToEnd_MultipleMessagesOnOneConnection(t *testing.T) {
	cfg := testConfig(t)
	st, err := storage.Open(context.Background(), storage.Options{Path: cfg.Storage.Path})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { st.Close() })
	srv := startServer(t, cfg, st)

	c := dial(t, srv.Addr().String())
	defer c.close()
	c.expectGreeting()
	c.cmd("EHLO me", 250)
	for i := 0; i < 3; i++ {
		c.cmd("MAIL FROM:<a@localhost>", 250)
		c.cmd("RCPT TO:<m@sink.local>", 250)
		c.cmd("DATA", 354)
		c.raw("batch\r\n.\r\n")
		c.readReply250(t)
	}
	db := independentDB(t, cfg.Storage.Path)
	var n int
	if err := db.QueryRow(`SELECT COUNT(*) FROM messages`).Scan(&n); err != nil {
		t.Fatal(err)
	}
	if n != 3 {
		t.Fatalf("messages persisted = %d, want 3", n)
	}
}

func TestEndToEnd_IdleTimeoutReturns421(t *testing.T) {
	cfg := testConfig(t)
	cfg.Timeouts.IdleMS = 200
	st, err := storage.Open(context.Background(), storage.Options{Path: cfg.Storage.Path})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { st.Close() })
	srv := startServer(t, cfg, st)

	c := dial(t, srv.Addr().String())
	defer c.close()
	c.expectGreeting()
	// Send nothing; server should close with 421 after the idle deadline.
	code, _ := c.readReply()
	if code != 421 {
		t.Fatalf("idle timeout code = %d, want 421", code)
	}
}

func TestStart_BindFailure(t *testing.T) {
	cfg := testConfig(t)
	cfg.Listen.Port = -7
	srv := server.New(cfg, failSink{}, testLogger())
	if err := srv.Start(context.Background()); err == nil {
		_ = srv.Close()
		t.Fatal("expected listen error for invalid port")
	}
}

func TestClose_Idempotent(t *testing.T) {
	cfg := testConfig(t)
	srv := startServer(t, cfg, failSink{})
	if err := srv.Close(); err != nil {
		t.Fatal(err)
	}
	if err := srv.Close(); err != nil {
		t.Fatalf("second Close must be a no-op, got %v", err)
	}
}

func TestEndToEnd_HeloFallbackWorks(t *testing.T) {
	cfg := testConfig(t)
	st, err := storage.Open(context.Background(), storage.Options{Path: cfg.Storage.Path})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { st.Close() })
	srv := startServer(t, cfg, st)

	c := dial(t, srv.Addr().String())
	defer c.close()
	c.expectGreeting()
	c.cmd("HELO oldhost", 250) // single-line greeting, no ESMTP extensions
	c.cmd("MAIL FROM:<a@localhost>", 250)
	c.cmd("RCPT TO:<b@sink.local>", 250)
	c.cmd("DATA", 354)
	c.raw("helo-path\r\n.\r\n")
	c.readReply250(t)
}

func TestNew_NilLoggerDoesNotPanic(t *testing.T) {
	cfg := testConfig(t)
	srv := server.New(cfg, failSink{}, nil)
	if err := srv.Start(context.Background()); err != nil {
		t.Fatal(err)
	}
	defer srv.Close()
}

func TestEndToEnd_FatalOverflowClosesWith421(t *testing.T) {
	cfg := testConfig(t)
	cfg.Limits.CommandLineBytes = 16
	st, err := storage.Open(context.Background(), storage.Options{Path: cfg.Storage.Path})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { st.Close() })
	srv := startServer(t, cfg, st)

	c := dial(t, srv.Addr().String())
	defer c.close()
	c.expectGreeting()
	// Far more than the hard ceiling (16*16=256) with no newline: framing is
	// unrecoverable, server replies 421 and closes.
	c.raw(strings.Repeat("z", 5000))
	code, _ := c.readReply()
	if code != 421 {
		t.Fatalf("fatal overflow code = %d, want 421", code)
	}
}

func TestEndToEnd_AtCapacityReturns421(t *testing.T) {
	cfg := testConfig(t)
	cfg.Timeouts.IdleMS = 30000
	srv := startServer(t, cfg, failSink{})

	// Hold the maximum number of sessions idle, then exceed capacity.
	var held []*smtpClient
	for i := 0; i < 8; i++ {
		cl := dial(t, srv.Addr().String())
		cl.expectGreeting()
		held = append(held, cl)
	}
	defer func() {
		for _, cl := range held {
			cl.close()
		}
	}()

	extra := dial(t, srv.Addr().String())
	defer extra.close()
	code, _ := extra.readReply()
	if code != 421 {
		t.Fatalf("capacity-exceeded code = %d, want 421", code)
	}
}
