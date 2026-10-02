package integration_test

import (
	"context"
	"sync/atomic"
	"testing"
	"time"

	"coaplab/internal/engine"
	"coaplab/internal/service"
	"coaplab/internal/transport"
	"coaplab/internal/wire"
	"coaplab/test/harness"
	"coaplab/test/oracle"
)

// putBlock sends one raw Block1 PUT and returns the oracle-decoded response.
func putBlock(t *testing.T, peer *harness.RawPeer, to *netUDPAddr,
	path string, mid uint16, b wire.Block, payload []byte) oracle.MessageView {
	t.Helper()
	req := &wire.Message{
		Type: wire.CON, Code: wire.PUT, MID: mid, Token: []byte{byte(mid), byte(mid >> 8)},
		Options: []wire.Option{
			{Number: wire.OpURIPath, Value: []byte(path)},
			{Number: wire.OpBlock1, Value: b.Encode()},
		},
		Payload: payload,
	}
	if err := peer.Send(req, to); err != nil {
		t.Fatal(err)
	}
	d, ok := peer.ReceiveOne(2 * time.Second)
	if !ok || d.Msg == nil {
		t.Fatalf("MID 0x%04x: no response", mid)
	}
	v, err := oracleInspect(d.Data)
	if err != nil {
		t.Fatal(err)
	}
	return v
}

// RFC 7959 Figure 7 — Simple Atomic Block-Wise PUT at 128 bytes (SZX=3).
// Exact wire descriptors: 1:0/1/128 -> 2.31; 1:1/1/128 -> 2.31;
// 1:2/0/128 -> 2.04. Final stored bytes equal the assembled 300 bytes.
func TestRFC7959_Figure7_AtomicPut128(t *testing.T) {
	app, _, cleanup := harness.StartTestApp(t, testCfg, testFixtures,
		service.WithPreferredSZX(3))
	defer cleanup()

	peer, target, err := harness.NewRawPeer(app.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	defer peer.Close()

	body := oracle.ExpectedBody("rfc-fig7", 300)
	path := "compat/options"
	// /options is an EXISTING resource in RFC Figure 7, so PUT -> 2.04.
	if _, err := app.Store().Put(path, 0, []byte("old-version")); err != nil {
		t.Fatal(err)
	}

	r0 := putBlock(t, peer, target, path, 1234, wire.Block{NUM: 0, M: true, SZX: 3}, body[0:128])
	if r0.Code != uint8(wire.Continue) {
		t.Fatalf("fig7 block0: code %d, want 2.31(95)", r0.Code)
	}
	if r0.BlockOpt == nil || r0.BlockOpt.NUM != 0 || !r0.BlockOpt.M || r0.BlockOpt.SZX != 3 {
		t.Fatalf("fig7 block0 reply descriptor = %+v, want 1:0/1/128", r0.BlockOpt)
	}

	r1 := putBlock(t, peer, target, path, 1235, wire.Block{NUM: 1, M: true, SZX: 3}, body[128:256])
	if r1.Code != uint8(wire.Continue) || r1.BlockOpt == nil ||
		r1.BlockOpt.NUM != 1 || !r1.BlockOpt.M || r1.BlockOpt.SZX != 3 {
		t.Fatalf("fig7 block1 reply = code %d %+v, want 2.31 1:1/1/128", r1.Code, r1.BlockOpt)
	}

	r2 := putBlock(t, peer, target, path, 1236, wire.Block{NUM: 2, M: false, SZX: 3}, body[256:300])
	if r2.Code != uint8(wire.Changed) || r2.BlockOpt == nil ||
		r2.BlockOpt.NUM != 2 || r2.BlockOpt.M || r2.BlockOpt.SZX != 3 {
		t.Fatalf("fig7 block2 reply = code %d %+v, want 2.04 1:2/0/128", r2.Code, r2.BlockOpt)
	}

	res, err := app.Store().Get(path)
	if err != nil {
		t.Fatal(err)
	}
	if oracle.SHA256Hex(res.Body) != oracle.SHA256Hex(body) {
		t.Fatal("fig7 stored bytes differ from assembled body")
	}
}

// RFC 7959 Figure 9 — atomic PUT with server-driven size negotiation.
// Client proposes 128 (SZX=3); server counters with 32 (SZX=1); the client
// renumbers subsequent blocks in 32-byte offsets (next NUM=4). Interim
// replies are 2.31 1:N/1/32, the final one is 2.04.
func TestRFC7959_Figure9_NegotiatedPut32(t *testing.T) {
	app, _, cleanup := harness.StartTestApp(t, testCfg, testFixtures,
		service.WithPreferredSZX(1)) // 32 bytes
	defer cleanup()

	peer, target, err := harness.NewRawPeer(app.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	defer peer.Close()

	body := oracle.ExpectedBody("rfc-fig9", 300) // 128 + 172(6*32 => 5 full + 12 tail)
	path := "compat/negotiated"
	if _, err := app.Store().Put(path, 0, []byte("old-version")); err != nil {
		t.Fatal(err)
	}

	r0 := putBlock(t, peer, target, path, 1234, wire.Block{NUM: 0, M: true, SZX: 3}, body[0:128])
	if r0.Code != uint8(wire.Continue) {
		t.Fatalf("fig9 block0 code %d, want 2.31", r0.Code)
	}
	if r0.BlockOpt == nil || r0.BlockOpt.SZX != 1 || !r0.BlockOpt.M {
		t.Fatalf("fig9 counter-offer = %+v, want 1:0/1/32", r0.BlockOpt)
	}

	// Remaining 172 bytes at 32 bytes: NUMs 4..9, last tail 12 bytes M=0.
	type step struct {
		num  uint32
		more bool
		lo   int
		hi   int
	}
	steps := []step{
		{4, true, 128, 160}, {5, true, 160, 192}, {6, true, 192, 224},
		{7, true, 224, 256}, {8, true, 256, 288}, {9, false, 288, 300},
	}
	for i, s := range steps {
		r := putBlock(t, peer, target, path, uint16(1235+i),
			wire.Block{NUM: s.num, M: s.more, SZX: 1}, body[s.lo:s.hi])
		if s.more {
			if r.Code != uint8(wire.Continue) || r.BlockOpt == nil ||
				r.BlockOpt.SZX != 1 || !r.BlockOpt.M || r.BlockOpt.NUM != s.num {
				t.Fatalf("fig9 block NUM=%d reply %d %+v, want 2.31 1:%d/1/32", s.num, r.Code, r.BlockOpt, s.num)
			}
		} else {
			if r.Code != uint8(wire.Changed) || r.BlockOpt == nil ||
				r.BlockOpt.SZX != 1 || r.BlockOpt.M || r.BlockOpt.NUM != 9 {
				t.Fatalf("fig9 final reply %d %+v, want 2.04 1:9/0/32", r.Code, r.BlockOpt)
			}
		}
	}

	res, _ := app.Store().Get(path)
	if oracle.SHA256Hex(res.Body) != oracle.SHA256Hex(body) {
		t.Fatal("fig9 stored bytes differ after renegotiation")
	}
}

// Network-induced Block1 REORDERING: the impairment proxy delays block 0
// while passing block 1 immediately, so block 1 arrives first and is
// rejected 4.08 request_incomplete/gap. This is driven entirely over UDP,
// not by calling the state machine directly.
func TestImpairment_Block1ReorderedOverUDP_408(t *testing.T) {
	app, _, cleanup := harness.StartTestApp(t, testCfg, testFixtures,
		service.WithPreferredSZX(2))
	defer cleanup()

	proxy, err := harness.NewProxy(app.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	defer proxy.Close()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	proxy.Run(ctx)

	// Delay only the C2S request carrying Block1 NUM=0; NUM=1 passes at once.
	proxy.AddRule(func(p harness.Packet) (harness.Action, time.Duration) {
		if p.Dir == harness.C2S && p.Msg != nil && p.Msg.Code == wire.PUT {
			if b, ok := block1Num(p.Msg); ok && b == 0 {
				return harness.ActionDelay, 200 * time.Millisecond
			}
		}
		return harness.ActionPass, 0
	})

	peer, target, err := harness.NewRawPeer(proxy.ClientAddr().String())
	if err != nil {
		t.Fatal(err)
	}
	defer peer.Close()

	body := repeatASCII('R', 100)
	// Send block 0 then block 1 back-to-back; the proxy reorders delivery.
	go func() {
		_ = peer.Send(block1Put(0xa000, "reorder", wire.Block{NUM: 0, M: true, SZX: 2}, body[0:64]), target)
	}()
	time.Sleep(10 * time.Millisecond)
	if err := peer.Send(block1Put(0xa001, "reorder", wire.Block{NUM: 1, M: false, SZX: 2}, body[64:100]), target); err != nil {
		t.Fatal(err)
	}

	// The FIRST response must be the 4.08 for the early block 1.
	first, ok := peer.ReceiveOne(300 * time.Millisecond)
	if !ok || first.Msg == nil {
		t.Fatal("no response for reordered block")
	}
	if first.Msg.Code != wire.RequestEntityIncomplete {
		t.Fatalf("out-of-order block must be 4.08, first response was %s", first.Msg.Code)
	}
	v, _ := oracleInspect(first.Data)
	// Block 1 (0xa001) is the one rejected.
	if v.MID != 0xa001 {
		t.Fatalf("rejection MID = 0x%04x, want 0xa001", v.MID)
	}

	// The resource must NOT have been committed (block 0 alone is interim).
	if _, err := app.Store().Get("reorder"); err == nil {
		t.Fatal("reordered upload must not leave a committed resource")
	}
}

// Network-induced DUPLICATION: the proxy duplicates block requests in both
// directions. The high-level upload must still commit exactly once and the
// stored bytes must be correct.
func TestImpairment_DuplicatedDatagrams_CommitOnce(t *testing.T) {
	var handlerCalls int64
	app, _, cleanup := harness.StartTestApp(t, testCfg, testFixtures,
		service.WithPreferredSZX(2),
		harnessServiceOpt(func(next transport.Handler) transport.Handler {
			return transport.HandlerFunc(func(r *netUDPAddr, m *wire.Message) *wire.Message {
				atomic.AddInt64(&handlerCalls, 1)
				return next.ServeCoAP(r, m)
			})
		}))
	defer cleanup()

	var commits int64
	app.Engine().SetCommitObserver(func(string, int64) { atomic.AddInt64(&commits, 1) })

	proxy, err := harness.NewProxy(app.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	defer proxy.Close()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	proxy.Run(ctx)

	// Duplicate every C2S PUT exactly once (a bursty network).
	proxy.AddRule(func(p harness.Packet) (harness.Action, time.Duration) {
		if p.Dir == harness.C2S && p.Msg != nil && p.Msg.Code == wire.PUT {
			return harness.ActionDuplicate, 0
		}
		return harness.ActionPass, 0
	})

	c, err := transport.DialClient(transport.ClientOptions{
		Retransmit: transport.Retransmit{ACKTimeout: 200 * time.Millisecond, ACKRandomFactor: 1.0, MaxRetransmit: 2},
	})
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	bc := newBlockwiseFrom(c)

	body := repeatASCII('D', 200) // 64,64,64,8 => 4 blocks
	if _, err := bc.Upload(context.Background(), proxy.ClientAddr(), "dup-net", wire.PUT, 0, body, 2); err != nil {
		t.Fatalf("upload over duplicating proxy: %v", err)
	}
	if got := atomic.LoadInt64(&commits); got != 1 {
		t.Fatalf("commits = %d, duplicated datagrams must commit once", got)
	}
	res, _ := app.Store().Get("dup-net")
	if oracle.SHA256Hex(res.Body) != oracle.SHA256Hex(body) {
		t.Fatal("stored bytes wrong after duplicated transfer")
	}
	// Handler invocations must be ~ one per unique block (4), not doubled to
	// 8 by the replayed copies (message-layer dedup).
	if got := atomic.LoadInt64(&handlerCalls); got != 4 {
		t.Fatalf("handler invocations = %d, want 4 (deduped), duplicated copies must not re-run it", got)
	}
}

func block1Put(mid uint16, path string, b wire.Block, payload []byte) *wire.Message {
	return &wire.Message{
		Type: wire.CON, Code: wire.PUT, MID: mid, Token: []byte{byte(mid)},
		Options: []wire.Option{
			{Number: wire.OpURIPath, Value: []byte(path)},
			{Number: wire.OpBlock1, Value: b.Encode()},
		},
		Payload: payload,
	}
}

func newBlockwiseFrom(c *transport.Client) *engine.BlockwiseClient {
	return engine.NewBlockwiseClient(c)
}

func block1Num(m *wire.Message) (uint32, bool) {
	b, has, err := m.Block1()
	if err != nil || !has {
		return 0, false
	}
	return b.NUM, true
}
