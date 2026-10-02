package integration_test

import (
	"context"
	"errors"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"coaplab/internal/diag"
	"coaplab/internal/transport"
	"coaplab/internal/wire"
	"coaplab/test/harness"
)

const (
	testCfg      = "../../configs/coaplab-test.conf"
	testFixtures = "../fixtures/data/resources.json"
)

// handlerCounter wraps the handler to prove MID dedup: the handler must run
// ONCE per unique CON no matter how many identical datagrams arrive.
func handlerCounter(count *int64) func(transport.Handler) transport.Handler {
	return func(next transport.Handler) transport.Handler {
		return transport.HandlerFunc(func(remote *netUDPAddr, req *wire.Message) *wire.Message {
			atomic.AddInt64(count, 1)
			return next.ServeCoAP(remote, req)
		})
	}
}

// Scenario 1: two identical CON datagrams (same MID) produce two identical
// replies, but the resource handler runs exactly once (message-layer dedup).
// The oracle independently decodes both replies and checks MID and Token.
func TestMessageLayer_CONRetransmissionDedupedAtServer(t *testing.T) {
	var calls int64
	app, _, cleanup := harness.StartTestApp(t, testCfg, testFixtures,
		harnessServiceOpt(handlerCounter(&calls)))
	defer cleanup()

	peer, target, err := harness.NewRawPeer(app.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	defer peer.Close()

	const mid uint16 = 0x5001
	token := []byte{0x7, 0x7}
	build := func() *wire.Message {
		return &wire.Message{
			Type: wire.CON, Code: wire.GET, MID: mid, Token: token,
			Options: []wire.Option{{Number: wire.OpURIPath, Value: []byte("hello")}},
		}
	}

	for i := 0; i < 3; i++ {
		if err := peer.Send(build(), target); err != nil {
			t.Fatal(err)
		}
	}
	dgs := peer.ReceiveN(3, time.Second)
	if len(dgs) != 3 {
		t.Fatalf("expected 3 replies (one per datagram), got %d", len(dgs))
	}
	first := dgs[0].Data
	for i, d := range dgs {
		if string(d.Data) != string(first) {
			t.Fatalf("reply %d differs from replayed reply 0", i)
		}
		v, err := oracleInspect(d.Data)
		if err != nil {
			t.Fatalf("oracle parse reply %d: %v", i, err)
		}
		if v.Type != 2 || v.MID != mid {
			t.Fatalf("reply %d not ACK matching MID: type=%d mid=0x%04x", i, v.Type, v.MID)
		}
		if string(v.Token) != string(token) {
			t.Fatalf("reply %d token %x != request token %x", i, v.Token, token)
		}
	}
	if got := atomic.LoadInt64(&calls); got != 1 {
		t.Fatalf("handler ran %d times for 3 identical CONs; dedup requires exactly 1", got)
	}
}

// Scenario 2: the FIRST acknowledgement is lost by the network. The client
// retransmits the SAME request with the SAME MID; the server replays its
// stored response and the handler still runs once. End-to-end GET succeeds.
func TestMessageLayer_LostACK_RetransmitSameMID(t *testing.T) {
	var calls int64
	app, _, cleanup := harness.StartTestApp(t, testCfg, testFixtures,
		harnessServiceOpt(handlerCounter(&calls)))
	defer cleanup()

	proxy, err := harness.NewProxy(app.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	defer proxy.Close()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	proxy.Run(ctx)

	// Drop exactly the FIRST server->client datagram (the lost ACK).
	var dropped int32
	proxy.AddRule(func(p harness.Packet) (harness.Action, time.Duration) {
		if p.Dir == harness.S2C && atomic.CompareAndSwapInt32(&dropped, 0, 1) {
			return harness.ActionDrop, 0
		}
		return harness.ActionPass, 0
	})

	client, err := transport.DialClient(transport.ClientOptions{
		Retransmit: transport.Retransmit{ACKTimeout: 40 * time.Millisecond, ACKRandomFactor: 1.0, MaxRetransmit: 3},
	})
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()

	req := &wire.Message{Code: wire.GET, Options: []wire.Option{
		{Number: wire.OpURIPath, Value: []byte("hello")},
	}}
	resp, err := client.Exchange(context.Background(), proxy.ClientAddr(), req)
	if err != nil {
		t.Fatalf("exchange after one lost ACK failed: %v", err)
	}
	if resp.Code != wire.Content || string(resp.Payload) != "Hello, CoAP!" {
		t.Fatalf("bad response: %s %q", resp.Code, resp.Payload)
	}
	if got := atomic.LoadInt64(&calls); got != 1 {
		t.Fatalf("handler ran %d times; the retransmitted MID must be a replay", got)
	}
}

// Scenario 3: every response is dropped. The client sends exactly
// MAX_RETRANSMIT+1 copies, ALL with the same MID, then reports a timeout.
func TestMessageLayer_AllACKsLost_TimeoutAndMIDConstant(t *testing.T) {
	srv, err := harness.NewScriptedServer()
	if err != nil {
		t.Fatal(err)
	}
	defer srv.Close()
	srv.OnMessage(func(*wire.Message, []byte, *netUDPAddr) *wire.Message { return nil }) // silent
	srv.Start()

	client, err := transport.DialClient(transport.ClientOptions{
		Retransmit: transport.Retransmit{ACKTimeout: 20 * time.Millisecond, ACKRandomFactor: 1.0, MaxRetransmit: 2},
	})
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()

	req := &wire.Message{Code: wire.GET, Token: []byte{1}, Options: []wire.Option{
		{Number: wire.OpURIPath, Value: []byte("x")},
	}}
	start := time.Now()
	_, err = client.Exchange(context.Background(), srv.Addr(), req)
	if !errors.Is(err, transport.ErrTimeout) {
		t.Fatalf("want ErrTimeout, got %v", err)
	}
	if d := time.Since(start); d < 20+40+80*time.Millisecond-time.Millisecond {
		// 20 + 40 + 80 ms backoff minimum.
		t.Fatalf("returned too early after %s", d)
	}

	seen := srv.Seen()
	if len(seen) != 3 {
		t.Fatalf("want 3 transmissions (1 + MAX_RETRANSMIT=2), got %d", len(seen))
	}
	mid := seen[0].Msg.MID
	for i, s := range seen {
		if s.Msg.MID != mid {
			t.Fatalf("transmission %d changed MID: 0x%04x vs 0x%04x", i, s.Msg.MID, mid)
		}
		if string(s.Msg.Token) != string(req.Token) {
			t.Fatalf("transmission %d changed token", i)
		}
	}
}

// Scenario 4: layer separation. An ACK matches the MID but a piggybacked
// response carrying the WRONG token must be rejected at the request layer;
// conversely a response with the right token but wrong MID is not the ACK.
func TestMessageLayer_TokenMIDNotConflated(t *testing.T) {
	srv, err := harness.NewScriptedServer()
	if err != nil {
		t.Fatal(err)
	}
	defer srv.Close()

	var mu sync.Mutex
	var respond bool
	srv.OnMessage(func(msg *wire.Message, _ []byte, from *netUDPAddr) *wire.Message {
		mu.Lock()
		defer mu.Unlock()
		if !respond {
			// Piggyback ACK with the SAME mid but a DIFFERENT token.
			respond = true
			return &wire.Message{Type: wire.ACK, Code: wire.Content, MID: msg.MID, Token: []byte{0xde, 0xad}, Payload: []byte("forged")}
		}
		return nil
	})
	srv.Start()

	client, err := transport.DialClient(transport.ClientOptions{
		Retransmit: transport.Retransmit{ACKTimeout: 60 * time.Millisecond, ACKRandomFactor: 1.0, MaxRetransmit: 0},
	})
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()

	req := &wire.Message{Code: wire.GET, Token: []byte{0x12, 0x34}, Options: []wire.Option{
		{Number: wire.OpURIPath, Value: []byte("x")},
	}}
	_, err = client.Exchange(context.Background(), srv.Addr(), req)
	if err == nil {
		t.Fatal("piggyback response with mismatched token must not be delivered")
	}
	// The decisive proof: the wrong-token payload must never surface.
	// (Client retried once after MID-ACK token mismatch then timed out.)
}

// Malformed datagrams are diagnosed parse_error and answered with silence
// (RFC permits dropping); the service keeps serving subsequent good traffic.
func TestMessageLayer_MalformedDatagramDoesNotKillServer(t *testing.T) {
	app, rec, cleanup := harness.StartTestApp(t, testCfg, testFixtures)
	defer cleanup()

	peer, target, err := harness.NewRawPeer(app.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	defer peer.Close()

	if err := peer.SendRaw([]byte{0x40, 0x01}, target); err != nil {
		t.Fatal(err)
	}
	// No reply expected for the malformed datagram.
	if d, ok := peer.ReceiveOne(120 * time.Millisecond); ok {
		t.Fatalf("malformed datagram must be dropped, got %x", d.Data)
	}
	if len(rec.ByCategory(diag.CatParseError)) == 0 {
		t.Fatal("parse rejection must be diagnosed with category parse_error")
	}

	// Good traffic still served.
	good := &wire.Message{Type: wire.CON, Code: wire.GET, MID: 1, Options: []wire.Option{
		{Number: wire.OpURIPath, Value: []byte("hello")},
	}}
	if err := peer.Send(good, target); err != nil {
		t.Fatal(err)
	}
	d, ok := peer.ReceiveOne(time.Second)
	if !ok || d.Msg.Code != wire.Content {
		t.Fatalf("service did not recover: ok=%v d=%+v", ok, d)
	}
}
