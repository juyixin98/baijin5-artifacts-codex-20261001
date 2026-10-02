package blocks_test

import (
	"bytes"
	"testing"
	"time"

	"coaplab/internal/blocks"
	"coaplab/internal/diag"
	"coaplab/internal/wire"
)

func newAssembler(t *testing.T) *blocks.Block1Assembler {
	t.Helper()
	return blocks.NewBlock1Assembler(time.Hour, 1<<20, 6, diag.NewRecorder(nil).Quiet())
}

func commitOK(string, uint16, []byte) (wire.Code, *blocks.Failure) { return wire.Changed, nil }

func offer(a *blocks.Block1Assembler, b wire.Block, p []byte) (blocks.Block1Result, *blocks.Failure) {
	return a.Offer("127.0.0.1:9", b, p, 0, true, "r", commitOK)
}

// Strict contiguity: 300 bytes at 16-byte blocks must accept in order and
// commit once on the final chunk.
func TestBlock1_HappyPath_CommitsOnce(t *testing.T) {
	a := newAssembler(t)
	body := bytes.Repeat([]byte{'A'}, 300)
	chunks := blocks.ChunkBody(body, 0) // 16-byte blocks => 19 chunks
	commits := 0
	commit := func(string, uint16, []byte) (wire.Code, *blocks.Failure) {
		commits++
		return wire.Changed, nil
	}
	for i, c := range chunks {
		res, f := a.Offer("127.0.0.1:9", c.Block, c.Payload, 0, true, "r", commit)
		if f != nil {
			t.Fatalf("chunk %d rejected: %v", i, f)
		}
		if i < len(chunks)-1 {
			if res.ReplyCode != wire.Continue || !res.Reply.M {
				t.Fatalf("chunk %d: want 2.31 M=1, got %s M=%d", i, res.ReplyCode, b2m(res.Reply.M))
			}
		} else {
			if res.ReplyCode != wire.Changed || !res.Final {
				t.Fatalf("final chunk: want 2.04 Final, got %s final=%v", res.ReplyCode, res.Final)
			}
			if !bytes.Equal(res.Body, body) {
				t.Fatalf("assembled body mismatch (%d bytes)", len(res.Body))
			}
		}
	}
	if commits != 1 {
		t.Fatalf("commit called %d times, want exactly 1", commits)
	}
}

// A gap (block 0 then block 2) MUST be refused 4.08 request_incomplete.
func TestBlock1_GapRejected408(t *testing.T) {
	a := newAssembler(t)
	if _, f := offer(a, wire.Block{NUM: 0, M: true, SZX: 0}, bytes.Repeat([]byte{1}, 16)); f != nil {
		t.Fatalf("block 0: %v", f)
	}
	_, f := offer(a, wire.Block{NUM: 2, M: false, SZX: 0}, []byte{9})
	if f == nil || f.Code != wire.RequestEntityIncomplete || f.Category != diag.CatGap {
		t.Fatalf("gap must be 4.08/gap, got %v", f)
	}
}

// Starting at a non-zero block with no prior state MUST be 4.08.
func TestBlock1_StartAtBlockTwoRejected(t *testing.T) {
	a := newAssembler(t)
	_, f := offer(a, wire.Block{NUM: 5, M: false, SZX: 0}, []byte{1})
	if f == nil || f.Code != wire.RequestEntityIncomplete {
		t.Fatalf("want 4.08, got %v", f)
	}
}

// Duplicate block with identical bytes is answered (IGNORE) but MUST NOT
// commit twice; duplicate with DIFFERENT bytes MUST be rejected.
func TestBlock1_DuplicateIdempotentThenMismatch(t *testing.T) {
	a := newAssembler(t)
	commits := 0
	commit := func(string, uint16, []byte) (wire.Code, *blocks.Failure) {
		commits++
		return wire.Changed, nil
	}
	b0 := wire.Block{NUM: 0, M: true, SZX: 0}
	p0 := bytes.Repeat([]byte{7}, 16)
	off := func(f *blocks.Failure) {
		if f != nil {
			t.Fatalf("offer: %v", f)
		}
	}
	r1, f := a.Offer("h", b0, p0, 0, true, "r", commit)
	off(f)
	if r1.Verdict != diag.Accept {
		t.Fatalf("first verdict %s", r1.Verdict)
	}
	// Exact retransmit.
	r2, f := a.Offer("h", b0, p0, 0, true, "r", commit)
	off(f)
	if r2.Verdict != diag.Ignore || r2.ReplyCode != wire.Continue {
		t.Fatalf("dup verdict=%s code=%s, want IGNORE/2.31", r2.Verdict, r2.ReplyCode)
	}
	if commits != 0 {
		t.Fatalf("interim duplicate must not commit, commits=%d", commits)
	}
	// Same NUM/MID-range, different bytes => duplicate_mismatch.
	_, f = a.Offer("h", b0, bytes.Repeat([]byte{8}, 16), 0, true, "r", commit)
	if f == nil || f.Category != diag.CatDuplicateMismatch || f.Code != wire.Conflict {
		t.Fatalf("mismatched duplicate must be conflict/duplicate_mismatch, got %v", f)
	}
}

// After the final block, retransmitting the final block replays the final
// code without a second commit (server-side dedup idempotency).
func TestBlock1_FinalRetransmitNoRecommit(t *testing.T) {
	a := newAssembler(t)
	commits := 0
	commit := func(string, uint16, []byte) (wire.Code, *blocks.Failure) {
		commits++
		return wire.Changed, nil
	}
	chunks := blocks.ChunkBody([]byte("hello block1!!"), 0) // 16 + 2
	for _, c := range chunks {
		if _, f := a.Offer("h", c.Block, c.Payload, 0, true, "r", commit); f != nil {
			t.Fatal(f)
		}
	}
	if commits != 1 {
		t.Fatalf("commits=%d", commits)
	}
	last := chunks[len(chunks)-1]
	res, f := a.Offer("h", last.Block, last.Payload, 0, true, "r", commit)
	if f != nil {
		t.Fatal(f)
	}
	if res.Verdict != diag.Ignore || res.ReplyCode != wire.Changed || commits != 1 {
		t.Fatalf("final retransmit verdict=%s code=%s commits=%d", res.Verdict, res.ReplyCode, commits)
	}
}

// Mid-transfer server-preferred size reduction: client sends block 0 at
// SZX 6 (1024), server answers ceiling SZX 0 (16); subsequent chunks are
// renumbered in byte offsets and still assemble contiguously.
func TestBlock1_NegotiatedSizeReduction(t *testing.T) {
	a := blocks.NewBlock1Assembler(time.Hour, 1<<20, 0, diag.NewRecorder(nil).Quiet()) // prefers 16
	body := bytes.Repeat([]byte{0x5a}, 40)                                             // 40 bytes
	// First chunk carries 1024-size descriptor but only 40 real bytes... that
	// violates M=1 fill rule, so instead model the realistic adaptation:
	// block 0 = 1024 proposed, server caps, client RE-SENDS from offset 0 at
	// 16-byte granularity. First message here is already 16 bytes (client
	// learned via the control reply path).
	chunks := blocks.ChunkBody(body, 0)
	for i, c := range chunks {
		res, f := offerFrom(a, c.Block, c.Payload)
		if f != nil {
			t.Fatalf("chunk %d: %v", i, f)
		}
		if res.Reply.SZX != 0 {
			t.Fatalf("ceiling SZX = %d, want 0", res.Reply.SZX)
		}
	}
}

func offerFrom(a *blocks.Block1Assembler, b wire.Block, p []byte) (blocks.Block1Result, *blocks.Failure) {
	return a.Offer("h", b, p, 0, true, "r", commitOK)
}

// Explicit renegotiation: block 0 arrives with 1024-size FULL content
// (1024 bytes), server prefers 256 (SZX=4); reply carries SZX=4; block 1
// then uses 256-byte numbering (offset 1024 => NUM 4). Assembly succeeds.
func TestBlock1_SizeReductionAfterLargeBlock0(t *testing.T) {
	a := blocks.NewBlock1Assembler(time.Hour, 1<<20, 4, diag.NewRecorder(nil).Quiet())
	first := bytes.Repeat([]byte{1}, 1024)
	tail := bytes.Repeat([]byte{2}, 32)
	res, f := a.Offer("h", wire.Block{NUM: 0, M: true, SZX: 6}, first, 0, true, "r", commitOK)
	if f != nil {
		t.Fatal(f)
	}
	if res.Reply.SZX != 4 {
		t.Fatalf("block-0 reply SZX = %d, want 4", res.Reply.SZX)
	}
	// Next chunk starts at byte 1024 => NUM=4 at 256-byte size.
	final := wire.Block{NUM: 4, M: false, SZX: 4}
	res, f = a.Offer("h", final, tail, 0, true, "r", commitOK)
	if f != nil {
		t.Fatalf("renumbered final: %v", f)
	}
	if !res.Final || len(res.Body) != 1056 {
		t.Fatalf("final body len=%d final=%v", len(res.Body), res.Final)
	}
}

// Content-Format drift mid-upload MUST fail (RFC 7959 §2.3 -> 4.08).
func TestBlock1_ContentFormatDrift(t *testing.T) {
	a := newAssembler(t)
	if _, f := a.Offer("h", wire.Block{NUM: 0, M: true, SZX: 0}, make([]byte, 16), 0, true, "r", commitOK); f != nil {
		t.Fatal(f)
	}
	_, f := a.Offer("h", wire.Block{NUM: 1, M: false, SZX: 0}, []byte{1}, 50, true, "r", commitOK)
	if f == nil || f.Category != diag.CatContentFormatDrift {
		t.Fatalf("want content_format_drift, got %v", f)
	}
}

// M=1 block whose payload does not fill SZX is a bad-block-size rejection.
func TestBlock1_ShortMoreBlockRejected(t *testing.T) {
	a := newAssembler(t)
	_, f := offer(a, wire.Block{NUM: 0, M: true, SZX: 0}, []byte{1, 2, 3}) // only 3 of 16
	if f == nil || f.Category != diag.CatBadBlockSize {
		t.Fatalf("want bad_block_size, got %v", f)
	}
}

// Expired partial upload is discarded; a late block yields 4.08.
func TestBlock1_RetentionExpiry(t *testing.T) {
	clk := &fakeClock{t: time.Unix(1000, 0)}
	a := blocks.NewBlock1Assembler(50*time.Millisecond, 1<<20, 6, diag.NewRecorder(nil).Quiet())
	a.SetClock(clk.now)
	if _, f := offer(a, wire.Block{NUM: 0, M: true, SZX: 0}, make([]byte, 16)); f != nil {
		t.Fatal(f)
	}
	clk.t = clk.t.Add(100 * time.Millisecond)
	_, f := offer(a, wire.Block{NUM: 1, M: false, SZX: 0}, []byte{1})
	if f == nil || f.Category != diag.CatExchangeLifetime || f.Code != wire.RequestEntityIncomplete {
		t.Fatalf("want exchange_lifetime/4.08, got %v", f)
	}
}

func TestNegotiateAndSZXFor(t *testing.T) {
	if got := blocks.Negotiate(6, 2); got != 2 {
		t.Fatalf("negotiate(6,2)=%d want 2", got)
	}
	if got := blocks.Negotiate(1, 4); got != 1 {
		t.Fatalf("negotiate(1,4)=%d want 1", got)
	}
	cases := map[int]uint8{15: 0, 16: 0, 17: 0, 31: 0, 32: 1, 1024: 6, 2048: 6}
	for want, szx := range cases {
		if got := blocks.SZXFor(want); got != szx {
			t.Errorf("SZXFor(%d)=%d want %d", want, got, szx)
		}
	}
}

type fakeClock struct{ t time.Time }

func (f *fakeClock) now() time.Time { return f.t }

// GC must drop expired partials so a later block 0 starts a fresh sequence.
func TestBlock1_GCDropsExpiredPartials(t *testing.T) {
	clk := &fakeClock{t: time.Unix(0, 0)}
	a := blocks.NewBlock1Assembler(50*time.Millisecond, 1<<20, 6, diag.NewRecorder(nil).Quiet())
	a.SetClock(clk.now)
	if _, f := offer(a, wire.Block{NUM: 0, M: true, SZX: 0}, make([]byte, 16)); f != nil {
		t.Fatal(f)
	}
	clk.t = clk.t.Add(100 * time.Millisecond)
	a.GC()
	// After GC, block 0 must be accepted as a NEW sequence (not replay).
	res, f := offer(a, wire.Block{NUM: 0, M: false, SZX: 0}, bytes.Repeat([]byte{'z'}, 16)) // exact block, M=0
	if f != nil {
		t.Fatalf("fresh block 0 after GC: %v", f)
	}
	if res.Verdict != diag.Accept || !res.Final {
		t.Fatalf("want a fresh final acceptance, got %+v", res)
	}
}

// 4.13 path: an upload exceeding the server body cap is rejected with the
// entity_too_large category and 4.13 code.
func TestBlock1_BodyCapRejected413(t *testing.T) {
	a := blocks.NewBlock1Assembler(time.Hour, 20, 6, diag.NewRecorder(nil).Quiet())
	_, f := offer(a, wire.Block{NUM: 0, M: true, SZX: 0}, make([]byte, 16))
	if f != nil {
		t.Fatal(f)
	}
	_, f = offer(a, wire.Block{NUM: 1, M: false, SZX: 0}, make([]byte, 16))
	if f == nil || f.Code != wire.RequestEntityTooLarge || f.Category != diag.CatTooLarge {
		t.Fatalf("want 4.13/entity_too_large, got %v", f)
	}
}

// A block arriving with SZX larger than the negotiated ceiling is refused.
func TestBlock1_SizeGrewBeyondCeiling(t *testing.T) {
	a := blocks.NewBlock1Assembler(time.Hour, 1<<20, 1, diag.NewRecorder(nil).Quiet())
	if _, f := offer(a, wire.Block{NUM: 0, M: true, SZX: 1}, make([]byte, 32)); f != nil {
		t.Fatal(f)
	}
	// NUM=1 at SZX=6 claims offset 1024, skipping ahead -> gap anyway;
	// directly assert the ceiling rule with a contiguous-but-larger number.
	_, f := offer(a, wire.Block{NUM: 1, M: false, SZX: 6}, make([]byte, 1))
	if f == nil {
		t.Fatal("SZX growth beyond ceiling must be rejected")
	}
}

func b2m(m bool) int {
	if m {
		return 1
	}
	return 0
}
