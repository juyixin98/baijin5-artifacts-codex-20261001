package server

import (
	"bufio"
	"context"
	"io"
	"log/slog"
	"net"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"imaplite/internal/auth"
	"imaplite/internal/fixture"
	"imaplite/internal/store"
)

// startTestServer boots a fully wired service on an ephemeral port backed by
// the bundled synthetic fixtures, returning the address and store handle.
func startTestServer(t *testing.T) (string, *store.Store) {
	t.Helper()
	ctx := context.Background()
	st, err := store.Open(ctx, filepath.Join(t.TempDir(), "t.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = st.Close() })

	loaded, err := fixture.Load(fixture.DefaultDataDir())
	if err != nil {
		t.Fatal(err)
	}
	if err := loaded.Provision(st); err != nil {
		t.Fatal(err)
	}
	users := auth.NewUserStore()
	if err := loaded.ProvisionAccounts(users); err != nil {
		t.Fatal(err)
	}
	srv := New(Config{
		Addr: "127.0.0.1:0", Store: st, Users: users,
		Logger: slog.New(slog.NewTextHandler(io.Discard, nil)),
	})
	if err := srv.Listen(); err != nil {
		t.Fatal(err)
	}
	go func() { _ = srv.Serve(ctx) }()
	t.Cleanup(func() { _ = srv.Close() })
	return srv.Addr(), st
}

// testConn is a minimal line-oriented IMAP client that understands literals
// in responses. It is intentionally separate from imapwire internals.
type testConn struct {
	t  *testing.T
	c  net.Conn
	br *bufio.Reader
}

func dialTest(t *testing.T, addr string) *testConn {
	t.Helper()
	c, err := net.DialTimeout("tcp", addr, 2*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	tc := &testConn{t: t, c: c, br: bufio.NewReader(c)}
	tc.readLine() // greeting
	return tc
}

func (tc *testConn) close() { _ = tc.c.Close() }

// send writes a command (optionally with one literal payload via {n}).
func (tc *testConn) send(tag, cmd string, lit []byte) {
	if lit != nil {
		head, tail, _ := strings.Cut(cmd, "{n}")
		_, _ = tc.c.Write([]byte(tag + " " + head + "{" + strconv.Itoa(len(lit)) + "}\r\n"))
		tc.readLine() // continuation
		_, _ = tc.c.Write(lit)
		_, _ = tc.c.Write([]byte(tail + "\r\n"))
		return
	}
	_, _ = tc.c.Write([]byte(tag + " " + cmd + "\r\n"))
}

// readLine returns one line, transparently consuming one response literal.
func (tc *testConn) readLine() string {
	line, err := tc.br.ReadString('\n')
	if err != nil {
		tc.t.Fatalf("read: %v", err)
	}
	if i := strings.Index(line, "{"); i >= 0 && strings.Contains(line[i:], "}") {
		j := strings.IndexByte(line[i:], '}')
		nStr := line[i+1 : i+j]
		if n, err := strconv.Atoi(nStr); err == nil {
			buf := make([]byte, n)
			_, _ = io.ReadFull(tc.br, buf)
		}
	}
	return line
}

// exec runs a command and returns all lines up to its tagged completion.
func (tc *testConn) exec(tag, cmd string, lit ...[]byte) []string {
	var payload []byte
	if len(lit) > 0 {
		payload = lit[0]
	}
	tc.send(tag, cmd, payload)
	var out []string
	for {
		l := tc.readLine()
		out = append(out, strings.TrimRight(l, "\r\n"))
		if strings.HasPrefix(l, tag+" ") {
			return out
		}
	}
}

// completion returns the status token (OK/NO/BAD) of a command's last line.
func completionStatus(lines []string, tag string) (status, code string) {
	last := lines[len(lines)-1]
	parts := strings.SplitN(strings.TrimPrefix(last, tag+" "), " ", 2)
	status = parts[0]
	if len(parts) == 2 && strings.HasPrefix(parts[1], "[") {
		if end := strings.IndexByte(parts[1], ']'); end >= 0 {
			code = parts[1][1:end]
		}
	}
	return status, code
}

func TestServerEndToEndCommandCoverage(t *testing.T) {
	addr, _ := startTestServer(t)
	c := dialTest(t, addr)
	defer c.close()

	check := func(tag, cmd string, lit ...[]byte) []string {
		lines := c.exec(tag, cmd, lit...)
		st, code := completionStatus(lines, tag)
		if st != "OK" {
			t.Fatalf("%s %s -> %s [%s] (lines=%v)", tag, cmd, st, code, lines)
		}
		return lines
	}

	check("C1", "CAPABILITY")
	check("L1", "LOGIN tester Tester#2025!")
	sel := check("S1", "SELECT INBOX")
	if !containsLine(sel, "* 5 EXISTS") {
		t.Fatalf("missing EXISTS: %v", sel)
	}
	if !containsLine(sel, "* OK [UIDVALIDITY 1001]") {
		t.Fatalf("missing UIDVALIDITY: %v", sel)
	}

	// FETCH variants.
	f1 := check("F1", "FETCH 1:5 (UID FLAGS RFC822.SIZE INTERNALDATE)")
	if countPrefix(f1, "* ") != 5 {
		t.Fatalf("want 5 FETCH records, got %v", f1)
	}
	check("F2", "FETCH 1 (ENVELOPE BODYSTRUCTURE BODY)")
	check("F3", "FETCH 3 (BODY[1] BODY[1.MIME] BODY[1.HEADER] RFC822 RFC822.HEADER RFC822.TEXT)")
	check("F4", "UID FETCH 1:3 (UID FLAGS)")
	check("F5", "UID FETCH 5 (BODY.PEEK[])")
	check("F6", "FETCH 1 (BODY[TEXT]<0.10>)")
	check("N1", "NOOP")

	// STORE add/replace/remove and silent.
	check("T1", "STORE 1 +FLAGS (\\Flagged)")
	check("T2", "STORE 1 FLAGS (\\Seen \\Draft)")
	check("T3", "STORE 1 -FLAGS (\\Draft)")
	check("T4", "UID STORE 4 +FLAGS.SILENT (\\Seen)")

	// EXPUNGE path: flag UID 2 then expunge.
	check("T5", "STORE 2 +FLAGS (\\Deleted)")
	exp := check("X1", "EXPUNGE")
	if !containsLine(exp, "* 2 EXPUNGE") {
		t.Fatalf("missing * 2 EXPUNGE: %v", exp)
	}

	// UNSELECT then reselect.
	check("U1", "UNSELECT")
	check("S2", "SELECT INBOX")
	check("L2", "LOGOUT")
}

func TestServerAuthAndGating(t *testing.T) {
	addr, _ := startTestServer(t)
	c := dialTest(t, addr)
	defer c.close()

	// Wrong password.
	lines := c.exec("A1", "LOGIN tester nope")
	if st, code := completionStatus(lines, "A1"); st != "NO" || code != "AUTHENTICATIONFAILED" {
		t.Fatalf("bad login: %s [%s]", st, code)
	}
	// Literal password (non-ASCII account) works.
	lines = c.exec("A2", "LOGIN lit {n}", []byte("litpäss"))
	if st, _ := completionStatus(lines, "A2"); st != "OK" {
		t.Fatalf("literal login: %v", lines)
	}

	// Unknown command is BAD and connection stays usable.
	lines = c.exec("Z1", "XYZZY")
	if st, _ := completionStatus(lines, "Z1"); st != "BAD" {
		t.Fatalf("unknown want BAD, got %s", st)
	}
	// Unsupported data item is BAD but stream stays aligned.
	c.exec("L3", "LOGIN tester Tester#2025!")
	c.exec("S1", "SELECT INBOX")
	lines = c.exec("F0", "FETCH 1 (BOGUSITEM)")
	if st, _ := completionStatus(lines, "F0"); st != "BAD" {
		t.Fatalf("bogus item want BAD")
	}
	lines = c.exec("F9", "FETCH 1 (UID)")
	if st, _ := completionStatus(lines, "F9"); st != "OK" {
		t.Fatalf("stream not aligned after BAD: %v", lines)
	}
	// Wrong-state commands.
	c2 := dialTest(t, addr)
	defer c2.close()
	if st, _ := completionStatus(c2.exec("E1", "EXPUNGE"), "E1"); st != "BAD" {
		t.Fatalf("EXPUNGE pre-auth want BAD")
	}
}

func TestServerExaminerAndPermissionAndNonexistent(t *testing.T) {
	addr, _ := startTestServer(t)
	c := dialTest(t, addr)
	defer c.close()
	c.exec("L1", "LOGIN tester Tester#2025!")

	// EXAMINE opens read-only; writes refused with READ-ONLY.
	c.exec("S1", "EXAMINE INBOX")
	if st, code := completionStatus(c.exec("T1", "STORE 1 +FLAGS (\\Deleted)"), "T1"); st != "NO" || code != "READ-ONLY" {
		t.Fatalf("EXAMINE STORE want NO READ-ONLY")
	}
	if st, code := completionStatus(c.exec("X1", "EXPUNGE"), "X1"); st != "NO" || code != "READ-ONLY" {
		t.Fatalf("EXAMINE EXPUNGE want NO READ-ONLY")
	}
	c.exec("U1", "UNSELECT")

	// Permission is checked before existence (no existence oracle). tester
	// is whitelisted, so GHOST -> NO [NOPERM].
	if st, code := completionStatus(c.exec("S2", "SELECT GHOST"), "S2"); st != "NO" || code != "NOPERM" {
		t.Fatalf("whitelisted account selecting GHOST want NO NOPERM, got %s [%s]", st, code)
	}

	// An unrestricted account gets the true NONEXISTENT answer.
	c.exec("L2", "LOGOUT")
	cAdmin := dialTest(t, addr)
	defer cAdmin.close()
	cAdmin.exec("A0", "LOGIN admin Admin#2025!")
	if st, code := completionStatus(cAdmin.exec("S9", "SELECT GHOST"), "S9"); st != "NO" || code != "NONEXISTENT" {
		t.Fatalf("unrestricted account selecting GHOST want NO NONEXISTENT, got %s [%s]", st, code)
	}

	// Permission: watcher may not open ProjectX.
	c2 := dialTest(t, addr)
	defer c2.close()
	c2.exec("W1", "LOGIN watcher Watcher#2025!")
	if st, code := completionStatus(c2.exec("S3", "SELECT ProjectX"), "S3"); st != "NO" || code != "NOPERM" {
		t.Fatalf("watcher ProjectX want NO NOPERM")
	}
}

func TestServerStaleUIDValidity(t *testing.T) {
	addr, st := startTestServer(t)
	c := dialTest(t, addr)
	defer c.close()
	c.exec("L1", "LOGIN tester Tester#2025!")
	c.exec("S1", "SELECT INBOX")

	// UID 1 is fetchable in the current epoch.
	if s, _ := completionStatus(c.exec("U1", "UID FETCH 1 (UID)"), "U1"); s != "OK" {
		t.Fatal("UID 1 should fetch before rotation")
	}

	// An out-of-band actor (control plane / new process) rotates the epoch.
	newV, err := st.RotateUIDValidity(context.Background(), "INBOX")
	if err != nil || newV != 1002 {
		t.Fatalf("rotate: %d %v", newV, err)
	}

	// The still-selected connection must refuse with NO [UIDVALIDITY] and
	// return no message data for the stale UID.
	lines := c.exec("U2", "UID FETCH 1 (UID)")
	if s, code := completionStatus(lines, "U2"); s != "NO" || code != "UIDVALIDITY" {
		t.Fatalf("stale epoch want NO [UIDVALIDITY], got %s [%s]", s, code)
	}
	for _, l := range lines {
		if strings.Contains(l, " FETCH (") {
			t.Fatalf("stale UID must not return FETCH data: %s", l)
		}
	}

	// Re-SELECT advertises the new epoch with zero messages.
	sel := c.exec("S2", "SELECT INBOX")
	if !containsLine(sel, "* OK [UIDVALIDITY 1002]") {
		t.Fatalf("reselect missing new UIDVALIDITY: %v", sel)
	}
	if !containsLine(sel, "* 0 EXISTS") {
		t.Fatalf("rotated epoch should be empty: %v", sel)
	}
}

func TestServerAdditionalRejectionsAndUIDExpunge(t *testing.T) {
	addr, _ := startTestServer(t)
	c := dialTest(t, addr)
	defer c.close()

	// SASL/STARTTLS explicitly unavailable before login.
	if st, code := completionStatus(c.exec("T0", "STARTTLS"), "T0"); st != "BAD" || code != "UNSUPPORTED-STEPS" {
		t.Fatalf("STARTTLS want BAD UNSUPPORTED-STEPS, got %s [%s]", st, code)
	}
	if st, code := completionStatus(c.exec("A0", "AUTHENTICATE PLAIN"), "A0"); st != "BAD" || code != "UNSUPPORTED-STEPS" {
		t.Fatalf("AUTHENTICATE want BAD, got %s [%s]", st, code)
	}
	// Login required for mailbox commands.
	if st, _ := completionStatus(c.exec("U0", "UID FETCH 1 (UID)"), "U0"); st != "BAD" {
		t.Fatalf("UID before auth want BAD, got %s", st)
	}
	// LOGIN argument validation.
	if st, _ := completionStatus(c.exec("L0", "LOGIN onlyone"), "L0"); st != "BAD" {
		t.Fatalf("LOGIN with one arg want BAD")
	}

	c.exec("L1", "LOGIN tester Tester#2025!")
	c.exec("S1", "SELECT INBOX")

	// Unknown UID sub-command and unsupported UID verb.
	if st, _ := completionStatus(c.exec("U1", "UID FROBNICATE 1"), "U1"); st != "BAD" {
		t.Fatalf("UID FROBNICATE want BAD")
	}
	if st, _ := completionStatus(c.exec("U2", "UID SEARCH ALL"), "U2"); st != "BAD" {
		t.Fatalf("UID SEARCH (unsupported) want BAD")
	}

	// FETCH of a UID set that matches nothing: OK with zero FETCH records.
	lines := c.exec("F7", "UID FETCH 900 (UID)")
	if countPrefix(lines, "* ") != 0 {
		t.Fatalf("non-matching UID set should return no FETCH records: %v", lines)
	}
	if st, _ := completionStatus(lines, "F7"); st != "OK" {
		t.Fatalf("empty UID FETCH want OK")
	}

	// Malformed STORE op and flag-list shape.
	if st, _ := completionStatus(c.exec("T0", "STORE 1 WATFLAGS (\\Seen)"), "T0"); st != "BAD" {
		t.Fatalf("bad STORE op want BAD")
	}
	if st, _ := completionStatus(c.exec("T9", "STORE 1 FLAGS \\Seen"), "T9"); st != "BAD" {
		t.Fatalf("non-list STORE flags want BAD")
	}

	// UID EXPUNGE restricted to one UID: flag UIDs 1 and 2, expunge only {2}.
	c.exec("TA", "UID STORE 1,2 +FLAGS.SILENT (\\Deleted)")
	ex := c.exec("X2", "UID EXPUNGE 2")
	if !containsLine(ex, "* 2 EXPUNGE") {
		t.Fatalf("UID EXPUNGE should remove only seq 2: %v", ex)
	}
	// UID 1 remains flagged and present.
	f := c.exec("F8", "UID FETCH 1 (FLAGS)")
	if st, _ := completionStatus(f, "F8"); st != "OK" {
		t.Fatalf("UID 1 should survive restricted UID EXPUNGE")
	}
}

func TestServerObserverReceivesExternalExpunge(t *testing.T) {
	addr, _ := startTestServer(t)
	observer := dialTest(t, addr)
	defer observer.close()
	observer.exec("L1", "LOGIN watcher Watcher#2025!")
	observer.exec("S1", "SELECT INBOX")

	actor := dialTest(t, addr)
	defer actor.close()
	actor.exec("L1", "LOGIN tester Tester#2025!")
	actor.exec("S1", "SELECT INBOX")
	actor.exec("D1", "STORE 1 +FLAGS (\\Deleted)")
	actor.exec("X1", "EXPUNGE")

	// The observer's next command (NOOP) surfaces the unsolicited EXPUNGE.
	time.Sleep(150 * time.Millisecond)
	lines := observer.exec("N1", "NOOP")
	if !containsLine(lines, "* 1 EXPUNGE") {
		t.Fatalf("observer should see external '* 1 EXPUNGE': %v", lines)
	}
}

func TestServerMalformedFrameDisconnectsCleanly(t *testing.T) {
	addr, _ := startTestServer(t)
	raw, err := net.Dial("tcp", addr)
	if err != nil {
		t.Fatal(err)
	}
	defer raw.Close()
	br := bufio.NewReader(raw)
	br.ReadString('\n') // greeting
	// Send a command with a parenthesized token left unterminated then EOF:
	// the framing layer reports a pre-tag BAD and a BYE, then closes.
	_, _ = raw.Write([]byte("FETCH 1:2 (UID FLAGS\r\n"))
	deadline := time.Now().Add(2 * time.Second)
	var got strings.Builder
	for time.Now().Before(deadline) {
		raw.SetReadDeadline(time.Now().Add(300 * time.Millisecond))
		l, err := br.ReadString('\n')
		if err != nil {
			break
		}
		got.WriteString(l)
	}
	out := got.String()
	if !strings.Contains(out, "BAD") || !strings.Contains(out, "BYE") {
		t.Fatalf("malformed frame should yield BAD then BYE, got %q", out)
	}
}

func containsLine(lines []string, want string) bool {
	for _, l := range lines {
		if strings.TrimSpace(l) == want {
			return true
		}
	}
	return false
}

func countPrefix(lines []string, p string) int {
	n := 0
	for _, l := range lines {
		if strings.HasPrefix(l, p) && strings.Contains(l, " FETCH (") {
			n++
		}
	}
	return n
}
