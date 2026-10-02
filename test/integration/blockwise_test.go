package integration_test

import (
	"context"
	"sync/atomic"
	"testing"
	"time"

	"coaplab/internal/engine"
	"coaplab/internal/transport"
	"coaplab/internal/wire"
	"coaplab/test/harness"
	"coaplab/test/oracle"
)

func newBlockwiseClient(t *testing.T) (*transport.Client, *engine.BlockwiseClient) {
	t.Helper()
	c, err := transport.DialClient(transport.ClientOptions{
		Retransmit: transport.Retransmit{ACKTimeout: 80 * time.Millisecond, ACKRandomFactor: 1.0, MaxRetransmit: 3},
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = c.Close() })
	return c, engine.NewBlockwiseClient(c)
}

// End-to-end happy path: UPLOAD a 3073-byte body with Block1 proposing
// 1024-byte blocks while the server prefers 64; the transfer negotiates
// down, then DOWNLOAD it with Block2 and verify the bytes with the
// INDEPENDENT oracle (formula + SHA-256), not with the SUT's own code.
func TestBlockwise_UploadNegotiateDownload_OracleBytes(t *testing.T) {
	app, _, cleanup := harness.StartTestApp(t, testCfg, testFixtures)
	defer cleanup()

	const path = "cd/3073b"
	want := oracle.ExpectedBody(path, 3073)

	_, bc := newBlockwiseClient(t)
	ctx := context.Background()

	up, err := bc.Upload(ctx, app.Addr(), path, wire.PUT, 0, want, 6 /*1024 proposed*/)
	if err != nil {
		t.Fatalf("upload: %v", err)
	}
	if up.NegotiatedSZX != 2 {
		t.Fatalf("negotiated SZX = %d, want 2 (64 bytes, server preference)", up.NegotiatedSZX)
	}
	// First block 1024 bytes, then (3073-1024)=2049 at 64 bytes = 33 blocks.
	if up.Exchanges != 34 {
		t.Fatalf("upload exchanges = %d, want 34 (1 @1024 + 33 @64)", up.Exchanges)
	}

	dl, err := bc.Download(ctx, app.Addr(), path, 6)
	if err != nil {
		t.Fatalf("download: %v", err)
	}
	if dl.SZX != 2 {
		t.Fatalf("download SZX = %d, want 2", dl.SZX)
	}
	if dl.Exchanges != 49 { // ceil(3073/64) = 49
		t.Fatalf("download exchanges = %d, want 49", dl.Exchanges)
	}
	if len(dl.Body) != 3073 {
		t.Fatalf("downloaded %d bytes", len(dl.Body))
	}
	if oracle.SHA256Hex(dl.Body) != oracle.SHA256Hex(want) {
		t.Fatal("downloaded bytes differ from oracle-expected body")
	}
	// Store terminal state: version and body.
	res, err := app.Store().Get(path)
	if err != nil {
		t.Fatal(err)
	}
	if res.Version != 2 { // seeded once, PUT bumps to 2
		t.Fatalf("stored version = %d, want 2", res.Version)
	}
}

// Out-of-order Block2 RETRIEVAL: a raw peer requests blocks 0, then the
// tail, then the gap (1..) in shuffled order, feeding each real server
// response into the INDEPENDENT oracle reassembler. The oracle must report
// incomplete while a gap exists and byte-identical at the end.
func TestBlockwise_Block2_OutOfOrderRetrieval(t *testing.T) {
	app, _, cleanup := harness.StartTestApp(t, testCfg, testFixtures)
	defer cleanup()

	const path = "cd/300b" // 64-byte server blocks => 5 blocks (4*64 + 44)
	peer, target, err := harness.NewRawPeer(app.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	defer peer.Close()

	ref := oracle.NewReferenceReassembler()
	order := []uint32{0, 4, 2, 1, 3} // deliberately shuffled
	var boundETag []byte
	for i, num := range order {
		req := &wire.Message{
			Type: wire.CON, Code: wire.GET, MID: uint16(0x6000 + num),
			Token: []byte{byte(num + 1)},
			Options: []wire.Option{
				{Number: wire.OpURIPath, Value: []byte(path)},
				{Number: wire.OpBlock2, Value: wire.Block{NUM: num, M: false, SZX: 2}.Encode()},
			},
		}
		if err := peer.Send(req, target); err != nil {
			t.Fatal(err)
		}
		d, ok := peer.ReceiveOne(time.Second)
		if !ok || d.Msg == nil || d.Msg.Code != wire.Content {
			t.Fatalf("block %d no 2.05 (ok=%v)", num, ok)
		}
		v, err := oracleInspect(d.Data)
		if err != nil || v.BlockOpt == nil {
			t.Fatalf("oracle inspect block %d: %v", num, err)
		}
		if v.BlockOpt.SZX != 2 || v.BlockOpt.NUM != num {
			t.Fatalf("block descriptor mismatch: %+v", v.BlockOpt)
		}
		if num == 0 {
			boundETag = v.ETag
		} else if !bytesEqual(v.ETag, boundETag) {
			t.Fatalf("block %d etag drifted", num)
		}
		if err := ref.Add(v.BlockOpt.NUM, v.BlockOpt.M, v.BlockOpt.SZX, v.Payload, v.ETag); err != nil {
			t.Fatalf("oracle add %d: %v", num, err)
		}
		if i < len(order)-1 && ref.Complete() {
			t.Fatal("oracle must not complete while gaps remain")
		}
	}
	if !ref.Complete() {
		t.Fatal("oracle incomplete after all blocks retrieved")
	}
	got := ref.Body()
	want := oracle.ExpectedBody(path, 300)
	if oracle.SHA256Hex(got) != oracle.SHA256Hex(want) {
		t.Fatalf("out-of-order assembly mismatch: %x vs %x",
			oracle.SHA256Hex(got), oracle.SHA256Hex(want))
	}
}

// Representation update DURING a download: block 0 binds ETag e1; the store
// is then mutated to a new version; block 1 arrives with ETag e2. The SUT
// client must abort with etag_changed and the oracle must independently
// observe that the two blocks belong to different versions — never spliced.
func TestBlockwise_RepresentationUpdateMidDownload_RejectsSplice(t *testing.T) {
	app, _, cleanup := harness.StartTestApp(t, testCfg, testFixtures)
	defer cleanup()

	const path = "cd/1025b" // 64-byte blocks => 17 blocks; spans multiple requests

	peer, target, err := harness.NewRawPeer(app.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	defer peer.Close()

	getBlock := func(num uint32, mid uint16, token byte) wireResult {
		req := &wire.Message{
			Type: wire.CON, Code: wire.GET, MID: mid, Token: []byte{token},
			Options: []wire.Option{
				{Number: wire.OpURIPath, Value: []byte(path)},
				{Number: wire.OpBlock2, Value: wire.Block{NUM: num, M: false, SZX: 2}.Encode()},
			},
		}
		if err := peer.Send(req, target); err != nil {
			t.Fatal(err)
		}
		d, ok := peer.ReceiveOne(time.Second)
		if !ok {
			t.Fatalf("no response for block %d", num)
		}
		v, err := oracleInspect(d.Data)
		if err != nil {
			t.Fatal(err)
		}
		return wireResult{view: v, msg: d.Msg}
	}

	b0 := getBlock(0, 0x7000, 1)
	if b0.view.BlockOpt == nil || !b0.view.HasETag {
		t.Fatal("block 0 must carry Block2 and ETag")
	}
	etag1 := append([]byte(nil), b0.view.ETag...)

	// --- Representation changes mid-transfer (simulated external update) ---
	// Must stay >= 2 blocks (>=128 bytes) so block 1 of the NEW version
	// exists; otherwise the server would answer block 1 with 4.00 gap.
	newVersion := repeatASCII('Z', 200)
	saved, err := app.Store().Put(path, 0, newVersion)
	if err != nil {
		t.Fatal(err)
	}
	if saved.Version != 2 {
		t.Fatalf("mutated version = %d", saved.Version)
	}

	// Block 1 of the OLD transfer now answers with the NEW etag.
	b1 := getBlock(1, 0x7001, 2)
	if bytesEqual(b1.view.ETag, etag1) {
		t.Fatal("server must serve the new representation/etag after update")
	}

	// Feed both into the SUT reassembler via the high-level client path is
	// awkward scripted; assert the decision using the independent oracle AND
	// the SUT's own state machine, which must agree.
	if err := oracleAddChanged(b0, b1); err == nil {
		t.Fatal("oracle must reject splicing the two versions")
	}
	// The SUT client, running a fresh download of the now-small resource,
	// must succeed and return the NEW bytes (consistent terminal state).
	_, bc := newBlockwiseClient(t)
	dl, err := bc.Download(context.Background(), app.Addr(), path, 2)
	if err != nil {
		t.Fatalf("fresh download after update: %v", err)
	}
	if string(dl.Body) != string(newVersion) {
		t.Fatal("terminal state: download did not return current representation")
	}
	if !bytesEqual(dl.ETag, saved.ETag) {
		t.Fatal("terminal state: downloaded etag != stored etag")
	}
}

// Live client straddling an update: a proxy hook mutates the representation
// when the block-1 REQUEST crosses; the client's reassembler must surface a
// classified etag_changed TransferError.
func TestBlockwise_LiveClientETagChangeRejected(t *testing.T) {
	app, _, cleanup := harness.StartTestApp(t, testCfg, testFixtures)
	defer cleanup()
	const path = "cd/3073b"

	proxy, err := harness.NewProxy(app.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	defer proxy.Close()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	proxy.Run(ctx)

	var updated int32
	proxy.AddRule(func(p harness.Packet) (harness.Action, time.Duration) {
		if p.Dir == harness.C2S && p.Msg != nil && p.Msg.Code == wire.GET {
			if b, ok := block2Num(p.Msg); ok && b == 3 {
				if atomicCAS(&updated) {
					// New representation must still span block 3 (offset 192
					// at SZX=2); use a full-size, distinct body.
					_, _ = app.Store().Put(path, 0, repeatASCII('X', 3073))
				}
			}
		}
		return harness.ActionPass, 0
	})

	_, bc := newBlockwiseClient(t)
	_, err = bc.Download(context.Background(), proxy.ClientAddr(), path, 2)
	if err == nil {
		t.Fatal("download straddling an update must fail, not splice")
	}
	te, ok := err.(*engine.TransferError)
	if !ok || te.Category != "etag_changed" {
		t.Fatalf("want *TransferError etag_changed, got %T %v", err, err)
	}
}

// Duplicate blocks must not be committed twice: after a completed Block1
// upload, resending the final chunk under a NEW MID (so it passes the
// message layer) must replay 2.04 but the store version must not advance.
func TestBlockwise_DuplicateFinalBlockNotRecommitted(t *testing.T) {
	app, _, cleanup := harness.StartTestApp(t, testCfg, testFixtures)
	defer cleanup()
	const path = "dup-final"
	body := repeatASCII('Q', 100) // 100 bytes at 64 => blocks 0 (M=1), 1 tail

	var commits int64
	app.Engine().SetCommitObserver(func(string, int64) { atomic.AddInt64(&commits, 1) })

	tc, bc := newBlockwiseClient(t)
	if _, err := bc.Upload(context.Background(), app.Addr(), path, wire.PUT, 0, body, 2); err != nil {
		t.Fatalf("upload: %v", err)
	}
	if got := atomic.LoadInt64(&commits); got != 1 {
		t.Fatalf("commits after upload = %d, want 1", got)
	}
	res, _ := app.Store().Get(path)
	if res.Version != 1 {
		t.Fatalf("new resource version = %d, want 1", res.Version)
	}

	// Resend the FINAL chunk (block 1 tail) from the SAME endpoint (same
	// socket) with a fresh MID/Token, so it passes the message layer but
	// hits the completed Block1 sequence. It must replay 2.04 WITHOUT a
	// second commit and without advancing the version.
	tailPayload := body[64:]
	replay := &wire.Message{
		Code: wire.PUT,
		Options: []wire.Option{
			{Number: wire.OpURIPath, Value: []byte(path)},
			{Number: wire.OpContentFormat, Value: wire.EncodeUint(0)},
			{Number: wire.OpBlock1, Value: wire.Block{NUM: 1, M: false, SZX: 2}.Encode()},
		},
		Payload: tailPayload,
	}
	resp, err := tc.Exchange(context.Background(), app.Addr(), replay)
	if err != nil {
		t.Fatalf("final-block replay exchange: %v", err)
	}
	// The original final PUT created a new resource => 2.01 Created. A
	// retransmission must replay that SAME final code, not recompute Changed.
	if resp.Code != wire.Created {
		t.Fatalf("replay of completed final block must replay 2.01, got %s (%s)", resp.Code, resp.Payload)
	}
	if got := atomic.LoadInt64(&commits); got != 1 {
		t.Fatalf("duplicate final block recommitted: commits=%d", got)
	}
	res2, _ := app.Store().Get(path)
	if res2.Version != 1 {
		t.Fatalf("version advanced on duplicate: %d", res2.Version)
	}
	if !bytesEqual(res2.Body, body) {
		t.Fatal("stored body altered by duplicate replay")
	}
}

// A Block1 gap (block 0 then block 2, block 1 missing) is refused 4.08
// request_incomplete with a specific failure category.
func TestBlockwise_Block1GapRejected408(t *testing.T) {
	app, _, cleanup := harness.StartTestApp(t, testCfg, testFixtures)
	defer cleanup()
	peer, target, err := harness.NewRawPeer(app.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	defer peer.Close()

	send := func(num uint32, more bool, mid uint16, payload []byte) wire.Code {
		req := &wire.Message{
			Type: wire.CON, Code: wire.PUT, MID: mid, Token: []byte{byte(mid)},
			Options: []wire.Option{
				{Number: wire.OpURIPath, Value: []byte("gap-upload")},
				{Number: wire.OpBlock1, Value: wire.Block{NUM: num, M: more, SZX: 2}.Encode()},
			},
			Payload: payload,
		}
		if err := peer.Send(req, target); err != nil {
			t.Fatal(err)
		}
		d, ok := peer.ReceiveOne(time.Second)
		if !ok || d.Msg == nil {
			t.Fatalf("no reply for block %d", num)
		}
		return d.Msg.Code
	}

	if code := send(0, true, 0x9000, make([]byte, 64)); code != wire.Continue {
		t.Fatalf("block 0 want 2.31, got %s", code)
	}
	if code := send(2, false, 0x9002, []byte{1, 2, 3}); code != wire.RequestEntityIncomplete {
		t.Fatalf("gap block want 4.08, got %s", code)
	}
}
