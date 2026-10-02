package blocks

import (
	"sync"
	"time"

	"coaplab/internal/diag"
	"coaplab/internal/wire"
)

// Block1Result is the disposition of one incoming Block1 chunk.
type Block1Result struct {
	// Accepted: chunk advanced (or replayed for) the assembly.
	Verdict diag.Verdict
	// Final is true when this chunk completed the body; Body is the
	// fully assembled representation (nil unless Final).
	Final bool
	Body  []byte
	// Reply is the Block1 control option the responder must echo.
	Reply wire.Block
	// ReplyCode is 2.31 for interim chunks and supplied by the commit
	// callback for the final chunk.
	ReplyCode wire.Code
}

// CommitFunc applies a fully assembled, verified body. It returns the final
// CoAP response code (2.01 Created / 2.04 Changed / ...) or an error code.
type CommitFunc func(path string, contentFormat uint16, body []byte) (wire.Code, *Failure)

// block1State is one in-flight atomic Block1 upload (RFC 7959 §2.5).
// Contiguity is tracked on byte offsets, not block numbers, so an RFC 7959
// mid-transfer block-size renegotiation (NUM scales when SZX shrinks) works.
type block1State struct {
	remote, path string
	contentFmt   uint16
	haveCF       bool
	ceilingSZX   uint8 // server may shrink this once, after block 0
	negotiated   bool  // block 0 answered: ceiling is fixed
	frontier     int64 // bytes accepted contiguously so far
	prefix       []byte
	completed    bool
	finalCode    wire.Code
	lastSeen     time.Time
}

// blockOffset duplicates wire.Block.Offset so callers in tests can reason
// about renegotiated NUM values explicitly.
func blockOffset(b wire.Block) int64 { return int64(b.NUM) << (b.SZX + 4) }

// ReqMeta carries the message-layer identifiers of the datagram that
// conveyed a chunk, so diagnostics can correlate a block decision with its
// MID/Token. It is optional (zero value => identifiers omitted).
type ReqMeta struct {
	MID     uint16
	HaveMID bool
	Token   string // masked hex, rendered by the caller
}

// Block1Assembler holds per-(endpoint, URI) upload states (RFC 7959: the
// sequence is identified by endpoint + URI). It is safe for concurrent use.
type Block1Assembler struct {
	mu        sync.Mutex
	states    map[string]*block1State
	retention time.Duration
	maxBody   int
	prefSZX   uint8
	now       func() time.Time
	rec       *diag.Recorder
	curMeta   ReqMeta // valid only while Offer holds the mutex
}

// NewBlock1Assembler builds an assembler with atomic (2.31-until-final)
// semantics. retention bounds partial-body lifetime (RFC 7959 §2.5); after
// it elapses a late chunk yields 4.08 request_incomplete.
func NewBlock1Assembler(retention time.Duration, maxBody int, preferredSZX uint8, rec *diag.Recorder) *Block1Assembler {
	return &Block1Assembler{
		states:    map[string]*block1State{},
		retention: retention,
		maxBody:   maxBody,
		prefSZX:   preferredSZX,
		now:       time.Now,
		rec:       rec,
	}
}

func key(remote, path string) string { return remote + "\x00" + path }

// SetClock overrides the time source (tests). Not safe to call concurrently
// with Offer.
func (a *Block1Assembler) SetClock(now func() time.Time) {
	a.mu.Lock()
	a.now = now
	a.mu.Unlock()
}

// GC discards expired partial uploads. Safe to call periodically.
func (a *Block1Assembler) GC() {
	a.mu.Lock()
	defer a.mu.Unlock()
	now := a.now()
	for k, s := range a.states {
		if !s.completed && now.Sub(s.lastSeen) > a.retention {
			delete(a.states, k)
		}
	}
}

// Offer processes one descriptive Block1 chunk (a PUT/POST request body
// slice). Exact duplicate chunks are answered from cache WITHOUT re-committing;
// gaps are refused 4.08; oversized transfers are refused 4.13.
//
// On the final chunk commit is invoked exactly once.
func (a *Block1Assembler) Offer(remote string, b wire.Block, payload []byte, contentFmt uint16, haveCF bool, path string, commit CommitFunc, meta ...ReqMeta) (Block1Result, *Failure) {
	if f := ValidateIncoming(b, len(payload), "block1"); f != nil {
		return Block1Result{}, f
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	if len(meta) > 0 {
		a.curMeta = meta[0]
	} else {
		a.curMeta = ReqMeta{}
	}
	defer func() { a.curMeta = ReqMeta{} }()
	now := a.now()
	k := key(remote, path)
	s := a.states[k]

	if s == nil {
		// A fresh sequence MUST start at byte offset zero.
		if off := blockOffset(b); off != 0 {
			a.record(remote, b, diag.Reject, diag.CatIncomplete,
				"no partial upload for %s and block %d starts at offset %d", path, b.NUM, off)
			return Block1Result{}, fail("block1", diag.CatIncomplete, wire.RequestEntityIncomplete,
				"block-wise upload starts at block %d, expected 0", b.NUM)
		}
		s = &block1State{
			remote: remote, path: path,
			contentFmt: contentFmt, haveCF: haveCF,
			ceilingSZX: Negotiate(b.SZX, a.prefSZX),
			frontier:   0,
			lastSeen:   now,
		}
		a.states[k] = s
	} else {
		if now.Sub(s.lastSeen) > a.retention {
			delete(a.states, k)
			a.record(remote, b, diag.Reject, diag.CatExchangeLifetime,
				"partial upload for %s discarded after %s", path, a.retention)
			return Block1Result{}, fail("block1", diag.CatExchangeLifetime, wire.RequestEntityIncomplete,
				"partial request body expired before block %d", b.NUM)
		}
	}

	// Content-Format must be constant across the whole body (RFC 7959 §2.3).
	if haveCF && s.haveCF && contentFmt != s.contentFmt {
		a.record(remote, b, diag.Reject, diag.CatContentFormatDrift,
			"block %d content-format %d differs from established %d", b.NUM, contentFmt, s.contentFmt)
		return Block1Result{}, fail("block1", diag.CatContentFormatDrift, wire.RequestEntityIncomplete,
			"content-format changed from %d to %d mid-upload", s.contentFmt, contentFmt)
	}
	if !s.haveCF && haveCF {
		s.contentFmt, s.haveCF = contentFmt, true
	}

	off := blockOffset(b)

	// Completed transfer: only exact replays of the final chunk are legal.
	if s.completed {
		return a.replayOrReject(s, remote, b, off, payload)
	}

	// Size ceiling: after block 0 the responder pins the negotiated size;
	// a later chunk larger than the ceiling is a negotiation violation.
	if s.negotiated && b.SZX > s.ceilingSZX {
		a.record(remote, b, diag.Reject, diag.CatBadBlockSize,
			"block %d uses SZX %d (> negotiated ceiling %d)", b.NUM, b.SZX, s.ceilingSZX)
		return Block1Result{}, fail("block1", diag.CatBadBlockSize, wire.BadRequest,
			"block size grew beyond negotiated SZX %d", s.ceilingSZX)
	}

	switch {
	case off < s.frontier:
		// Retransmission / duplicate: accept ONLY if bytes are identical to
		// the already accepted prefix. It is never committed twice.
		return a.replayOrReject(s, remote, b, off, payload)
	case off > s.frontier:
		a.record(remote, b, diag.Reject, diag.CatGap,
			"block %d offset %d leaves gap (frontier at %d)", b.NUM, off, s.frontier)
		return Block1Result{}, fail("block1", diag.CatGap, wire.RequestEntityIncomplete,
			"out-of-sequence block %d: expected offset %d, got %d",
			expectedNum(s, b.SZX), s.frontier, off)
	}

	// Contiguous new chunk. Body size cap -> 4.13 with smaller-SZX hint.
	if int64(len(s.prefix))+int64(len(payload)) > int64(a.maxBody) {
		hint := s.ceilingSZX
		if hint > 2 {
			hint = 2
		}
		a.record(remote, b, diag.Reject, diag.CatTooLarge,
			"assembled body would exceed %d bytes", a.maxBody)
		return Block1Result{}, &Failure{
			Category: diag.CatTooLarge, Code: wire.RequestEntityTooLarge,
			Op:     "block1",
			Detail: "body exceeds server limit; retry with smaller blocks",
		}
	}

	s.prefix = append(s.prefix, payload...)
	s.frontier += int64(len(payload))
	s.lastSeen = now
	s.negotiated = true
	if b.SZX < s.ceilingSZX {
		// Client chose smaller than the negotiated ceiling; RFC allows equal
		// or smaller, but from now on the active size is what the client uses.
		s.ceilingSZX = b.SZX
	}

	res := Block1Result{
		Reply: wire.Block{NUM: b.NUM, M: b.M, SZX: s.ceilingSZX},
	}
	if b.M {
		// Interim chunk: 2.31 Continue, no payload (RFC 7959 §2.9.1).
		res.Verdict = diag.Accept
		res.ReplyCode = wire.Continue
		res.Reply.M = true
		a.record(remote, b, diag.Accept, "",
			"block %d (%d bytes, offset %d) buffered, frontier=%d, 2.31 Continue SZX %d",
			b.NUM, len(payload), off, s.frontier, s.ceilingSZX)
		return res, nil
	}

	// Final chunk: verify nothing is missing (strict sequence guarantees
	// contiguity), commit exactly once.
	code, ferr := commit(path, s.contentFmt, s.prefix)
	if ferr != nil {
		// Keep the prefix so the client can retry the final chunk.
		s.frontier -= int64(len(payload))
		s.prefix = s.prefix[:len(s.prefix)-len(payload)]
		a.record(remote, b, diag.Reject, ferr.Category, "commit rejected: %s", ferr.Detail)
		return Block1Result{}, ferr
	}
	s.completed = true
	s.finalCode = code
	res.Verdict = diag.Accept
	res.Final = true
	res.Body = append([]byte(nil), s.prefix...)
	res.ReplyCode = code
	res.Reply.M = false
	a.record(remote, b, diag.Accept, "",
		"final block %d: assembled %d bytes, committed as %s", b.NUM, len(s.prefix), code)
	return res, nil
}

// replayOrReject answers a chunk landing inside already-accepted territory.
// Identical bytes => idempotent replay (IGNORE, no second commit); different
// bytes at the same offset => duplicate_mismatch rejection.
func (a *Block1Assembler) replayOrReject(s *block1State, remote string, b wire.Block, off int64, payload []byte) (Block1Result, *Failure) {
	end := off + int64(len(payload))
	if end > int64(len(s.prefix)) {
		// Overlaps the frontier but is not fully contained: ambiguous
		// rewrite — refuse rather than splice.
		a.record(remote, b, diag.Reject, diag.CatGap,
			"block %d overlaps frontier (offset %d, len %d, frontier %d)", b.NUM, off, len(payload), s.frontier)
		return Block1Result{}, fail("block1", diag.CatGap, wire.RequestEntityIncomplete,
			"block %d partially overlaps accepted prefix", b.NUM)
	}
	if !bytesEqual(s.prefix[off:end], payload) {
		a.record(remote, b, diag.Reject, diag.CatDuplicateMismatch,
			"duplicate block %d carries different bytes than first submission", b.NUM)
		return Block1Result{}, fail("block1", diag.CatDuplicateMismatch, wire.Conflict,
			"block %d retransmitted with different content", b.NUM)
	}
	res := Block1Result{Verdict: diag.Ignore, Reply: wire.Block{NUM: b.NUM, M: b.M, SZX: s.ceilingSZX}}
	if s.completed {
		res.Reply.M = false
		res.ReplyCode = s.finalCode
		res.Final = true
		res.Body = append([]byte(nil), s.prefix...)
	} else {
		res.Reply.M = true
		res.ReplyCode = wire.Continue
	}
	a.record(remote, b, diag.Ignore, "",
		"duplicate block %d at offset %d matches accepted bytes; replaying %s without recommit",
		b.NUM, off, res.ReplyCode)
	return res, nil
}

func (a *Block1Assembler) record(remote string, _ wire.Block, v diag.Verdict, c diag.Category, format string, args ...any) {
	if a.rec != nil {
		m := a.curMeta
		a.rec.Log(remote, m.MID, m.HaveMID, m.Token, v, c, format, args...)
	}
}

func expectedNum(s *block1State, szx uint8) uint32 {
	return uint32(s.frontier >> (szx + 4))
}

func bytesEqual(a, b []byte) bool {
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
