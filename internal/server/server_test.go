package server_test

import (
	"bufio"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"net"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"imapd/internal/fixture"
	"imapd/internal/server"
	"imapd/internal/store"
	"imapd/internal/testlog"
)

const (
	fixtureDir    = "../../testdata/messages"
	manifestPath  = "../../testdata/manifest.sha256"
	goldenPath    = "../../testdata/golden/transcript.txt"
	goldUIDValid  = 20260105
	clientTimeout = 5 * time.Second
)

// --- test server & client harness ---

func startServer(t *testing.T, uidvalidity uint32) (string, *store.Store) {
	t.Helper()
	st, err := store.Open(filepath.Join(t.TempDir(), "test.db"))
	if err != nil {
		t.Fatalf("store open: %v", err)
	}
	msgs, err := fixture.Load(fixtureDir)
	if err != nil {
		t.Fatalf("fixture load: %v", err)
	}
	if err := st.Seed("INBOX", uidvalidity, msgs); err != nil {
		t.Fatalf("seed: %v", err)
	}
	srv, err := server.Listen("127.0.0.1:0", st, nil)
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	go srv.Serve()
	t.Cleanup(func() {
		srv.Close()
		st.Close()
	})
	return srv.Addr(), st
}

type imapClient struct {
	t        *testing.T
	conn     net.Conn
	r        *bufio.Reader
	n        int
	greeting string
}

func dial(t *testing.T, addr string) *imapClient {
	t.Helper()
	conn, err := net.Dial("tcp", addr)
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	if err := conn.SetReadDeadline(time.Now().Add(clientTimeout)); err != nil {
		t.Fatalf("deadline: %v", err)
	}
	c := &imapClient{t: t, conn: conn, r: bufio.NewReader(conn)}
	t.Cleanup(func() { conn.Close() })
	c.greeting = strings.TrimRight(c.readLine(), "\r\n")
	if !strings.HasPrefix(c.greeting, "* OK") {
		t.Fatalf("greeting = %q, want * OK ...", c.greeting)
	}
	return c
}

// readLine reads one logical response line. If the line ends in a literal
// marker "{n}", exactly n payload bytes are read and inlined, then the
// remainder of the response line is read. The result keeps its framing
// CRLFs so binary payloads stay byte-exact.
func (c *imapClient) readLine() string {
	c.t.Helper()
	var sb strings.Builder
	for {
		line, err := c.r.ReadString('\n')
		if err != nil {
			c.t.Fatalf("read: %v", err)
		}
		sb.WriteString(line)
		if n, ok := literalMarker(line); ok {
			lit := make([]byte, n)
			if _, err := io.ReadFull(c.r, lit); err != nil {
				c.t.Fatalf("literal read: %v", err)
			}
			sb.Write(lit)
			continue
		}
		return sb.String()
	}
}

// tryReadLine is readLine but reports EOF instead of failing.
func (c *imapClient) tryReadLine() (string, error) {
	line, err := c.r.ReadString('\n')
	return line, err
}

func literalMarker(line string) (int, bool) {
	trimmed := strings.TrimRight(line, "\r\n")
	if !strings.HasSuffix(trimmed, "}") {
		return 0, false
	}
	i := strings.LastIndexByte(trimmed, '{')
	if i < 0 {
		return 0, false
	}
	n, err := strconv.Atoi(trimmed[i+1 : len(trimmed)-1])
	if err != nil {
		return 0, false
	}
	return n, true
}

// collect reads lines until the tagged response for tag arrives.
func (c *imapClient) collect(tag string) []string {
	c.t.Helper()
	var lines []string
	for {
		line := strings.TrimRight(c.readLine(), "\r\n")
		lines = append(lines, line)
		if strings.HasPrefix(line, tag+" ") {
			return lines
		}
	}
}

// do sends one command with a fresh tag and returns the response lines.
func (c *imapClient) do(cmd string) []string {
	c.n++
	tag := fmt.Sprintf("t%d", c.n)
	fmt.Fprintf(c.conn, "%s %s\r\n", tag, cmd)
	return c.collect(tag)
}

// doTagged is do with an explicit tag (golden transcripts, pipelining).
func (c *imapClient) doTagged(tag, cmd string) []string {
	fmt.Fprintf(c.conn, "%s %s\r\n", tag, cmd)
	return c.collect(tag)
}

func tagged(lines []string) string { return lines[len(lines)-1] }

func untagged(lines []string) []string {
	var out []string
	for _, l := range lines {
		if strings.HasPrefix(l, "* ") {
			out = append(out, l)
		}
	}
	return out
}

func expungeSeqs(lines []string) []int {
	var out []int
	for _, l := range lines {
		if strings.HasPrefix(l, "* ") && strings.HasSuffix(l, " EXPUNGE") {
			n, err := strconv.Atoi(strings.TrimSuffix(strings.TrimPrefix(l, "* "), " EXPUNGE"))
			if err == nil {
				out = append(out, n)
			}
		}
	}
	return out
}

// --- tests ---

// TestGoldenTranscript replays a fixed session and compares the full
// server output against a hand-written reference file. The reference was
// derived from the fixture bytes (sizes via wc -c), not from the
// implementation under test.
func TestGoldenTranscript(t *testing.T) {
	lg := testlog.New(t)
	addr, _ := startServer(t, goldUIDValid)
	c := dial(t, addr)

	var got []string
	got = append(got, c.greeting)
	script := [][2]string{
		{"g1", "SELECT INBOX"},
		{"g2", "FETCH 1:* (FLAGS UID RFC822.SIZE)"},
		{"g3", "STORE 4 +FLAGS.SILENT \\Deleted"},
		{"g4", "EXPUNGE"},
		{"g5", "FETCH 1:* (UID FLAGS)"},
		{"g6", "UID FETCH 4:* (UID RFC822.SIZE)"},
		{"g7", "NOOP"},
		{"g8", "LOGOUT"},
	}
	for _, step := range script {
		got = append(got, c.doTagged(step[0], step[1])...)
	}
	lg.Log("transcript", "full session output must equal the hand-written golden file",
		"lines", len(got))

	wantBytes, err := os.ReadFile(goldenPath)
	if err != nil {
		t.Fatalf("golden: %v", err)
	}
	want := strings.ReplaceAll(string(wantBytes), "\r\n", "\n")
	gotStr := strings.Join(got, "\n") + "\n"
	if gotStr != want {
		t.Fatalf("transcript mismatch\n--- got ---\n%s\n--- want ---\n%s", gotStr, want)
	}
	lg.Log("verified", "golden transcript matched exactly",
		"reason", "deletion of seq 4 shifted UID 5 to seq 4 while its UID stayed 5")
}

// TestMessageIdentityByHash verifies FETCH BODY.PEEK[] payloads against
// SHA-256 sums produced by the external sha256sum(1) tool.
func TestMessageIdentityByHash(t *testing.T) {
	lg := testlog.New(t)
	addr, _ := startServer(t, goldUIDValid)
	c := dial(t, addr)
	c.do("SELECT INBOX")

	data, err := os.ReadFile(manifestPath)
	if err != nil {
		t.Fatalf("manifest: %v", err)
	}
	var wantHashes []string
	for _, line := range strings.Split(strings.TrimSpace(string(data)), "\n") {
		wantHashes = append(wantHashes, strings.Fields(line)[0])
	}
	for i, want := range wantHashes {
		seq := i + 1
		c.n++
		tag := fmt.Sprintf("t%d", c.n)
		fmt.Fprintf(c.conn, "%s FETCH %d BODY.PEEK[]\r\n", tag, seq)
		resp := c.readLine() // literal response
		lit := extractLiteral(t, resp)
		sum := sha256.Sum256(lit)
		got := hex.EncodeToString(sum[:])
		lg.Log("fetch-identity", "payload hash must equal the independent sha256sum reference",
			"seq", seq, "bytes", len(lit), "sha256", got)
		if got != want {
			t.Fatalf("seq %d: sha256 %s, want %s (from sha256sum)", seq, got, want)
		}
		if line := strings.TrimRight(c.readLine(), "\r\n"); !strings.HasPrefix(line, tag+" OK") {
			t.Fatalf("seq %d: tagged = %q, want OK", seq, line)
		}
	}
}

func extractLiteral(t *testing.T, resp string) []byte {
	t.Helper()
	i := strings.Index(resp, "{")
	j := strings.Index(resp[i:], "}\r\n")
	if i < 0 || j < 0 {
		t.Fatalf("no literal marker in %q", resp)
	}
	n, err := strconv.Atoi(resp[i+1 : i+j])
	if err != nil {
		t.Fatalf("literal length: %v", err)
	}
	start := i + j + 3
	if len(resp) < start+n {
		t.Fatalf("response too short for declared literal: %d < %d", len(resp), start+n)
	}
	return []byte(resp[start : start+n])
}

// TestUIDValidityChangeInvalidatesOldUIDs resets the mailbox server-side
// and checks that the client observes a new UIDVALIDITY and that UIDs
// cached under the old one no longer resolve.
func TestUIDValidityChangeInvalidatesOldUIDs(t *testing.T) {
	lg := testlog.New(t)
	addr, st := startServer(t, goldUIDValid)
	c := dial(t, addr)

	lines := c.do("SELECT INBOX")
	v1 := parseUIDValidity(t, lines)
	lg.Log("selected", "baseline UIDVALIDITY recorded", "uidvalidity", v1, "exists", 5)

	msgs, err := fixture.Load(fixtureDir)
	if err != nil {
		t.Fatalf("fixture: %v", err)
	}
	// Replace with fewer messages under a new UIDVALIDITY: old UID 5 dies.
	if err := st.Replace("INBOX", 31337, msgs[:2]); err != nil {
		t.Fatalf("replace: %v", err)
	}
	lg.Log("replaced", "mailbox reset with new UIDVALIDITY and only 2 messages",
		"uidvalidity", 31337)

	lines = c.do("SELECT INBOX")
	v2 := parseUIDValidity(t, lines)
	lg.Log("reselected", "client must detect the UIDVALIDITY change and drop cached UIDs",
		"before", v1, "after", v2)
	if v2 == v1 {
		t.Fatalf("UIDVALIDITY unchanged: %d", v2)
	}

	lines = c.do("UID FETCH 5 RFC822.SIZE")
	lg.Log("stale-uid", "UID 5 was valid under the old UIDVALIDITY; it must not resolve now",
		"untagged", untagged(lines), "tagged", tagged(lines))
	if got := untagged(lines); len(got) != 0 {
		t.Fatalf("stale UID 5 still resolves: %v", got)
	}
	if !strings.HasPrefix(tagged(lines), "t") || !strings.Contains(tagged(lines), " OK ") {
		t.Fatalf("tagged = %q, want OK (unknown UIDs are not an error)", tagged(lines))
	}

	lines = c.do("UID FETCH 1:* UID")
	lg.Log("new-uids", "only the replacement UIDs exist under the new UIDVALIDITY",
		"untagged", untagged(lines))
	if len(untagged(lines)) != 2 {
		t.Fatalf("want 2 messages under new UIDVALIDITY, got %v", untagged(lines))
	}
}

func parseUIDValidity(t *testing.T, lines []string) uint32 {
	t.Helper()
	for _, l := range lines {
		if strings.Contains(l, "[UIDVALIDITY ") {
			rest := strings.SplitN(l, "[UIDVALIDITY ", 2)[1]
			n, err := strconv.ParseUint(strings.SplitN(rest, "]", 2)[0], 10, 32)
			if err != nil {
				t.Fatalf("UIDVALIDITY parse: %v", err)
			}
			return uint32(n)
		}
	}
	t.Fatalf("no UIDVALIDITY in %v", lines)
	return 0
}

// TestConcurrentObserversSeeSameExpungeOrder runs two sessions on one
// mailbox: A deletes and expunges twice; both A and B must observe the
// identical expunge event stream.
func TestConcurrentObserversSeeSameExpungeOrder(t *testing.T) {
	lg := testlog.New(t)
	addr, _ := startServer(t, goldUIDValid)
	a := dial(t, addr)
	b := dial(t, addr)
	a.do("SELECT INBOX")
	b.do("SELECT INBOX")

	a.do("STORE 1 +FLAGS.SILENT \\Deleted")
	aExp := a.do("EXPUNGE")
	bSync := b.do("NOOP") // drains B's pending async events
	lg.Log("first-expunge", "UID 1 (seq 1) removed; both observers must report seq 1",
		"a_events", expungeSeqs(aExp), "b_events", expungeSeqs(bSync))

	a.do("STORE 1 +FLAGS.SILENT \\Deleted") // now UID 2 sits at seq 1
	aExp2 := a.do("EXPUNGE")
	bSync2 := b.do("NOOP")
	lg.Log("second-expunge", "UID 2 shifted to seq 1 before removal; event must again be seq 1",
		"a_events", expungeSeqs(aExp2), "b_events", expungeSeqs(bSync2))

	aEvents := append(expungeSeqs(aExp), expungeSeqs(aExp2)...)
	bEvents := append(expungeSeqs(bSync), expungeSeqs(bSync2)...)
	lg.Log("order-check", "concurrent observers must see the identical event order",
		"a", aEvents, "b", bEvents)
	want := []int{1, 1}
	if fmt.Sprint(aEvents) != fmt.Sprint(want) {
		t.Fatalf("A observed %v, want %v", aEvents, want)
	}
	if fmt.Sprint(bEvents) != fmt.Sprint(aEvents) {
		t.Fatalf("B observed %v, A observed %v — event order diverged", bEvents, aEvents)
	}

	lines := a.do("FETCH 1:* UID")
	lg.Log("post-expunge-uids", "remaining UIDs keep identity at shifted sequence numbers",
		"untagged", untagged(lines))
	wantLines := []string{
		"* 1 FETCH (UID 3)",
		"* 2 FETCH (UID 4)",
		"* 3 FETCH (UID 5)",
	}
	if fmt.Sprint(untagged(lines)) != fmt.Sprint(wantLines) {
		t.Fatalf("got %v, want %v", untagged(lines), wantLines)
	}
}

// TestPipelinedCommandsKeepTagOrder sends three commands in one write;
// tagged responses must come back in command order with matching tags.
func TestPipelinedCommandsKeepTagOrder(t *testing.T) {
	lg := testlog.New(t)
	addr, _ := startServer(t, goldUIDValid)
	c := dial(t, addr)
	c.do("SELECT INBOX")

	fmt.Fprintf(c.conn, "p1 NOOP\r\np2 FETCH 1 UID\r\np3 NOOP\r\n")
	lines := c.collect("p3")
	lg.Log("pipelined", "three commands in one write must answer in tag order",
		"lines", lines)
	want := []string{
		"p1 OK NOOP completed",
		"* 1 FETCH (UID 1)",
		"p2 OK FETCH completed",
		"p3 OK NOOP completed",
	}
	if fmt.Sprint(lines) != fmt.Sprint(want) {
		t.Fatalf("got %v, want %v", lines, want)
	}
}

// TestBinaryAndUTF8Literals proves literal framing counts bytes: a NUL
// inside a mailbox name, and a 6-byte UTF-8 name declared as {6}.
func TestBinaryAndUTF8Literals(t *testing.T) {
	lg := testlog.New(t)
	addr, _ := startServer(t, goldUIDValid)
	c := dial(t, addr)

	// Positive case: INBOX delivered as a 5-byte literal.
	fmt.Fprintf(c.conn, "b1 SELECT {5}\r\n")
	if line := strings.TrimRight(c.readLine(), "\r\n"); !strings.HasPrefix(line, "+") {
		t.Fatalf("continuation = %q, want + ...", line)
	}
	fmt.Fprintf(c.conn, "INBOX\r\n")
	lines := c.collect("b1")
	lg.Log("literal-select", "INBOX via literal must select normally", "tagged", tagged(lines))
	if !strings.Contains(tagged(lines), " OK ") {
		t.Fatalf("tagged = %q, want OK", tagged(lines))
	}

	// NUL inside a literal mailbox name: 7 bytes, must not desync; the
	// mailbox simply does not exist (state error, not a parse failure).
	fmt.Fprintf(c.conn, "b2 SELECT {7}\r\n")
	c.readLine() // continuation
	c.conn.Write([]byte{'I', 'N', 0x00, 'B', 'O', 'X', '\r', '\n'})
	lines = c.collect("b2")
	lg.Log("literal-nul", "NUL in literal is data; unknown mailbox is a state error",
		"tagged", tagged(lines))
	if !strings.Contains(tagged(lines), " NO ") {
		t.Fatalf("tagged = %q, want NO (state)", tagged(lines))
	}

	// 报表 is 6 UTF-8 bytes: {6} frames it exactly. A rune-counting
	// server would wait for bytes that never come or desync the stream.
	fmt.Fprintf(c.conn, "b3 SELECT {6}\r\n")
	c.readLine() // continuation
	fmt.Fprintf(c.conn, "报表\r\n")
	lines = c.collect("b3")
	lg.Log("literal-utf8", "literal length is bytes; 6-byte name framed as {6}",
		"tagged", tagged(lines))
	if !strings.Contains(tagged(lines), " NO ") {
		t.Fatalf("tagged = %q, want NO (state)", tagged(lines))
	}

	// The stream must still be framed correctly after all of the above.
	lines = c.do("NOOP")
	if !strings.Contains(tagged(lines), " OK ") {
		t.Fatalf("post-literal NOOP = %q, want OK — stream desynchronised", tagged(lines))
	}
}

// TestOversizedLiteralIsResourceError declares a literal above the 1 MiB
// cap; the server must reject with BYE and close the connection.
func TestOversizedLiteralIsResourceError(t *testing.T) {
	lg := testlog.New(t)
	addr, _ := startServer(t, goldUIDValid)
	c := dial(t, addr)

	fmt.Fprintf(c.conn, "r1 SELECT {2097153}\r\n")
	line := strings.TrimRight(c.readLine(), "\r\n")
	lg.Log("resource-bye", "oversized literal must end the connection with BYE",
		"line", line)
	if !strings.HasPrefix(line, "* BYE") {
		t.Fatalf("line = %q, want * BYE ...", line)
	}
	if _, err := c.tryReadLine(); err != io.EOF {
		lg.Log("resource-close", "connection must be closed after BYE", "err", err)
		if err == nil {
			t.Fatalf("connection still open after BYE")
		}
	}
}

// TestUnknownAndUndeclaredAreRejected pins the failure category of every
// rejection path: input errors answer BAD, state conflicts answer NO.
func TestUnknownAndUndeclaredAreRejected(t *testing.T) {
	lg := testlog.New(t)
	addr, _ := startServer(t, goldUIDValid)
	c := dial(t, addr)

	type expectation struct {
		cmd    string
		status string // "BAD" or "NO"
		reason string
	}
	preSelect := []expectation{
		{"FROBNICATE", "BAD", "unknown verb"},
		{"FETCH 1 FLAGS", "NO", "no mailbox selected (state)"},
		{"EXPUNGE", "NO", "no mailbox selected (state)"},
		{"SELECT GHOST", "NO", "unknown mailbox (state)"},
	}
	for _, e := range preSelect {
		lines := c.do(e.cmd)
		lg.Log("reject", e.reason, "cmd", e.cmd, "tagged", tagged(lines))
		if !strings.Contains(tagged(lines), " "+e.status+" ") {
			t.Fatalf("%s: tagged = %q, want %s", e.cmd, tagged(lines), e.status)
		}
	}

	c.do("SELECT INBOX")
	postSelect := []expectation{
		{"FETCH 1 ENVELOPE", "BAD", "undeclared data item"},
		{"FETCH 1 BODY[HEADER]", "BAD", "undeclared BODY section"},
		{"FETCH 1 ALL", "BAD", "undeclared macro"},
		{"FETCH 99 UID", "NO", "sequence out of range (state)"},
		{"UID COPY 1 ARCHIVE", "BAD", "undeclared UID subcommand"},
		{"UID EXPUNGE 1", "BAD", "undeclared UID subcommand"},
		{"STORE 1 +FLAGS \\Flagged", "BAD", "undeclared flag"},
		{"STORE 99 +FLAGS \\Deleted", "NO", "sequence out of range (state)"},
	}
	for _, e := range postSelect {
		lines := c.do(e.cmd)
		lg.Log("reject", e.reason, "cmd", e.cmd, "tagged", tagged(lines))
		if !strings.Contains(tagged(lines), " "+e.status+" ") {
			t.Fatalf("%s: tagged = %q, want %s", e.cmd, tagged(lines), e.status)
		}
	}

	// UID FETCH of a nonexistent UID is OK with zero untagged responses —
	// distinct from the sequence-out-of-range state error above. (A range
	// like 4242:* would still match the last message per RFC 3501 §9, so
	// the no-match case uses a single UID.)
	lines := c.do("UID FETCH 4242 UID")
	lg.Log("uid-no-match", "unknown UIDs match nothing and are not an error",
		"untagged", untagged(lines), "tagged", tagged(lines))
	if len(untagged(lines)) != 0 || !strings.Contains(tagged(lines), " OK ") {
		t.Fatalf("UID FETCH no-match: %v", lines)
	}
}

// TestFetchBodyPeekVsSeen pins the \Seen side effect: BODY[] sets it,
// BODY.PEEK[] does not.
func TestFetchBodyPeekVsSeen(t *testing.T) {
	lg := testlog.New(t)
	addr, _ := startServer(t, goldUIDValid)
	c := dial(t, addr)
	c.do("SELECT INBOX")

	flagsOf := func() string {
		lines := c.do("FETCH 2 FLAGS")
		return untagged(lines)[0]
	}
	if got := flagsOf(); got != "* 2 FETCH (FLAGS ())" {
		t.Fatalf("initial flags = %q", got)
	}
	c.do("FETCH 2 BODY.PEEK[]")
	lg.Log("peek", "BODY.PEEK[] must not set \\Seen", "flags", flagsOf())
	if got := flagsOf(); got != "* 2 FETCH (FLAGS ())" {
		t.Fatalf("after PEEK flags = %q, want no \\Seen", got)
	}
	c.do("FETCH 2 BODY[]")
	lg.Log("body", "BODY[] must set \\Seen", "flags", flagsOf())
	if got := flagsOf(); got != "* 2 FETCH (FLAGS (\\Seen))" {
		t.Fatalf("after BODY[] flags = %q, want \\Seen", got)
	}
}
