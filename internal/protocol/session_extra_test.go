package protocol

import (
	"strings"
	"testing"
)

func TestHeloGreeting(t *testing.T) {
	store := &recordingStore{}
	c := newSession(t, store, defaultLimits())
	c.expectReply("220 smtpsink.local Service ready")
	c.send("HELO client.test")
	c.expectReply("250 smtpsink.local greets client.test")
	c.send("MAIL FROM:<alice@example.test>")
	c.expectReply("250 2.1.0 Sender OK")
}

func TestEhloRequiresArgument(t *testing.T) {
	store := &recordingStore{}
	c := newSession(t, store, defaultLimits())
	c.expectReply("220 smtpsink.local Service ready")
	c.send("EHLO")
	c.expectReply("501 5.5.4 EHLO requires domain argument")
}

func TestQuitBeforeGreeting(t *testing.T) {
	store := &recordingStore{}
	done, conn, rd := newPipeSession(t, store, defaultLimits())
	if banner := readLine(t, rd); !strings.HasPrefix(banner, "220 ") {
		t.Fatalf("banner = %q", banner)
	}
	sendLines(t, conn, "QUIT")
	if line := readLine(t, rd); !strings.HasPrefix(line, "221 ") {
		t.Fatalf("QUIT reply = %q", line)
	}
	if err := <-done; err != nil {
		t.Fatalf("session should end cleanly on QUIT, got %v", err)
	}
}

func TestBadDotSequenceAbortsAndDisconnects(t *testing.T) {
	store := &recordingStore{}
	done, conn, rd := newPipeSession(t, store, defaultLimits())
	readLine(t, rd) // banner
	sendLines(t, conn, "EHLO client.test")
	for i := 0; i < 3; i++ {
		readLine(t, rd)
	}
	sendLines(t, conn, "MAIL FROM:<alice@example.test>")
	readLine(t, rd)
	sendLines(t, conn, "RCPT TO:<bob@example.test>")
	readLine(t, rd)
	sendLines(t, conn, "DATA")
	readLine(t, rd) // 354
	// ".x" is neither a stuffed line nor the terminator: the stream
	// cannot be resynced, so the session must abort and hang up.
	// Raw write without CRLF: the server fails after consuming ".x" and
	// would not drain a trailing CRLF (net.Pipe writes would block).
	if _, err := conn.Write([]byte(".x")); err != nil {
		t.Fatalf("write: %v", err)
	}
	if err := <-done; err == nil {
		t.Fatalf("session should end with an error on bad dot sequence")
	}
	if got := len(store.messages()); got != 0 {
		t.Fatalf("committed %d messages from malformed DATA", got)
	}
	if store.aborts() != 1 {
		t.Fatalf("pending message not aborted (aborts=%d)", store.aborts())
	}
}

func TestParsePath(t *testing.T) {
	cases := []struct {
		arg, prefix, want string
		wantErr           bool
	}{
		{"FROM:<a@b.test>", "FROM:", "a@b.test", false},
		{"from:<a@b.test>", "FROM:", "a@b.test", false}, // case-insensitive
		{"FROM: <a@b.test>", "FROM:", "a@b.test", false},
		{"FROM:<>", "FROM:", "", false},
		{"FROM:<a@b.test> SIZE=123", "FROM:", "a@b.test", false}, // params ignored
		{"FROM:a@b.test", "FROM:", "", true},                     // angle brackets required
		{"TO:<a@b.test>", "FROM:", "", true},                     // wrong prefix
		{"FROM:<a@b.test", "FROM:", "", true},                    // unterminated
		{"", "FROM:", "", true},
	}
	for _, tc := range cases {
		got, err := parsePath(tc.arg, tc.prefix)
		if tc.wantErr {
			if err == nil {
				t.Errorf("parsePath(%q, %q): want error, got %q", tc.arg, tc.prefix, got)
			}
			continue
		}
		if err != nil || got != tc.want {
			t.Errorf("parsePath(%q, %q) = %q, %v; want %q", tc.arg, tc.prefix, got, err, tc.want)
		}
	}
}

func TestSplitAddress(t *testing.T) {
	if _, d, err := splitAddress("Bob@Example.TEST"); err != nil || d != "example.test" {
		t.Fatalf("domain not lower-cased: %q, %v", d, err)
	}
	for _, bad := range []string{"", "noatsign", "@domain.test", "local@", "@"} {
		if _, _, err := splitAddress(bad); err == nil {
			t.Errorf("splitAddress(%q): want error", bad)
		}
	}
}

func TestMaskAddress(t *testing.T) {
	if got := MaskAddress("alice@example.test"); got != "a***@example.test" {
		t.Fatalf("mask = %q", got)
	}
	if got := MaskAddress("no-at-sign"); got != "***" {
		t.Fatalf("mask = %q", got)
	}
}
