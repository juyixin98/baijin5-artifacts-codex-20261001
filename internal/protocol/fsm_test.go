package protocol_test

import (
	"context"
	"errors"
	"io"
	"strings"
	"sync"
	"testing"
	"time"

	"smtpsink/internal/protocol"
	"smtpsink/internal/wire"
)

// fakeSink is a test-only Sink written independently of the production store.
// It records what it was asked to persist and can be scripted to fail so the
// state machine's failure classification can be asserted.
type fakeSink struct {
	mu         sync.Mutex
	delivered  []protocol.Message
	failWith   error
	failN      int // fail the next failN calls, then succeed
	alwaysFail bool
	calls      int
}

func (f *fakeSink) Deliver(_ context.Context, m protocol.Message) (protocol.Receipt, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.calls++
	if f.alwaysFail {
		return protocol.Receipt{}, f.failWith
	}
	if f.failN > 0 {
		f.failN--
		return protocol.Receipt{}, f.failWith
	}
	stored := protocol.Message{
		ID: m.ID, From: m.From,
		Recipients: append([]string(nil), m.Recipients...),
		Data:       append([]byte(nil), m.Data...),
		ReceivedAt: m.ReceivedAt,
	}
	f.delivered = append(f.delivered, stored)
	return protocol.Receipt{MessageID: m.ID, DeliveredTo: m.Recipients}, nil
}

func (f *fakeSink) messages() []protocol.Message {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]protocol.Message(nil), f.delivered...)
}

type harness struct {
	t    *testing.T
	sess *protocol.Session
	sink *fakeSink
}

func newHarness(t *testing.T) *harness {
	t.Helper()
	sink := &fakeSink{}
	cfg := protocol.Config{
		Hostname: "sink.local",
		Policy:   protocol.Policy{LocalDomains: []string{"localhost", "sink.local"}},
		Limits: protocol.Limits{
			CommandLineBytes: 512, DataLineBytes: 1000, MessageBytes: 200,
			Recipients: 5, MessagesPerConn: 3, CommandsPerConn: 50,
		},
		Sink:  sink,
		Now:   func() time.Time { return time.Unix(1700000000, 0) },
		NewID: func() string { return "msg-fixed-id" },
	}
	return &harness{t: t, sess: protocol.NewSession(cfg), sink: sink}
}

func (h *harness) feed(line string) protocol.Reply {
	h.t.Helper()
	r := h.sess.Feed(context.Background(), line)
	return r
}

func (h *harness) assertCode(line string, want int) protocol.Reply {
	h.t.Helper()
	r := h.feed(line)
	if r.Code != want {
		h.t.Fatalf("command %q: code = %d, want %d (%v)", line, r.Code, want, r.Text)
	}
	return r
}

func (h *harness) greet() {
	h.t.Helper()
	h.assertCode("EHLO testclient", 250)
}

func dataReader(s string) *wire.Reader {
	return wire.NewReader(strings.NewReader(s), 1000)
}

func TestCommandSequence_OutOfOrderRejected(t *testing.T) {
	h := newHarness(t)

	// Before EHLO everything transactional is refused with 503.
	h.assertCode("MAIL FROM:<a@localhost>", 503)
	h.assertCode("RCPT TO:<b@sink.local>", 503)
	h.assertCode("DATA", 503)

	h.greet()

	// MAIL before RCPT/DATA ordering.
	h.assertCode("RCPT TO:<b@sink.local>", 503)
	h.assertCode("DATA", 503)

	h.assertCode("MAIL FROM:<a@localhost>", 250)
	h.assertCode("MAIL FROM:<a@localhost>", 503) // nested MAIL
	h.assertCode("DATA", 503)                    // no RCPT yet
}

func TestDuplicateRcpt_AcceptedOnce_DeliveredOnce(t *testing.T) {
	h := newHarness(t)
	h.greet()
	h.assertCode("MAIL FROM:<a@localhost>", 250)

	h.assertCode("RCPT TO:<bob@sink.local>", 250)
	dup := h.assertCode("RCPT TO:<bob@sink.local>", 250)
	if !strings.Contains(dup.Text[0], "already listed") {
		t.Fatalf("duplicate reply text = %v, want 'already listed'", dup.Text)
	}
	if rcpts := h.sess.Rcpts(); len(rcpts) != 1 {
		t.Fatalf("unique recipients = %v, want exactly 1", rcpts)
	}

	h.assertCode("DATA", 354)
	dr := dataReader("hello\r\n.\r\n")
	rep := h.sess.HandleData(context.Background(), dr)
	if rep.Code != 250 {
		t.Fatalf("after DATA code = %d %v", rep.Code, rep.Text)
	}
	msgs := h.sink.messages()
	if len(msgs) != 1 {
		t.Fatalf("delivered messages = %d, want 1", len(msgs))
	}
	if len(msgs[0].Recipients) != 1 || msgs[0].Recipients[0] != "bob@sink.local" {
		t.Fatalf("delivered recipients = %v, want exactly [bob@sink.local]", msgs[0].Recipients)
	}
}

func TestMixedRcpts_PartialAcceptanceScopesDelivery(t *testing.T) {
	h := newHarness(t)
	h.greet()
	h.assertCode("MAIL FROM:<a@localhost>", 250)

	// Two local (accepted) + one remote (rejected with 550). Delivery scope
	// must be exactly the two accepted mailboxes.
	h.assertCode("RCPT TO:<one@sink.local>", 250)
	h.assertCode("RCPT TO:<two@localhost>", 250)
	h.assertCode("RCPT TO:<evil@external.example>", 550)
	h.assertCode("RCPT TO:<bad-address>", 501)

	h.assertCode("DATA", 354)
	rep := h.sess.HandleData(context.Background(), dataReader("x\r\n.\r\n"))
	if rep.Code != 250 {
		t.Fatalf("DATA final = %d %v", rep.Code, rep.Text)
	}
	msgs := h.sink.messages()
	if len(msgs) != 1 {
		t.Fatalf("messages = %d", len(msgs))
	}
	got := strings.Join(msgs[0].Recipients, ",")
	if got != "one@sink.local,two@localhost" {
		t.Fatalf("delivery scope = %q, want only accepted local recipients", got)
	}
}

func TestDotTransparency_BodyPreservedByteForByte(t *testing.T) {
	h := newHarness(t)
	h.greet()
	h.assertCode("MAIL FROM:<>", 250) // bounce empty path accepted
	h.assertCode("RCPT TO:<m@sink.local>", 250)
	h.assertCode("DATA", 354)

	raw := "Subject: hi\r\n" +
		".starts-with-dot\r\n" +
		"..\r\n" +
		"normal\r\n" +
		".\r\n"
	rep := h.sess.HandleData(context.Background(), dataReader(raw))
	if rep.Code != 250 {
		t.Fatalf("code = %d %v", rep.Code, rep.Text)
	}
	msgs := h.sink.messages()
	wantBody := "Subject: hi\r\nstarts-with-dot\r\n.\r\nnormal\r\n"
	if string(msgs[0].Data) != wantBody {
		t.Fatalf("stored body = %q, want %q", msgs[0].Data, wantBody)
	}
	if msgs[0].From != "" {
		t.Fatalf("bounce sender stored as %q, want empty", msgs[0].From)
	}
}

func TestRset_ClearsTransaction_KeepsGreeting(t *testing.T) {
	h := newHarness(t)
	h.greet()
	h.assertCode("MAIL FROM:<a@localhost>", 250)
	h.assertCode("RCPT TO:<b@sink.local>", 250)
	h.assertCode("RSET", 250)

	if h.sess.Phase() != protocol.PhaseReady {
		t.Fatalf("phase after RSET = %v, want ready", h.sess.Phase())
	}
	if len(h.sess.Rcpts()) != 0 || h.sess.From() != "" {
		t.Fatalf("transaction not cleared: from=%q rcpts=%v", h.sess.From(), h.sess.Rcpts())
	}
	// RSET before greeting returns to init and MAIL still requires EHLO.
	h2 := newHarness(t)
	h2.assertCode("RSET", 250)
	h2.assertCode("MAIL FROM:<a@localhost>", 503)

	// A fresh transaction after RSET works and does not inherit old state.
	h.assertCode("MAIL FROM:<c@localhost>", 250)
	h.assertCode("RCPT TO:<d@sink.local>", 250)
	h.assertCode("DATA", 354)
	rep := h.sess.HandleData(context.Background(), dataReader("y\r\n.\r\n"))
	if rep.Code != 250 {
		t.Fatalf("post-RSET delivery code = %d", rep.Code)
	}
	if msgs := h.sink.messages(); len(msgs) != 1 || msgs[0].Recipients[0] != "d@sink.local" {
		t.Fatalf("post-RSET delivery wrong: %+v", msgs)
	}
}

func TestDeliveryFailure_NoSuccessBeforeDurability(t *testing.T) {
	h := newHarness(t)
	sink := h.sink
	sink.failWith = protocol.ErrTemporary
	sink.alwaysFail = true

	h.greet()
	h.assertCode("MAIL FROM:<a@localhost>", 250)
	h.assertCode("RCPT TO:<b@sink.local>", 250)
	h.assertCode("DATA", 354)
	rep := h.sess.HandleData(context.Background(), dataReader("body\r\n.\r\n"))
	if rep.Code != 451 {
		t.Fatalf("on temporary sink failure code = %d, want 451", rep.Code)
	}
	if n := len(sink.messages()); n != 0 {
		t.Fatalf("%d messages visible after failed delivery, want 0", n)
	}
	// Transaction is reset: client must restart with MAIL.
	if h.sess.Phase() != protocol.PhaseReady {
		t.Fatalf("phase after failed delivery = %v, want ready", h.sess.Phase())
	}
	h.assertCode("RCPT TO:<b@sink.local>", 503)
}

func TestInsufficientStorage_MapsTo452(t *testing.T) {
	h := newHarness(t)
	h.sink.failWith = protocol.ErrInsufficient
	h.sink.alwaysFail = true
	h.greet()
	h.assertCode("MAIL FROM:<a@localhost>", 250)
	h.assertCode("RCPT TO:<b@sink.local>", 250)
	h.assertCode("DATA", 354)
	rep := h.sess.HandleData(context.Background(), dataReader("z\r\n.\r\n"))
	if rep.Code != 452 {
		t.Fatalf("quota failure code = %d, want 452", rep.Code)
	}
}

func TestUnclassifiedSinkError_IsTemporaryAndSafe(t *testing.T) {
	h := newHarness(t)
	h.sink.failWith = errors.New("sqlite: secret disk path /etc/priv")
	h.sink.alwaysFail = true
	h.greet()
	h.assertCode("MAIL FROM:<a@localhost>", 250)
	h.assertCode("RCPT TO:<b@sink.local>", 250)
	h.assertCode("DATA", 354)
	rep := h.sess.HandleData(context.Background(), dataReader("z\r\n.\r\n"))
	if rep.Code != 451 {
		t.Fatalf("code = %d, want 451", rep.Code)
	}
	for _, line := range rep.Text {
		if strings.Contains(line, "secret") || strings.Contains(line, "/etc/") {
			t.Fatalf("internal error leaked to peer: %q", line)
		}
	}
}

func TestClientAbortDuringData_NoDelivery(t *testing.T) {
	h := newHarness(t)
	h.greet()
	h.assertCode("MAIL FROM:<a@localhost>", 250)
	h.assertCode("RCPT TO:<b@sink.local>", 250)
	h.assertCode("DATA", 354)

	// Peer disconnects after sending partial content and never sends the dot.
	rep := h.sess.HandleData(context.Background(), wire.NewReader(strings.NewReader("partial body\r\n"), 1000))
	if !rep.Silent || !rep.Close {
		t.Fatalf("abort reply = %+v, want silent close", rep)
	}
	if n := len(h.sink.messages()); n != 0 {
		t.Fatalf("delivered %d messages after abort, want 0", n)
	}
}

func TestOverlongDataLine_FailsTransactionWithoutDelivery(t *testing.T) {
	h := newHarness(t)
	h.greet()
	h.assertCode("MAIL FROM:<a@localhost>", 250)
	h.assertCode("RCPT TO:<b@sink.local>", 250)
	h.assertCode("DATA", 354)

	big := strings.Repeat("q", 1500) + "\r\n.\r\n"
	rd := wire.NewReader(strings.NewReader(big), 512)
	rep := h.sess.HandleData(context.Background(), rd)
	if rep.Code != 554 {
		t.Fatalf("over-long DATA line code = %d, want 554", rep.Code)
	}
	if n := len(h.sink.messages()); n != 0 {
		t.Fatalf("delivered after over-long line: %d", n)
	}
}

func TestMessageSizeCap_RejectsWith552(t *testing.T) {
	h := newHarness(t) // MessageBytes = 200
	h.greet()
	h.assertCode("MAIL FROM:<a@localhost>", 250)
	h.assertCode("RCPT TO:<b@sink.local>", 250)
	h.assertCode("DATA", 354)
	body := strings.Repeat("a", 201) + "\r\n.\r\n"
	rep := h.sess.HandleData(context.Background(), dataReader(body))
	if rep.Code != 552 {
		t.Fatalf("oversize code = %d, want 552", rep.Code)
	}
	if n := len(h.sink.messages()); n != 0 {
		t.Fatalf("oversize message stored: %d", n)
	}
}

func TestDeclaredSIZE_RejectedUpfront(t *testing.T) {
	h := newHarness(t)
	h.greet()
	h.assertCode("MAIL FROM:<a@localhost> SIZE=999999", 552)
	if h.sess.Phase() != protocol.PhaseReady {
		t.Fatalf("phase = %v, want ready after SIZE reject", h.sess.Phase())
	}
}

func TestSessionBudgets_MessagesAndCommands(t *testing.T) {
	h := newHarness(t) // 3 messages/conn, 50 commands/conn
	h.greet()

	deliver := func(n int) {
		h.assertCode("MAIL FROM:<a@localhost>", 250)
		h.assertCode("RCPT TO:<b@sink.local>", 250)
		h.assertCode("DATA", 354)
		body := strings.Repeat("m", n) + "\r\n.\r\n"
		rep := h.sess.HandleData(context.Background(), dataReader(body))
		if rep.Code != 250 {
			t.Fatalf("delivery code = %d %v", rep.Code, rep.Text)
		}
	}
	deliver(1)
	deliver(2)
	deliver(3)
	// Fourth MAIL triggers the per-connection message cap -> 421 close.
	rep := h.feed("MAIL FROM:<a@localhost>")
	if rep.Code != 421 || !rep.Close {
		t.Fatalf("message cap reply = %+v, want 421 close", rep)
	}
}

func TestUnsupportedCommands(t *testing.T) {
	h := newHarness(t)
	h.greet()
	h.assertCode("VRFY root", 502)
	h.assertCode("STARTTLS", 502)
	h.assertCode("NOOP", 250)
	quit := h.assertCode("QUIT", 221)
	if !quit.Close {
		t.Fatal("QUIT must close")
	}
}

func TestGreetingAndBareLine(t *testing.T) {
	h := newHarness(t)
	g := h.sess.Greeting()
	if g.Code != 220 {
		t.Fatalf("greeting code = %d", g.Code)
	}
	if r := h.feed(""); r.Code != 250 {
		t.Fatalf("empty command code = %d, want 250", r.Code)
	}
	// EHLO advertises SIZE.
	r := h.feed("EHLO me")
	found := false
	for _, line := range r.Text {
		if strings.HasPrefix(line, "SIZE ") {
			found = true
		}
	}
	if !found {
		t.Fatalf("EHLO did not advertise SIZE: %v", r.Text)
	}
}

func TestDataEOFAfterDotNotDoubleDelivered(t *testing.T) {
	h := newHarness(t)
	h.greet()
	h.assertCode("MAIL FROM:<a@localhost>", 250)
	h.assertCode("RCPT TO:<b@sink.local>", 250)
	h.assertCode("DATA", 354)
	// Terminator split across the last bytes of the stream.
	rep := h.sess.HandleData(context.Background(),
		wire.NewReader(&slowTailReader{head: "body\r\n.", tail: "\r\n"}, 1000))
	if rep.Code != 250 {
		t.Fatalf("split-terminator code = %d %v", rep.Code, rep.Text)
	}
	if n := len(h.sink.messages()); n != 1 {
		t.Fatalf("messages = %d, want 1", n)
	}
	if _, err := io.ReadAll(strings.NewReader("")); err != nil {
		t.Fatal(err)
	}
}

// slowTailReader returns head fully then tail on the next read.
type slowTailReader struct {
	head string
	tail string
	done bool
}

func (s *slowTailReader) Read(p []byte) (int, error) {
	if !s.done {
		s.done = true
		return copy(p, s.head), nil
	}
	if s.tail == "" {
		return 0, io.EOF
	}
	n := copy(p, s.tail)
	s.tail = s.tail[n:]
	return n, nil
}
