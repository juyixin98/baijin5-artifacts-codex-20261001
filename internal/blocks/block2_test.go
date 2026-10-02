package blocks_test

import (
	"bytes"
	"testing"

	"coaplab/internal/blocks"
	"coaplab/internal/diag"
	"coaplab/internal/wire"
)

// In-order 3-block download reassembles to the exact original bytes.
func TestBlock2_HappyPath(t *testing.T) {
	body := bytes.Repeat([]byte{0x33}, 40) // 16,16,8 at SZX 0
	r := blocks2()
	chunks := blocks.ChunkBody(body, 0)
	tag := []byte{0xaa, 0xbb}
	var assembled []byte
	for i, c := range chunks {
		out, f := r.Offer(c.Block, c.Payload, tag, 0, true)
		if f != nil {
			t.Fatalf("chunk %d: %v", i, f)
		}
		if i == len(chunks)-1 {
			if !out.Complete {
				t.Fatal("final chunk must complete")
			}
			assembled = out.Body
		} else if out.Complete {
			t.Fatalf("chunk %d completed early", i)
		}
	}
	if !bytes.Equal(assembled, body) {
		t.Fatalf("reassembled %d bytes != original %d", len(assembled), len(body))
	}
	if !bytes.Equal(r.ETag(), tag) {
		t.Fatalf("bound etag lost: %x", r.ETag())
	}
}

// Out-of-order delivery (0, then 2-tail, then 1) is buffered and only
// completes when the set is contiguous; byte equality still holds.
func TestBlock2_OutOfOrderBuffered(t *testing.T) {
	body := bytes.Repeat([]byte{0x44}, 40)
	chunks := blocks.ChunkBody(body, 0) // 0,1 (M=1), 2 tail (M=0)
	r := blocks2()
	tag := []byte{1, 2, 3, 4}

	out, f := r.Offer(chunks[0].Block, chunks[0].Payload, tag, 0, true)
	if f != nil || out.Complete {
		t.Fatalf("block0 complete=%v f=%v", out.Complete, f)
	}
	// Tail (block 2) arrives before block 1: not complete, gap at 1.
	out, f = r.Offer(chunks[2].Block, chunks[2].Payload, tag, 0, true)
	if f != nil {
		t.Fatal(f)
	}
	if out.Complete {
		t.Fatal("must not complete with a gap")
	}
	if out.NextRequest != 1 {
		t.Fatalf("NextRequest=%d want 1", out.NextRequest)
	}
	// Fill the gap: now complete and byte-identical.
	out, f = r.Offer(chunks[1].Block, chunks[1].Payload, tag, 0, true)
	if f != nil {
		t.Fatal(f)
	}
	if !out.Complete || !bytes.Equal(out.Body, body) {
		t.Fatalf("after gap fill complete=%v match=%v", out.Complete, bytes.Equal(out.Body, body))
	}
}

// Representation update mid-download: ETag changes => the WHOLE transfer is
// rejected etag_changed; blocks from two versions are never spliced.
func TestBlock2_ETagChangeRejectsSplice(t *testing.T) {
	r := blocks2()
	if _, f := r.Offer(wire.Block{NUM: 0, M: true, SZX: 0}, make([]byte, 16),
		[]byte{0xaa}, 0, true); f != nil {
		t.Fatal(f)
	}
	_, f := r.Offer(wire.Block{NUM: 1, M: false, SZX: 0}, []byte{1, 2},
		[]byte{0xbb}, 0, true)
	if f == nil || f.Category != diag.CatETagChanged || f.Code != wire.Conflict {
		t.Fatalf("want etag_changed/4.09, got %v", f)
	}
	if r.Started() {
		// State remains bound to the ORIGINAL etag; no new version leaks in.
		if !bytes.Equal(r.ETag(), []byte{0xaa}) {
			t.Fatalf("etag binding corrupted: %x", r.ETag())
		}
	}
}

// Identical duplicate: IGNORE, no completion change. Different bytes at the
// same NUM: duplicate_mismatch.
func TestBlock2_DuplicateBehaviour(t *testing.T) {
	r := blocks2()
	b0 := wire.Block{NUM: 0, M: true, SZX: 0}
	p0 := make([]byte, 16)
	if _, f := r.Offer(b0, p0, []byte{9}, 0, true); f != nil {
		t.Fatal(f)
	}
	out, f := r.Offer(b0, p0, []byte{9}, 0, true)
	if f != nil || out.Verdict != diag.Ignore {
		t.Fatalf("identical dup verdict=%s f=%v", out.Verdict, f)
	}
	_, f = r.Offer(b0, bytes.Repeat([]byte{1}, 16), []byte{9}, 0, true)
	if f == nil || f.Category != diag.CatDuplicateMismatch {
		t.Fatalf("changed duplicate must be duplicate_mismatch, got %v", f)
	}
}

// The block size converges on block 0; a later block using another SZX is
// rejected (this subset refuses mid-stream size changes rather than adapting).
func TestBlock2_SZXDriftRejected(t *testing.T) {
	r := blocks2()
	if _, f := r.Offer(wire.Block{NUM: 0, M: true, SZX: 2}, make([]byte, 64),
		[]byte{1}, 0, true); f != nil {
		t.Fatal(f)
	}
	b1 := wire.Block{NUM: 1, M: false, SZX: 3} // 128 vs converged 64
	_, f := r.Offer(b1, []byte{1}, []byte{1}, 0, true)
	if f == nil || f.Category != diag.CatBadBlockSize {
		t.Fatalf("want bad_block_size, got %v", f)
	}
}

// Content-Format drift is itself an assembly error.
func TestBlock2_ContentFormatDrift(t *testing.T) {
	r := blocks2()
	if _, f := r.Offer(wire.Block{NUM: 0, M: true, SZX: 0}, make([]byte, 16),
		[]byte{1}, 0, true); f != nil {
		t.Fatal(f)
	}
	_, f := r.Offer(wire.Block{NUM: 1, M: false, SZX: 0}, []byte{1},
		[]byte{1}, 50, true)
	if f == nil || f.Category != diag.CatContentFormatDrift {
		t.Fatalf("want content_format_drift, got %v", f)
	}
}

// A non-block-0 first chunk cannot start a transfer.
func TestBlock2_MustStartAtZero(t *testing.T) {
	r := blocks2()
	_, f := r.Offer(wire.Block{NUM: 3, M: false, SZX: 0}, []byte{1}, nil, 0, false)
	if f == nil || f.Category != diag.CatGap {
		t.Fatalf("want gap for non-zero start, got %v", f)
	}
}

// Full-size body that is an exact multiple of the block size: tail is a
// full, M=0 block and still completes exactly once.
func TestBlock2_ExactMultipleTail(t *testing.T) {
	body := make([]byte, 48) // 3 x 16
	for i := range body {
		body[i] = byte(i)
	}
	chunks := blocks.ChunkBody(body, 0)
	if chunks[len(chunks)-1].Block.M {
		t.Fatal("last chunk must have M=0")
	}
	r := blocks2()
	var out blocks.Block2Outcome
	for _, c := range chunks {
		o, f := r.Offer(c.Block, c.Payload, nil, 0, false)
		if f != nil {
			t.Fatal(f)
		}
		out = o
	}
	if !out.Complete || !bytes.Equal(out.Body, body) {
		t.Fatal("exact-multiple body did not reassemble")
	}
}

func blocks2() *blocks.Block2Reassembler {
	return blocks.NewBlock2Reassembler("t")
}
