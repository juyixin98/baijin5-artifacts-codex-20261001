// Package blocks contains the block-wise transfer state machines
// (RFC 7959): block-size negotiation, the Block1 upload assembler
// (server side, strict sequencing) and the Block2 download reassembler
// (client side, out-of-order tolerant). All logic is pure and free of
// I/O so it can be driven byte-by-byte by unit tests and fixtures.
package blocks

import (
	"fmt"

	"coaplab/internal/diag"
	"coaplab/internal/wire"
)

// Failure is a classified protocol failure. Code is the CoAP response a
// server should send; Category is the stable key tests assert on.
type Failure struct {
	Category diag.Category
	Code     wire.Code
	Op       string // short machine-readable operation
	Detail   string
}

func (f *Failure) Error() string {
	return fmt.Sprintf("blocks/%s: %s [%s -> %s]", f.Op, f.Detail, f.Category, f.Code)
}

func fail(op string, c diag.Category, code wire.Code, format string, args ...any) *Failure {
	return &Failure{Op: op, Category: c, Code: code, Detail: fmt.Sprintf(format, args...)}
}

// SZXFor returns the exponent (0..6) for the largest legal block size not
// exceeding want bytes. want is clamped to [16, 1024].
func SZXFor(want int) uint8 {
	if want <= wire.MinBlockPayload {
		return 0
	}
	if want >= wire.MaxBlockPayload {
		return 6
	}
	s := uint8(0)
	for (1<<(s+1+4)) <= want && s < 6 {
		s++
	}
	return s
}

// Negotiate applies RFC 7959: the responder uses the smaller of its
// preference and the requester-proposed exponent.
func Negotiate(requested, preferred uint8) uint8 {
	if preferred < requested {
		return preferred
	}
	return requested
}

// ValidateIncoming checks structural rules common to descriptive blocks:
// SZX legal and, when M is set, payload exactly fills the block (RFC 7959
// §2.3: "The block size implied by SZX MUST match the size of the payload
// in bytes, if the M bit is set").
func ValidateIncoming(b wire.Block, payloadLen int, op string) *Failure {
	if b.SZX > 6 {
		return fail(op, diag.CatBadBlockSize, wire.BadRequest, "reserved SZX 7")
	}
	if b.M && payloadLen != b.Size() {
		return fail(op, diag.CatBadBlockSize, wire.BadRequest,
			"M=1 block %d payload is %d bytes, SZX implies %d", b.NUM, payloadLen, b.Size())
	}
	if !b.M && payloadLen > b.Size() {
		return fail(op, diag.CatBadBlockSize, wire.BadRequest,
			"final block %d payload %d exceeds block size %d", b.NUM, payloadLen, b.Size())
	}
	return nil
}

// ChunkBody enumerates the blocks of a body for a sender at the given
// exponent. Each block carries its wire descriptor and an independent copy.
func ChunkBody(body []byte, szx uint8) []Chunk {
	size := 1 << (szx + 4)
	if len(body) == 0 {
		// A zero-length body is one final, empty block (M=0).
		return []Chunk{{Block: wire.Block{NUM: 0, M: false, SZX: szx}, Payload: []byte{}}}
	}
	n := (len(body) + size - 1) / size
	out := make([]Chunk, 0, n)
	for off, num := 0, uint32(0); off < len(body); off, num = off+size, num+1 {
		end := off + size
		if end > len(body) {
			end = len(body)
		}
		out = append(out, Chunk{
			Block:   wire.Block{NUM: num, M: end < len(body), SZX: szx},
			Payload: append([]byte(nil), body[off:end]...),
		})
	}
	return out
}

// Chunk is one enumerated slice.
type Chunk struct {
	Block   wire.Block
	Payload []byte
}

// Slice returns one block of a body for a Block2 responder, answering with
// the negotiated exponent. M is set while further blocks remain.
func Slice(body []byte, num uint32, szx uint8) (payload []byte, m bool, failure *Failure) {
	if szx > 6 {
		return nil, false, fail("slice", diag.CatBadBlockSize, wire.BadRequest, "reserved SZX 7")
	}
	size := 1 << (szx + 4)
	off := int64(num) << (szx + 4)
	// An empty representation is one final, empty block at NUM 0.
	if len(body) == 0 {
		if num != 0 {
			return nil, false, fail("slice", diag.CatGap, wire.BadRequest,
				"block %d beyond empty body", num)
		}
		return []byte{}, false, nil
	}
	if off >= int64(len(body)) {
		return nil, false, fail("slice", diag.CatGap, wire.BadRequest,
			"block %d offset %d beyond body length %d", num, off, len(body))
	}
	end := off + int64(size)
	if end > int64(len(body)) {
		end = int64(len(body))
	}
	return append([]byte(nil), body[off:end]...), end < int64(len(body)), nil
}
