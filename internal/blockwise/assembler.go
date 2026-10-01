// Package blockwise implements the RFC 7959 Block1 (upload) and Block2
// (download) state machines on top of the messaging layer in package
// protocol. It is deliberately transport-agnostic: the reassembly logic is a
// pure value type so it can be unit-tested without any socket, while the
// uploader/downloader drive a small Transport interface.
//
// The integrity rules enforced here are:
//
//   - Offset continuity: blocks must tile the body with no gap or overlap.
//     Out-of-order arrival is buffered, but a hole is never papered over.
//   - Duplicate blocks are idempotent: a retransmitted block number is
//     acknowledged but its bytes are never committed twice.
//   - Block size is negotiated on block zero and locked afterwards; a
//     mid-transfer size change is rejected because it renumbers every block.
//   - A downloaded representation is bound to one ETag; if the representation
//     changes while the transfer is in flight, reassembly is rejected rather
//     than concatenating bytes from two different versions.
package blockwise

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"sort"
	"sync"
)

func digestOf(b []byte) string {
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:6])
}

// FailureKind is the machine-readable reason a block was rejected or the
// transfer could not be completed. Tests assert the exact kind.
type FailureKind int

// Block-wise failure categories.
const (
	KindOK FailureKind = iota
	// KindGap: a block arrived beyond the next expected offset, leaving a hole.
	KindGap
	// KindOverlap: a block arrived below the next expected offset with
	// different bytes than what was already committed.
	KindOverlap
	// KindSizeChanged: block size changed after the size was locked.
	KindSizeChanged
	// KindETagChanged: a block belonged to a different representation version.
	KindETagChanged
	// KindBadBlockSize: payload exceeded the negotiated block size.
	KindBadBlockSize
	// KindBadMoreFlag: the more/last flags were inconsistent across blocks.
	KindBadMoreFlag
	// KindBlockNum: an illegal block number or option was supplied.
	KindBlockNum
	// KindIncomplete: bytes were requested before every block arrived.
	KindIncomplete
	// KindProtocol: an unexpected CoAP code/option from the peer.
	KindProtocol
	// KindTooLarge: the representation exceeds the negotiated maximum.
	KindTooLarge
)

func (k FailureKind) String() string {
	names := [...]string{
		"ok", "gap", "overlap", "size-changed", "etag-changed",
		"bad-block-size", "bad-more-flag", "bad-block-num", "incomplete",
		"protocol", "too-large",
	}
	if int(k) < 0 || int(k) >= len(names) {
		return "unknown"
	}
	return names[k]
}

// Error carries a FailureKind and human-readable context.
type Error struct {
	Kind FailureKind
	Msg  string
}

func (e *Error) Error() string {
	return "blockwise: " + e.Kind.String() + ": " + e.Msg
}

func fail(kind FailureKind, format string, args ...any) *Error {
	return &Error{Kind: kind, Msg: fmt.Sprintf(format, args...)}
}

// AsError extracts a *blockwise.Error from an error.
func AsError(err error) (*Error, bool) {
	if e, ok := err.(*Error); ok {
		return e, true
	}
	return nil, false
}

// BlockStatus reports what AddBlock did with a specific block.
type BlockStatus int

// Add-block outcomes.
const (
	// StatusAdvanced: the block extended the contiguous prefix.
	StatusAdvanced BlockStatus = iota
	// StatusBuffered: the block was stored but a preceding gap remains.
	StatusBuffered
	// StatusDuplicate: this block number was already present; not committed.
	StatusDuplicate
)

// AddResult summarizes one AddBlock call for diagnostics and tests.
type AddResult struct {
	Status       BlockStatus
	Num          int
	NextExpected int // lowest block number still missing
	Buffered     int // number of out-of-order blocks held behind a gap
	HaveLast     bool
	Complete     bool
}

// Assembler reorders and validates the blocks of one representation. It is
// safe for concurrent use. Zero value is not ready; use NewAssembler.
type Assembler struct {
	mu sync.Mutex

	// size, locked from the first block (block zero negotiation point).
	size     int
	sizeLock bool

	// etag binds every block to one representation version; nil means the
	// caller (upload side) is not validating ETags per block.
	etag     []byte
	etagLock bool

	blocks map[int][]byte
	// contiguous is the count of blocks forming the prefix 0..contiguous-1.
	contiguous int
	// lastNum is the m=0 block number; -1 until that block arrives.
	lastNum int
}

// NewAssembler returns an empty assembler. A zero size defers locking until
// the first block (used when block zero performs size negotiation).
func NewAssembler() *Assembler {
	return &Assembler{blocks: make(map[int][]byte), lastNum: -1}
}

// AddBlock incorporates one block. num/more/szx describe it, payload is its
// bytes, etag optionally binds the block to a representation (nil skips the
// ETag check). It never mutates payload.
func (a *Assembler) AddBlock(num int, more bool, szx int, payload, etag []byte,
) (AddResult, error) {
	if num < 0 {
		return AddResult{}, fail(KindBlockNum, "negative block num %d", num)
	}
	if szx < 0 || szx > 6 {
		return AddResult{}, fail(KindBlockNum, "szx %d out of range 0..6", szx)
	}
	size := 1 << uint(szx+4)
	if len(payload) > size {
		return AddResult{}, fail(KindBadBlockSize,
			"block %d payload %d bytes exceeds negotiated size %d",
			num, len(payload), size)
	}

	a.mu.Lock()
	defer a.mu.Unlock()

	// Lock and re-validate the negotiated size on every subsequent block.
	if !a.sizeLock {
		a.size = size
		a.sizeLock = true
	} else if size != a.size {
		return AddResult{}, fail(KindSizeChanged,
			"block %d uses size %d but transfer locked size %d at block zero",
			num, size, a.size)
	}

	// Bind/verify the representation version (ETag).
	if etag != nil {
		if !a.etagLock {
			a.etag = append([]byte(nil), etag...)
			a.etagLock = true
		} else if !bytes.Equal(a.etag, etag) {
			return AddResult{}, fail(KindETagChanged,
				"block %d ETag %x differs from locked ETag %x: "+
					"representation changed mid-transfer; refusing to splice",
				num, etag, a.etag)
		}
	}

	// Duplicate block: idempotent. If the bytes differ it is still treated as
	// a retransmission of the same numbered block and NOT recommitted, but the
	// discrepancy is surfaced so a peer implementation bug is visible.
	if existing, dup := a.blocks[num]; dup {
		if !bytes.Equal(existing, payload) {
			return AddResult{Status: StatusDuplicate, Num: num,
					NextExpected: a.contiguous, HaveLast: a.lastNum >= 0},
				fail(KindOverlap,
					"duplicate block %d carried different bytes (%x vs %x)",
					num, digestOf(existing), digestOf(payload))
		}
		return AddResult{
			Status:       StatusDuplicate,
			Num:          num,
			NextExpected: a.contiguous,
			Buffered:     a.bufferedLocked(),
			HaveLast:     a.lastNum >= 0,
			Complete:     a.isCompleteLocked(),
		}, nil
	}

	// Track the terminal block and flag consistency.
	if !more {
		if a.lastNum >= 0 && a.lastNum != num {
			return AddResult{}, fail(KindBadMoreFlag,
				"two terminal blocks: num %d and num %d", a.lastNum, num)
		}
		a.lastNum = num
	}

	// A non-terminal block must be exactly full; a short middle block would
	// create a hole the following block cannot fill.
	if more && len(payload) != size {
		return AddResult{}, fail(KindBadBlockSize,
			"non-final block %d is short: %d bytes, want full %d",
			num, len(payload), size)
	}

	// The terminal block cannot be followed by a higher-numbered block.
	if a.lastNum >= 0 && num > a.lastNum {
		return AddResult{}, fail(KindBadMoreFlag,
			"block %d marked more but terminal block %d already seen",
			num, a.lastNum)
	}

	a.blocks[num] = append([]byte(nil), payload...)

	status := StatusBuffered
	if num == a.contiguous {
		a.advanceLocked()
		status = StatusAdvanced
	}
	res := AddResult{
		Status:       status,
		Num:          num,
		NextExpected: a.contiguous,
		Buffered:     a.bufferedLocked(),
		HaveLast:     a.lastNum >= 0,
		Complete:     a.isCompleteLocked(),
	}
	return res, nil
}

// advanceLocked extends the contiguous prefix over any buffered successors.
func (a *Assembler) advanceLocked() {
	for {
		b, ok := a.blocks[a.contiguous]
		if !ok {
			return
		}
		// Full-size block keeps going; a short/empty terminal block ends it.
		isTerminal := len(b) < a.size || a.lastNum == a.contiguous
		a.contiguous++
		if isTerminal {
			if a.lastNum < 0 {
				a.lastNum = a.contiguous - 1
			}
			return
		}
	}
}

func (a *Assembler) bufferedLocked() int {
	n := 0
	for num := range a.blocks {
		if num >= a.contiguous {
			n++
		}
	}
	return n
}

func (a *Assembler) isCompleteLocked() bool {
	return a.lastNum >= 0 && a.contiguous == a.lastNum+1
}

// IsComplete reports whether every block 0..last has been received.
func (a *Assembler) IsComplete() bool {
	a.mu.Lock()
	defer a.mu.Unlock()
	return a.isCompleteLocked()
}

// NextExpected returns the lowest block number not yet contiguously received.
func (a *Assembler) NextExpected() int {
	a.mu.Lock()
	defer a.mu.Unlock()
	return a.contiguous
}

// Size returns the locked block size, or 0 before the first block arrives.
func (a *Assembler) Size() int {
	a.mu.Lock()
	defer a.mu.Unlock()
	return a.size
}

// ETag returns the locked representation ETag, if any.
func (a *Assembler) ETag() []byte {
	a.mu.Lock()
	defer a.mu.Unlock()
	return append([]byte(nil), a.etag...)
}

// Bytes concatenates the blocks in order. It fails if the transfer is not
// complete, so a gappy body can never be materialized.
func (a *Assembler) Bytes() ([]byte, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	if !a.isCompleteLocked() {
		return nil, fail(KindIncomplete,
			"have contiguous blocks 0..%d, terminal is %d: %d block(s) buffered behind a gap",
			a.contiguous-1, a.lastNum, a.bufferedLocked())
	}
	nums := make([]int, 0, len(a.blocks))
	for n := range a.blocks {
		nums = append(nums, n)
	}
	sort.Ints(nums)
	var out bytes.Buffer
	for _, n := range nums {
		out.Write(a.blocks[n])
	}
	return out.Bytes(), nil
}

// GapNums lists block numbers still missing below the terminal block.
func (a *Assembler) GapNums() []int {
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.lastNum < 0 {
		return nil
	}
	var missing []int
	for n := 0; n < a.lastNum; n++ {
		if _, ok := a.blocks[n]; !ok {
			missing = append(missing, n)
		}
	}
	return missing
}
