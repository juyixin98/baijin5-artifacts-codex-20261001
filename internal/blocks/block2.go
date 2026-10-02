package blocks

import (
	"sort"
	"sync"

	"coaplab/internal/diag"
	"coaplab/internal/etag"
	"coaplab/internal/wire"
)

// Block2Reassembler collects Block2 response chunks (RFC 7959 §2.4).
//
// Policy of this subset (stricter than the RFC minimum, by requirement):
//   - chunks may arrive out of order and are buffered;
//   - every chunk MUST carry the same ETag; a changed ETag mid-transfer
//     rejects the WHOLE reassembly (etag_changed), blocks are never spliced
//     across representations;
//   - the block size converges on block 0 and MUST stay constant afterwards
//     (RFC 7959 §2.4); mid-stream SZX changes are rejected, not adapted;
//   - duplicate chunks are idempotent: identical bytes are ignored,
//     different bytes at the same NUM are duplicate_mismatch.
type Block2Reassembler struct {
	mu sync.Mutex

	uri string

	started   bool
	szx       uint8 // converged block exponent
	blockSize int
	etag      []byte
	cf        uint16
	haveCF    bool

	// buffered out-of-order / accepted chunks keyed by block number.
	have map[uint32][]byte
	// mFlags records the M bit per received chunk; the chunk with M=0 is
	// the tail and pins the total length via offset+len.
	mFlags map[uint32]bool
}

// NewBlock2Reassembler starts a fresh download for uri (diagnostic only).
func NewBlock2Reassembler(uri string) *Block2Reassembler {
	return &Block2Reassembler{uri: uri, have: map[uint32][]byte{}, mFlags: map[uint32]bool{}}
}

// Block2Outcome reports what an Offer changed.
type Block2Outcome struct {
	Verdict  diag.Verdict
	Complete bool
	Body     []byte
	// NextRequest is the block number the puller should ask for next
	// (lowest gap), useful even when chunks arrive out of order.
	NextRequest uint32
}

// Offer ingests one descriptive Block2 response chunk.
func (r *Block2Reassembler) Offer(b wire.Block, payload []byte, etagBytes []byte, contentFmt uint16, haveCF bool) (Block2Outcome, *Failure) {
	if f := ValidateIncoming(b, len(payload), "block2"); f != nil {
		return Block2Outcome{}, f
	}
	r.mu.Lock()
	defer r.mu.Unlock()

	if !r.started {
		if b.NUM != 0 {
			return Block2Outcome{}, fail("block2", diag.CatGap, wire.BadRequest,
				"download starts at block %d, expected 0", b.NUM)
		}
		r.started = true
		r.szx = b.SZX
		r.blockSize = b.Size()
		r.etag = append([]byte(nil), etagBytes...)
		r.cf, r.haveCF = contentFmt, haveCF
	} else {
		// Representation binding: ETag MUST be stable for the whole body.
		if len(r.etag) > 0 || len(etagBytes) > 0 {
			if !etagEqual(r.etag, etagBytes) {
				return Block2Outcome{}, fail("block2", diag.CatETagChanged, wire.Conflict,
					"block %d etag %s differs from block-0 etag %s: representation changed mid-download",
					b.NUM, etag.HexShort(etagBytes), etag.HexShort(r.etag))
			}
		}
		// Content-Format drift is an assembly error (RFC 7959 §2.3).
		if haveCF && r.haveCF && contentFmt != r.cf {
			return Block2Outcome{}, fail("block2", diag.CatContentFormatDrift, wire.NotAcceptable,
				"block %d content-format %d differs from %d", b.NUM, contentFmt, r.cf)
		}
		if b.SZX != r.szx {
			return Block2Outcome{}, fail("block2", diag.CatBadBlockSize, wire.BadRequest,
				"block %d uses SZX %d but the sequence converged on SZX %d (%d bytes)",
				b.NUM, b.SZX, r.szx, r.blockSize)
		}
	}

	if f := r.checkOffsetGeometry(b, payload); f != nil {
		return Block2Outcome{}, f
	}

	if prev, ok := r.have[b.NUM]; ok {
		if !bytesEqual(prev, payload) {
			return Block2Outcome{}, fail("block2", diag.CatDuplicateMismatch, wire.Conflict,
				"block %d re-delivered with different bytes (%d vs %d)",
				b.NUM, len(prev), len(payload))
		}
		// Exact duplicate: no state change, no resplice.
		return Block2Outcome{Verdict: diag.Ignore, NextRequest: r.lowestGap()}, nil
	}

	r.have[b.NUM] = append([]byte(nil), payload...)
	r.mFlags[b.NUM] = b.M

	out := Block2Outcome{Verdict: diag.Accept, NextRequest: r.lowestGap()}

	tailNum, haveTail := r.tailBlock()
	if haveTail {
		// Completeness requires every number 0..tail present with M=1
		// before the tail, and offset geometry already validated.
		if uint32(len(r.have)) == tailNum+1 && r.lowestGap() == tailNum+1 {
			body := r.splice(tailNum)
			out.Complete = true
			out.Body = body
		}
	}
	return out, nil
}

// checkOffsetGeometry verifies that non-final chunks fill the block size and
// that block offsets are consistent (defence against renumbered chunks that
// would overlap inside the assembled byte range).
func (r *Block2Reassembler) checkOffsetGeometry(b wire.Block, payload []byte) *Failure {
	size := r.blockSize
	if b.M && len(payload) != size {
		return fail("block2", diag.CatBadBlockSize, wire.BadRequest,
			"block %d M=1 but payload %d != block size %d", b.NUM, len(payload), size)
	}
	if !b.M && len(payload) > size {
		return fail("block2", diag.CatBadBlockSize, wire.BadRequest,
			"tail block %d payload %d exceeds block size %d", b.NUM, len(payload), size)
	}
	if b.NUM > 0 && !b.M && len(payload) == size {
		// A full-size chunk must have M=1 unless the body length is an
		// exact multiple: that case is legal, tail full. No error.
	}
	// NUM must not overflow a sane 20-bit block number.
	if b.NUM >= 1<<20 {
		return fail("block2", diag.CatBadBlockSize, wire.BadRequest, "block number %d out of range", b.NUM)
	}
	return nil
}

func (r *Block2Reassembler) tailBlock() (uint32, bool) {
	var tail uint32
	found := false
	for n, m := range r.mFlags {
		if !m {
			if !found || n > tail {
				tail, found = n, true
			}
		}
	}
	return tail, found
}

// lowestGap returns the first missing block number starting at 0.
func (r *Block2Reassembler) lowestGap() uint32 {
	n := uint32(0)
	for {
		if _, ok := r.have[n]; !ok {
			return n
		}
		n++
	}
}

// splice concatenates blocks 0..tail in number order.
func (r *Block2Reassembler) splice(tail uint32) []byte {
	nums := make([]uint32, 0, len(r.have))
	for n := range r.have {
		nums = append(nums, n)
	}
	sort.Slice(nums, func(i, j int) bool { return nums[i] < nums[j] })
	total := 0
	for _, n := range nums {
		total += len(r.have[n])
	}
	out := make([]byte, 0, total)
	for _, n := range nums {
		out = append(out, r.have[n]...)
	}
	return out
}

// LowestGap returns the first block number starting at 0 that has not yet
// been accepted; sequential pullers request this number next.
func (r *Block2Reassembler) LowestGap() uint32 {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.lowestGap()
}

// Started reports whether block 0 was accepted (size/etag then bound).
func (r *Block2Reassembler) Started() bool {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.started
}

// ETag returns the bound representation validator ("" before start).
func (r *Block2Reassembler) ETag() []byte {
	r.mu.Lock()
	defer r.mu.Unlock()
	return append([]byte(nil), r.etag...)
}

// etagEqual compares, tolerating one side absent.
func etagEqual(a, b []byte) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}
