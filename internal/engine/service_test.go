package engine_test

import (
	"net"
	"testing"
	"time"

	"coaplab/internal/blocks"
	"coaplab/internal/diag"
	"coaplab/internal/engine"
	"coaplab/internal/store"
	"coaplab/internal/wire"
)

func testService(t *testing.T, prefSZX uint8) (*engine.Service, *store.Store) {
	t.Helper()
	st, err := store.Open(":memory:")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = st.Close() })
	asm := blocks.NewBlock1Assembler(time.Hour, 1<<20, prefSZX, diag.NewRecorder(nil).Quiet())
	return engine.NewService(st, asm, prefSZX, 1280, diag.NewRecorder(nil).Quiet()), st
}

func raddr() *net.UDPAddr {
	return &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1), Port: 9999}
}

func TestServe_NotFound_ThenPutGetDelete(t *testing.T) {
	svc, st := testService(t, 6)

	get := &wire.Message{Code: wire.GET, Options: []wire.Option{
		{Number: wire.OpURIPath, Value: []byte("thing")},
	}}
	if r := svc.ServeCoAP(raddr(), get); r.Code != wire.NotFound {
		t.Fatalf("missing resource want 4.04, got %s", r.Code)
	}

	// Single-datagram PUT creates (2.01) then GET returns 2.05 identical.
	put := &wire.Message{Code: wire.PUT, Options: []wire.Option{
		{Number: wire.OpURIPath, Value: []byte("thing")},
	}, Payload: []byte("payload-bytes")}
	if r := svc.ServeCoAP(raddr(), put); r.Code != wire.Created {
		t.Fatalf("create want 2.01, got %s", r.Code)
	}
	if r := svc.ServeCoAP(raddr(), get); r.Code != wire.Content || string(r.Payload) != "payload-bytes" {
		t.Fatalf("get after put: %s %q", r.Code, r.Payload)
	}

	// Second PUT to existing path -> 2.04.
	if r := svc.ServeCoAP(raddr(), put); r.Code != wire.Changed {
		t.Fatalf("update want 2.04, got %s", r.Code)
	}

	del := &wire.Message{Code: wire.DELETE, Options: []wire.Option{
		{Number: wire.OpURIPath, Value: []byte("thing")},
	}}
	if r := svc.ServeCoAP(raddr(), del); r.Code != wire.Deleted {
		t.Fatalf("delete want 2.02, got %s", r.Code)
	}
	if r := svc.ServeCoAP(raddr(), del); r.Code != wire.NotFound {
		t.Fatalf("second delete want 4.04, got %s", r.Code)
	}
	if _, err := st.Get("thing"); err == nil {
		t.Fatal("resource still present after delete")
	}
}

func TestServe_UnknownMethodAndCriticalOption(t *testing.T) {
	svc, _ := testService(t, 6)

	badMethod := &wire.Message{Code: wire.Code(9)} // 0.09 FETCH, unsupported
	if r := svc.ServeCoAP(raddr(), badMethod); r.Code != wire.MethodNotAllowed {
		t.Fatalf("unknown method want 4.05, got %s", r.Code)
	}

	// Unknown CRITICAL option (odd number, e.g. 711) -> 4.02 Bad Option.
	unkCrit := &wire.Message{Code: wire.GET, Options: []wire.Option{{Number: 711}}}
	if r := svc.ServeCoAP(raddr(), unkCrit); r.Code != wire.BadOption {
		t.Fatalf("unknown critical opt want 4.02, got %s", r.Code)
	}

	// Unknown ELECTIVE option (even number, e.g. 18) is accepted (-> 4.04).
	unkElec := &wire.Message{Code: wire.GET, Options: []wire.Option{{Number: 18}}}
	if r := svc.ServeCoAP(raddr(), unkElec); r.Code != wire.NotFound {
		t.Fatalf("elective unknown opt must be ignored, got %s", r.Code)
	}
}

func TestServe_ReservedSZX7_Is400(t *testing.T) {
	svc, _ := testService(t, 6)
	// Manually build a request carrying SZX=7 in Block1 (value 0x0f).
	req := &wire.Message{
		Code: wire.PUT,
		Options: []wire.Option{
			{Number: wire.OpURIPath, Value: []byte("x")},
			{Number: wire.OpBlock1, Value: []byte{0x0f}},
		},
	}
	r := svc.ServeCoAP(raddr(), req)
	if r.Code != wire.BadRequest {
		t.Fatalf("SZX=7 must answer 4.00, got %s", r.Code)
	}
}

// A single-datagram PUT larger than the message cap hints Block1 with 4.13
// and a Block1 option carrying the server preference.
func TestServe_OversizedSinglePut_HintsBlock1(t *testing.T) {
	svc, _ := testService(t, 2) // prefers 64
	huge := make([]byte, 2000)
	req := &wire.Message{Code: wire.PUT, Payload: huge}
	r := svc.ServeCoAP(raddr(), req)
	if r.Code != wire.RequestEntityTooLarge {
		t.Fatalf("want 4.13, got %s", r.Code)
	}
	b, has, err := r.Block1()
	if err != nil || !has {
		t.Fatalf("4.13 must carry Block1 hint (has=%v err=%v)", has, err)
	}
	if b.SZX != 2 {
		t.Fatalf("hint SZX = %d, want 2 (64 bytes)", b.SZX)
	}
}

// A GET for an existing large resource with no Block2 is answered
// block-wise (2.05 + Block2 M=1) because it exceeds one datagram.
func TestServe_LargeGet_UsesBlock2(t *testing.T) {
	svc, st := testService(t, 2) // 64-byte preference
	body := make([]byte, 300)
	if _, err := st.Put("big", 0, body); err != nil {
		t.Fatal(err)
	}
	req := &wire.Message{Code: wire.GET, Options: []wire.Option{
		{Number: wire.OpURIPath, Value: []byte("big")},
	}}
	r := svc.ServeCoAP(raddr(), req)
	if r.Code != wire.Content {
		t.Fatalf("want 2.05, got %s", r.Code)
	}
	b, has, err := r.Block2()
	if err != nil || !has || !b.M || b.SZX != 2 || len(r.Payload) != 64 {
		t.Fatalf("first block = %+v has=%v err=%v payload=%d", b, has, err, len(r.Payload))
	}
	if _, hasETag := r.FirstOption(wire.OpETag); !hasETag {
		t.Fatal("block-wise 2.05 must carry ETag")
	}

	// Requesting block 2 with M=1 in the control option is a 4.00.
	badM := &wire.Message{Code: wire.GET, Options: []wire.Option{
		{Number: wire.OpURIPath, Value: []byte("big")},
		{Number: wire.OpBlock2, Value: wire.Block{NUM: 1, M: true, SZX: 2}.Encode()},
	}}
	if rr := svc.ServeCoAP(raddr(), badM); rr.Code != wire.BadRequest {
		t.Fatalf("Block2 request with M=1 want 4.00, got %s", rr.Code)
	}
}
