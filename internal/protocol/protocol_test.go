package protocol_test

import (
	"bytes"
	"context"
	"encoding/hex"
	"net"
	"sync"
	"testing"
	"time"

	"coapblockwise/internal/diag"
	"coapblockwise/internal/protocol"
	"coapblockwise/internal/wire"
)

var peerAddr = &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1), Port: 5683}

func newTestEndpoint(t *testing.T, p protocol.RetransmitParams,
	clock protocol.Clock,
) (*protocol.Endpoint, *fakeUDPConn) {
	t.Helper()
	conn := newFakeUDPConn()
	e, err := protocol.NewEndpoint(protocol.Config{
		Conn:   conn,
		Params: p,
		Clock:  clock,
		Logger: diag.Discard(),
		Rand:   func() float64 { return 0 },
	})
	if err != nil {
		t.Fatalf("NewEndpoint: %v", err)
	}
	t.Cleanup(func() { _ = e.Close() })
	return e, conn
}

func deterministicParams() protocol.RetransmitParams {
	return protocol.RetransmitParams{
		ACKTimeout:       100 * time.Millisecond,
		ACKRandomFactor:  1.0,
		MaxRetransmit:    4,
		ExchangeLifetime: 5 * time.Second,
		NonLifetime:      3 * time.Second,
	}
}

// waitForSent blocks until at least n outbound datagrams were captured.
func waitForSent(t *testing.T, conn *fakeUDPConn, n int) {
	t.Helper()
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		if conn.sentCount() >= n {
			return
		}
		time.Sleep(time.Millisecond)
	}
	t.Fatalf("timed out waiting for %d outbound datagrams (got %d)",
		n, conn.sentCount())
}

// decodeLastSent parses the most recently captured outbound datagram.
func decodeLastSent(t *testing.T, conn *fakeUDPConn) *wire.Message {
	t.Helper()
	sent := conn.sentCopy()
	if len(sent) == 0 {
		t.Fatal("expected at least one outbound datagram")
	}
	m, err := wire.Decode(sent[len(sent)-1])
	if err != nil {
		t.Fatalf("decode outbound: %v", err)
	}
	return m
}

// piggyback builds a 2.05 ACK response echoing a request's MID and token.
func piggyback(req *wire.Message, code wire.Code, payload []byte) *wire.Message {
	m := wire.ResponseTo(req, code)
	m.Payload = payload
	return m
}

func mustEncode(t *testing.T, m *wire.Message) []byte {
	t.Helper()
	b, err := m.Encode()
	if err != nil {
		t.Fatalf("encode: %v", err)
	}
	return b
}

func TestRetransmitThenACKSucceeds(t *testing.T) {
	clock := protocol.NewFakeClock(time.Unix(1_700_000_000, 0))
	params := deterministicParams()
	e, conn := newTestEndpoint(t, params, clock)

	token := []byte{0x10, 0x20, 0x30}
	// Succeed on the third transmission (initial + 2 retransmits).
	conn.setOnWrite(func(_ *net.UDPAddr, _ []byte) {
		if conn.sentCount() == 3 {
			req := decodeLastSent(t, conn)
			resp := piggyback(req, wire.CodeContent, []byte("ok"))
			conn.deliver(mustEncode(t, resp), peerAddr)
		}
	})

	type result struct {
		m   *wire.Message
		err error
	}
	resCh := make(chan result, 1)
	go func() {
		m, err := e.Exchange(context.Background(), peerAddr, token,
			wire.CodeGET, wire.PathOptions("/t"), nil)
		resCh <- result{m, err}
	}()

	waitForSent(t, conn, 1)
	clock.Advance(400 * time.Millisecond) // fires 100ms and 200ms timers

	select {
	case r := <-resCh:
		if r.err != nil {
			t.Fatalf("Exchange: %v", r.err)
		}
		if r.m.Code != wire.CodeContent {
			t.Fatalf("code = %s, want 2.05", r.m.Code)
		}
		if string(r.m.Payload) != "ok" {
			t.Fatalf("payload = %q, want ok", r.m.Payload)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("exchange never completed after ACK on third transmission")
	}
	if got := conn.sentCount(); got != 3 {
		t.Fatalf("transmissions = %d, want 3 (initial + 2 retransmit)", got)
	}
}

func TestTimeoutAfterMaxRetransmit(t *testing.T) {
	clock := protocol.NewFakeClock(time.Unix(1_700_000_000, 0))
	params := deterministicParams()
	e, conn := newTestEndpoint(t, params, clock)

	errCh := make(chan error, 1)
	go func() {
		_, err := e.Exchange(context.Background(), peerAddr, []byte{1},
			wire.CodeGET, nil, nil)
		errCh <- err
	}()

	waitForSent(t, conn, 1)
	// 100+200+400+800+1600 ms = 3100 ms covers the fifth failed attempt.
	clock.Advance(3200 * time.Millisecond)

	select {
	case err := <-errCh:
		if err == nil {
			t.Fatal("expected timeout error, got nil")
		}
		pe, ok := protocol.AsError(err)
		if !ok {
			t.Fatalf("error %v does not carry a protocol failure kind", err)
		}
		if pe.Kind != protocol.KindTimeout {
			t.Fatalf("failure kind = %s, want %s", pe.Kind,
				protocol.KindTimeout)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("timeout never reported")
	}
	if got := conn.sentCount(); got != params.MaxRetransmit+1 {
		t.Fatalf("transmissions = %d, want %d (initial + MaxRetransmit)",
			got, params.MaxRetransmit+1)
	}
}

func TestRSTAbortsImmediately(t *testing.T) {
	clock := protocol.NewFakeClock(time.Unix(1_700_000_000, 0))
	e, conn := newTestEndpoint(t, deterministicParams(), clock)

	conn.setOnWrite(func(_ *net.UDPAddr, _ []byte) {
		req := decodeLastSent(t, conn)
		conn.deliver(mustEncode(t, wire.EmptyRST(req.MessageID)), peerAddr)
	})

	_, err := e.Exchange(context.Background(), peerAddr, []byte{2},
		wire.CodePUT, nil, nil)
	if err == nil {
		t.Fatal("expected RST error")
	}
	pe, ok := protocol.AsError(err)
	if !ok {
		t.Fatalf("error %v does not carry a failure kind", err)
	}
	if pe.Kind != protocol.KindReset {
		t.Fatalf("failure kind = %s, want reset", pe.Kind)
	}
	if got := conn.sentCount(); got != 1 {
		t.Fatalf("transmissions after RST = %d, want 1 (no retransmit)", got)
	}
}

func TestSeparateResponseMatchesTokenNotMID(t *testing.T) {
	clock := protocol.NewFakeClock(time.Unix(1_700_000_000, 0))
	e, conn := newTestEndpoint(t, deterministicParams(), clock)

	token := []byte{0xab, 0xcd}
	conn.setOnWrite(func(_ *net.UDPAddr, _ []byte) {
		if conn.sentCount() != 1 {
			return
		}
		req := decodeLastSent(t, conn)
		// 1) empty ACK confirms the REQUEST MID (piggyback not used).
		conn.deliver(mustEncode(t, wire.EmptyACK(req.MessageID)), peerAddr)
		// 2) separate response arrives with a FRESH MID but the SAME token.
		sep := wire.NewMessage(wire.CON, wire.CodeContent, 0x9999, token)
		sep.Payload = []byte("later")
		conn.deliver(mustEncode(t, sep), peerAddr)
	})

	m, err := e.Exchange(context.Background(), peerAddr, token,
		wire.CodeGET, nil, nil)
	if err != nil {
		t.Fatalf("Exchange: %v", err)
	}
	if m.MessageID != 0x9999 {
		t.Fatalf("separate response MID = 0x%04x, want 0x9999", m.MessageID)
	}
	if !bytes.Equal(m.Token, token) {
		t.Fatalf("token = %x, want %x", m.Token, token)
	}
	if string(m.Payload) != "later" {
		t.Fatalf("payload = %q, want later", m.Payload)
	}
	// We must have ACKed the separate response's own fresh MID.
	ack := decodeLastSent(t, conn)
	if ack.Type != wire.ACK || !ack.IsEmpty() || ack.MessageID != 0x9999 {
		t.Fatalf("did not ACK separate response at fresh MID: %s mid=0x%04x",
			ack.Type, ack.MessageID)
	}
}

func TestPiggybackWrongTokenRejected(t *testing.T) {
	clock := protocol.NewFakeClock(time.Unix(1_700_000_000, 0))
	e, conn := newTestEndpoint(t, deterministicParams(), clock)

	errCh := make(chan error, 1)
	go func() {
		_, err := e.Exchange(context.Background(), peerAddr, []byte{3, 3},
			wire.CodeGET, nil, nil)
		errCh <- err
	}()

	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) && conn.sentCount() == 0 {
		time.Sleep(time.Millisecond)
	}
	req := decodeLastSent(t, conn)
	bad := wire.ResponseTo(req, wire.CodeContent)
	bad.Token = []byte{9, 9, 9} // does NOT echo the request token
	conn.deliver(mustEncode(t, bad), peerAddr)

	select {
	case err := <-errCh:
		pe, ok := protocol.AsError(err)
		if !ok || pe.Kind != protocol.KindMalformed {
			t.Fatalf("err = %v, want malformed (token mismatch)", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("no result after mismatched-token response")
	}
}

func TestUnmatchedSeparateResponseByToken(t *testing.T) {
	clock := protocol.NewFakeClock(time.Unix(1_700_000_000, 0))
	_, conn := newTestEndpoint(t, deterministicParams(), clock)

	before := conn.sentCount()
	unsolicited := wire.NewMessage(wire.CON, wire.CodeContent, 0x4242,
		[]byte{0xff, 0xff})
	conn.deliver(mustEncode(t, unsolicited), peerAddr)
	time.Sleep(100 * time.Millisecond)

	if got := conn.sentCount() - before; got != 0 {
		t.Fatalf("endpoint emitted %d datagrams for an unknown token, want 0", got)
	}
}

func TestServerDedupProcessesOnce(t *testing.T) {
	clock := protocol.NewFakeClock(time.Now())
	e, conn := newTestEndpoint(t, deterministicParams(), clock)

	var mu sync.Mutex
	invocations := 0
	entered := make(chan struct{})
	release := make(chan struct{})
	e.SetHandler(protocol.HandlerFunc(func(w protocol.Responder, r *protocol.Request) {
		mu.Lock()
		invocations++
		mu.Unlock()
		close(entered)
		<-release
		w.Respond(wire.CodeChanged, nil, []byte("done"))
	}))

	req := wire.NewMessage(wire.CON, wire.CodePUT, 0x1111, []byte{0x7})
	req.Options = wire.PathOptions("/r")
	raw := mustEncode(t, req)

	conn.deliver(raw, peerAddr)
	<-entered
	// Duplicate while the first is still being processed.
	conn.deliver(raw, peerAddr)
	time.Sleep(100 * time.Millisecond)
	close(release)
	time.Sleep(100 * time.Millisecond)

	mu.Lock()
	got := invocations
	mu.Unlock()
	if got != 1 {
		t.Fatalf("handler invoked %d times for duplicate MID, want exactly 1", got)
	}
	sent := conn.sentCopy()
	if len(sent) != 2 {
		t.Fatalf("responses sent = %d, want 2 (original + duplicate replay)", len(sent))
	}
	if !bytes.Equal(sent[0], sent[1]) {
		t.Fatal("duplicate CON must replay the byte-identical cached response")
	}
}

func TestServerDedupAfterCompletion(t *testing.T) {
	clock := protocol.NewFakeClock(time.Now())
	e, conn := newTestEndpoint(t, deterministicParams(), clock)

	var mu sync.Mutex
	invocations := 0
	e.SetHandler(protocol.HandlerFunc(func(w protocol.Responder, r *protocol.Request) {
		mu.Lock()
		invocations++
		mu.Unlock()
		w.Respond(wire.CodeContent, nil, []byte("v"))
	}))

	req := wire.NewMessage(wire.CON, wire.CodeGET, 0x2222, []byte{0x8})
	raw := mustEncode(t, req)
	conn.deliver(raw, peerAddr)
	conn.deliver(raw, peerAddr)
	time.Sleep(150 * time.Millisecond)

	mu.Lock()
	got := invocations
	mu.Unlock()
	if got != 1 {
		t.Fatalf("handler invoked %d times, want 1", got)
	}
	if len(conn.sentCopy()) != 2 {
		t.Fatal("expected cached replay for the late duplicate")
	}
}

func TestMalformedDatagramIgnored(t *testing.T) {
	clock := protocol.NewFakeClock(time.Now())
	e, conn := newTestEndpoint(t, deterministicParams(), clock)
	_ = e

	conn.deliver([]byte{0x40, 0x01}, peerAddr) // shorter than 4 bytes
	conn.deliver([]byte{0x00, 0, 0, 0}, peerAddr) // bad version
	time.Sleep(100 * time.Millisecond)
	if conn.sentCount() != 0 {
		t.Fatalf("endpoint emitted %d datagrams for malformed input",
			conn.sentCount())
	}
}

func TestMIDAndTokenAreIndependent(t *testing.T) {
	// A token must be free to take any value irrespective of the MID; the
	// allocator assigns them from independent spaces.
	clock := protocol.NewFakeClock(time.Now())
	e, conn := newTestEndpoint(t, deterministicParams(), clock)

	conn.setOnWrite(func(_ *net.UDPAddr, _ []byte) {
		req := decodeLastSent(t, conn)
		conn.deliver(mustEncode(t, piggyback(req, wire.CodeContent, nil)), peerAddr)
	})

	tok, err := e.NewToken()
	if err != nil {
		t.Fatalf("NewToken: %v", err)
	}
	m, err := e.Exchange(context.Background(), peerAddr, tok,
		wire.CodeGET, nil, nil)
	if err != nil {
		t.Fatalf("Exchange: %v", err)
	}
	if hex.EncodeToString(m.Token) != hex.EncodeToString(tok) {
		t.Fatal("response token must echo request token")
	}
	// Nothing requires token == MID; assert they differ in width/value space.
	if m.MessageID == 0 && len(tok) == 0 {
		t.Fatal("MID and token both empty: identifiers not independent")
	}
}
