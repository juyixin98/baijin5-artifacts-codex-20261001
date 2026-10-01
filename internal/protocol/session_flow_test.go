package protocol

import (
	"errors"
	"strings"
	"testing"
)

func TestCommandSequenceEnforced(t *testing.T) {
	store := &recordingStore{}
	c := newSession(t, store, defaultLimits())
	c.expectReply("220 smtpsink.local Service ready")

	// DATA and RCPT before any MAIL are sequence errors.
	c.send("DATA")
	c.expectReply("503 5.5.1 Bad sequence of commands")
	c.send("RCPT TO:<bob@example.test>")
	c.expectReply("503 5.5.1 Bad sequence of commands")

	c.greet2()
	c.send("RCPT TO:<bob@example.test>")
	c.expectReply("503 5.5.1 Bad sequence of commands")
	c.send("DATA")
	c.expectReply("503 5.5.1 Bad sequence of commands")

	// Nested MAIL while a transaction is open is rejected.
	c.send("MAIL FROM:<alice@example.test>")
	c.expectReply("250 2.1.0 Sender OK")
	c.send("MAIL FROM:<alice@example.test>")
	c.expectReply("503 5.5.1 Bad sequence of commands")

	if store.begunCount() != 0 {
		t.Fatalf("store.Begin called %d times without a valid DATA", store.begunCount())
	}
}

// greet2 performs EHLO without consuming the banner (banner already read).
func (c *testClient) greet2() {
	c.t.Helper()
	c.send("EHLO client.test")
	c.expectReply("250-smtpsink.local greets client.test\n250-SIZE 1048576\n250 8BITMIME")
}

func TestRepeatedRcptAcceptedAndDeliveredOnce(t *testing.T) {
	store := &recordingStore{}
	c := newSession(t, store, defaultLimits())
	c.greet()
	c.send("MAIL FROM:<alice@example.test>")
	c.expectReply("250 2.1.0 Sender OK")
	// Same recipient three times: each RCPT is accepted...
	c.send("RCPT TO:<bob@example.test>")
	c.expectReply("250 2.1.5 Recipient OK")
	c.send("RCPT TO:<bob@example.test>")
	c.expectReply("250 2.1.5 Recipient OK")
	c.send("RCPT TO:<bob@example.test>")
	c.expectReply("250 2.1.5 Recipient OK")
	c.send("DATA")
	c.expectReply("354 End data with <CR><LF>.<CR><LF>")
	c.send("hi", ".")
	c.expectReplyPrefix("250 2.0.0 Queued as ")

	msgs := store.messages()
	if len(msgs) != 1 {
		t.Fatalf("want 1 message, got %d", len(msgs))
	}
	// ...and the envelope carries every accepted RCPT; deduplication of
	// the delivery scope happens at the storage layer (see sqlite_test).
	if len(msgs[0].env.RcptTo) != 3 {
		t.Fatalf("envelope rcpts = %v, want 3 accepted entries", msgs[0].env.RcptTo)
	}
}

func TestPartialRecipientAcceptanceScopesDelivery(t *testing.T) {
	store := &recordingStore{}
	c := newSession(t, store, defaultLimits())
	c.greet()
	c.send("MAIL FROM:<alice@example.test>")
	c.expectReply("250 2.1.0 Sender OK")
	c.send("RCPT TO:<bob@example.test>")
	c.expectReply("250 2.1.5 Recipient OK")
	// Foreign domain: rejected, must not enter the delivery scope.
	c.send("RCPT TO:<mallory@outside.example>")
	c.expectReply("550 5.7.1 Relay denied: not a local domain")
	c.send("RCPT TO:<carol@example.test>")
	c.expectReply("250 2.1.5 Recipient OK")
	c.send("DATA")
	c.expectReply("354 End data with <CR><LF>.<CR><LF>")
	c.send("hi", ".")
	c.expectReplyPrefix("250 2.0.0 Queued as ")

	msgs := store.messages()
	if len(msgs) != 1 {
		t.Fatalf("want 1 message, got %d", len(msgs))
	}
	got := strings.Join(msgs[0].env.RcptTo, ",")
	if got != "bob@example.test,carol@example.test" {
		t.Fatalf("delivery scope = %q", got)
	}
}

func TestRsetAbortsTransaction(t *testing.T) {
	store := &recordingStore{}
	c := newSession(t, store, defaultLimits())
	c.greet()
	c.send("MAIL FROM:<alice@example.test>")
	c.expectReply("250 2.1.0 Sender OK")
	c.send("RCPT TO:<bob@example.test>")
	c.expectReply("250 2.1.5 Recipient OK")
	c.send("RSET")
	c.expectReply("250 2.0.0 Reset state")
	// Transaction is gone: DATA without recipients is a sequence error.
	c.send("DATA")
	c.expectReply("503 5.5.1 Bad sequence of commands")
	// A fresh transaction still works.
	c.send("MAIL FROM:<alice@example.test>")
	c.expectReply("250 2.1.0 Sender OK")
	c.send("RCPT TO:<bob@example.test>")
	c.expectReply("250 2.1.5 Recipient OK")
	c.send("DATA")
	c.expectReply("354 End data with <CR><LF>.<CR><LF>")
	c.send("after reset", ".")
	c.expectReplyPrefix("250 2.0.0 Queued as ")

	msgs := store.messages()
	if len(msgs) != 1 || msgs[0].body != "after reset\r\n" {
		t.Fatalf("messages = %+v", msgs)
	}
}

func TestDotLeadingLinesUnstuffed(t *testing.T) {
	store := &recordingStore{}
	c := newSession(t, store, defaultLimits())
	c.greet()
	c.send("MAIL FROM:<alice@example.test>")
	c.expectReply("250 2.1.0 Sender OK")
	c.send("RCPT TO:<bob@example.test>")
	c.expectReply("250 2.1.5 Recipient OK")
	c.send("DATA")
	c.expectReply("354 End data with <CR><LF>.<CR><LF>")
	// Wire form: stuffed dot-leading lines and a lone-dot terminator.
	c.send("..first line starts with a dot", "...second starts with two", "plain", ".")
	c.expectReplyPrefix("250 2.0.0 Queued as ")

	msgs := store.messages()
	if len(msgs) != 1 {
		t.Fatalf("want 1 message, got %d", len(msgs))
	}
	want := ".first line starts with a dot\r\n..second starts with two\r\nplain\r\n"
	if msgs[0].body != want {
		t.Fatalf("body = %q want %q", msgs[0].body, want)
	}
}

func TestConnectionDropMidDataCommitsNothing(t *testing.T) {
	store := &recordingStore{}
	done, conn, rd := newPipeSession(t, store, defaultLimits())
	if banner := readLine(t, rd); !strings.HasPrefix(banner, "220 ") {
		t.Fatalf("banner = %q", banner)
	}
	// net.Pipe is synchronous: interleave every send with its replies.
	sendLines(t, conn, "EHLO client.test")
	for i := 0; i < 3; i++ {
		readLine(t, rd)
	}
	sendLines(t, conn, "MAIL FROM:<alice@example.test>")
	readLine(t, rd)
	sendLines(t, conn, "RCPT TO:<bob@example.test>")
	readLine(t, rd)
	sendLines(t, conn, "DATA")
	if line := readLine(t, rd); !strings.HasPrefix(line, "354 ") {
		t.Fatalf("DATA reply = %q", line)
	}
	// Partial body, then the connection drops without a terminator.
	sendLines(t, conn, "partial body that never finishes")
	conn.Close()

	err := <-done
	if err == nil {
		t.Fatalf("session should end with an error on mid-DATA drop")
	}
	if got := len(store.messages()); got != 0 {
		t.Fatalf("committed %d messages from a dropped DATA", got)
	}
	if store.aborts() != 1 {
		t.Fatalf("pending message was not aborted (aborts=%d)", store.aborts())
	}
}

func TestStorageBeginFailureYields451(t *testing.T) {
	store := &recordingStore{beginErr: errors.New("disk full")}
	c := newSession(t, store, defaultLimits())
	c.greet()
	c.send("MAIL FROM:<alice@example.test>")
	c.expectReply("250 2.1.0 Sender OK")
	c.send("RCPT TO:<bob@example.test>")
	c.expectReply("250 2.1.5 Recipient OK")
	c.send("DATA")
	c.expectReply("451 4.3.0 Local storage error, try again later")
	if got := len(store.messages()); got != 0 {
		t.Fatalf("committed %d messages despite begin failure", got)
	}
}

func TestStorageCommitFailureYields451AndNoAcceptance(t *testing.T) {
	store := &recordingStore{commitErr: errors.New("fsync failed")}
	c := newSession(t, store, defaultLimits())
	c.greet()
	c.send("MAIL FROM:<alice@example.test>")
	c.expectReply("250 2.1.0 Sender OK")
	c.send("RCPT TO:<bob@example.test>")
	c.expectReply("250 2.1.5 Recipient OK")
	c.send("DATA")
	c.expectReply("354 End data with <CR><LF>.<CR><LF>")
	c.send("this will not be accepted", ".")
	c.expectReply("451 4.3.0 Local storage error, message not accepted")
	if got := len(store.messages()); got != 0 {
		t.Fatalf("committed %d messages despite commit failure", got)
	}
}

func TestMessageSizeLimit(t *testing.T) {
	store := &recordingStore{}
	limits := defaultLimits()
	limits.MaxMessageBytes = 16
	c := newSession(t, store, limits)
	c.expectReply("220 smtpsink.local Service ready")
	c.send("EHLO client.test")
	c.expectReply("250-smtpsink.local greets client.test\n250-SIZE 16\n250 8BITMIME")
	c.send("MAIL FROM:<alice@example.test>")
	c.expectReply("250 2.1.0 Sender OK")
	c.send("RCPT TO:<bob@example.test>")
	c.expectReply("250 2.1.5 Recipient OK")
	c.send("DATA")
	c.expectReply("354 End data with <CR><LF>.<CR><LF>")
	c.send("this body is definitely longer than sixteen bytes", ".")
	c.expectReply("552 5.3.4 Message exceeds maximum size of 16 bytes")
	if got := len(store.messages()); got != 0 {
		t.Fatalf("committed %d oversized messages", got)
	}
	// Session stays usable after the rejection.
	c.send("RSET")
	c.expectReply("250 2.0.0 Reset state")
}

func TestRecipientLimit(t *testing.T) {
	store := &recordingStore{}
	limits := defaultLimits()
	limits.MaxRecipients = 2
	c := newSession(t, store, limits)
	c.greet()
	c.send("MAIL FROM:<alice@example.test>")
	c.expectReply("250 2.1.0 Sender OK")
	c.send("RCPT TO:<a@example.test>")
	c.expectReply("250 2.1.5 Recipient OK")
	c.send("RCPT TO:<b@example.test>")
	c.expectReply("250 2.1.5 Recipient OK")
	c.send("RCPT TO:<c@example.test>")
	c.expectReply("452 4.5.3 Too many recipients")
}

func TestLineTooLong(t *testing.T) {
	store := &recordingStore{}
	limits := defaultLimits()
	limits.MaxLineBytes = 32
	c := newSession(t, store, limits)
	c.expectReply("220 smtpsink.local Service ready")
	c.send("EHLO " + strings.Repeat("x", 64))
	c.expectReply("500 5.5.2 Line too long")
	// Stream resynced: a normal command works afterwards.
	c.send("EHLO client.test")
	c.expectReply("250-smtpsink.local greets client.test\n250-SIZE 1048576\n250 8BITMIME")
}

func TestUnknownAndMalformedCommands(t *testing.T) {
	store := &recordingStore{}
	c := newSession(t, store, defaultLimits())
	c.expectReply("220 smtpsink.local Service ready")
	c.send("FROBNICATE")
	c.expectReply("500 5.5.2 Command unrecognized")
	c.send("EHLO client.test")
	c.expectReply("250-smtpsink.local greets client.test\n250-SIZE 1048576\n250 8BITMIME")
	c.send("MAIL alice@example.test")
	c.expectReply("501 5.5.4 Malformed MAIL argument")
	c.send("MAIL FROM:not-an-angle-addr")
	c.expectReply("501 5.5.4 Malformed MAIL argument")
	c.send("MAIL FROM:<nodomain>")
	c.expectReply("501 5.1.7 Malformed sender address")
	c.send("VRFY bob")
	c.expectReply("252 2.1.5 Cannot VRFY user, send some mail and see")
	c.send("NOOP")
	c.expectReply("250 2.0.0 OK")
}

func TestNullReversePathAccepted(t *testing.T) {
	store := &recordingStore{}
	c := newSession(t, store, defaultLimits())
	c.greet()
	c.send("MAIL FROM:<>")
	c.expectReply("250 2.1.0 Sender OK")
	c.send("RCPT TO:<bob@example.test>")
	c.expectReply("250 2.1.5 Recipient OK")
	c.send("DATA")
	c.expectReply("354 End data with <CR><LF>.<CR><LF>")
	c.send("bounce notice", ".")
	c.expectReplyPrefix("250 2.0.0 Queued as ")
	if msgs := store.messages(); len(msgs) != 1 || msgs[0].env.MailFrom != "" {
		t.Fatalf("messages = %+v", msgs)
	}
}
