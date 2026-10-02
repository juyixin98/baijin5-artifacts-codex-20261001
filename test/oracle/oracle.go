// Package oracle is the INDEPENDENT test oracle. Nothing here imports the
// implementation under test (no coaplab/internal/blocks or wire helpers
// for its verdicts): it parses datagrams from raw bytes with its own
// minimal reader, reassembles blocks with its own logic, and checks bodies
// against (a) a hard-coded golden hash and (b) a separately written copy of
// the fixture byte formula. If the SUT were wrong, this package would still
// state the correct answer.
package oracle

import (
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"errors"
	"fmt"
)

// GoldenSHA256 is the independently computed hash of the "hello" fixture.
// Computed with the system sha256sum tool, hard-coded here:
//
//	printf 'Hello, CoAP!' | sha256sum
const GoldenSHA256 = "936ba7a8d914df425cf608ea5095b9ece845df46ab3c61d55ef6debcad7845c3"

// HelloBody is the exact expected hello representation.
var HelloBody = []byte("Hello, CoAP!")

// ExpectedBody independently regenerates the deterministic cd/* fixture
// body. This is a SEPARATE implementation of the documented formula
// (sha256("coaplab/<path>:<counter>") concatenated, truncated to n bytes) —
// duplicated on purpose so a bug in the fixture generator cannot also
// corrupt the expected answer.
func ExpectedBody(path string, n int) []byte {
	out := make([]byte, 0, n)
	for counter := uint64(0); len(out) < n; counter++ {
		h := sha256.Sum256([]byte(fmt.Sprintf("coaplab/%s:%d", path, counter)))
		out = append(out, h[:]...)
	}
	return out[:n]
}

// SHA256Hex returns the lowercase hex SHA-256 of b.
func SHA256Hex(b []byte) string {
	h := sha256.Sum256(b)
	return hex.EncodeToString(h[:])
}

// VerifyHello asserts the exact golden bytes and the hard-coded hash.
func VerifyHello(b []byte) error {
	if string(b) != string(HelloBody) {
		return fmt.Errorf("hello bytes = %q, want %q", b, HelloBody)
	}
	if got := SHA256Hex(b); got != GoldenSHA256 {
		return fmt.Errorf("hello sha256 = %s, want golden %s", got, GoldenSHA256)
	}
	return nil
}

// ---- Independent raw datagram inspection ----

// MessageView is the oracle's own decode of the fields tests care about.
type MessageView struct {
	Version   uint8
	Type      uint8 // 0 CON,1 NON,2 ACK,3 RST
	Code      uint8
	MID       uint16
	Token     []byte
	BlockOpt  *BlockView
	ETag      []byte
	HasETag   bool
	Payload   []byte
	RawCodeOK bool
}

// BlockView is an independently decoded Block1/Block2 option.
type BlockView struct {
	Number int // option number 23 or 27
	NUM    uint32
	M      bool
	SZX    uint8
	Size   int
}

// Inspect parses a CoAP datagram using only this package's reader.
func Inspect(dgram []byte) (MessageView, error) {
	if len(dgram) < 4 {
		return MessageView{}, errors.New("oracle: datagram shorter than 4 bytes")
	}
	v := MessageView{}
	v.Version = dgram[0] >> 6
	if v.Version != 1 {
		return v, fmt.Errorf("oracle: version %d", v.Version)
	}
	v.Type = (dgram[0] >> 4) & 0x3
	tkl := int(dgram[0] & 0x0f)
	v.Code = dgram[1]
	v.MID = binary.BigEndian.Uint16(dgram[2:4])
	p := 4
	if p+tkl > len(dgram) {
		return v, errors.New("oracle: TKL overruns")
	}
	v.Token = append([]byte(nil), dgram[p:p+tkl]...)
	p += tkl

	prevNum := 0
	for p < len(dgram) {
		if dgram[p] == 0xFF {
			p++
			v.Payload = append([]byte(nil), dgram[p:]...)
			break
		}
		first := dgram[p]
		p++
		d := int(first >> 4)
		l := int(first & 0x0f)
		if d == 15 || l == 15 {
			return v, errors.New("oracle: reserved nibble 15")
		}
		if d == 13 {
			if p >= len(dgram) {
				return v, errors.New("oracle: truncated delta ext")
			}
			d = int(dgram[p]) + 13
			p++
		} else if d == 14 {
			if p+1 >= len(dgram) {
				return v, errors.New("oracle: truncated delta ext16")
			}
			d = int(binary.BigEndian.Uint16(dgram[p:p+2])) + 269
			p += 2
		}
		if l == 13 {
			if p >= len(dgram) {
				return v, errors.New("oracle: truncated len ext")
			}
			l = int(dgram[p]) + 13
			p++
		} else if l == 14 {
			if p+1 >= len(dgram) {
				return v, errors.New("oracle: truncated len ext16")
			}
			l = int(binary.BigEndian.Uint16(dgram[p:p+2])) + 269
			p += 2
		}
		if p+l > len(dgram) {
			return v, errors.New("oracle: option value overruns")
		}
		num := prevNum + d
		val := dgram[p : p+l]
		p += l
		prevNum = num
		switch num {
		case 4:
			v.ETag = append([]byte(nil), val...)
			v.HasETag = true
		case 23, 27:
			bv, err := decodeBlock(num, val)
			if err != nil {
				return v, err
			}
			v.BlockOpt = &bv
		}
	}
	return v, nil
}

func decodeBlock(num int, val []byte) (BlockView, error) {
	if len(val) > 3 {
		return BlockView{}, fmt.Errorf("oracle: block option %d bytes", len(val))
	}
	var x uint32
	for _, c := range val {
		x = x<<8 | uint32(c)
	}
	bv := BlockView{
		Number: num,
		NUM:    x >> 4,
		M:      x&8 != 0,
		SZX:    uint8(x & 7),
	}
	if bv.SZX == 7 {
		return bv, errors.New("oracle: reserved SZX 7")
	}
	bv.Size = 1 << (bv.SZX + 4)
	return bv, nil
}

// ---- Independent Block2 reassembly ----

// ReferenceReassembler is the oracle's from-scratch download reassembler.
type ReferenceReassembler struct {
	blocks   map[uint32][]byte
	etag     []byte
	szx      uint8
	tail     uint32
	haveTail bool
}

func NewReferenceReassembler() *ReferenceReassembler {
	return &ReferenceReassembler{blocks: map[uint32][]byte{}}
}

// Add inserts one block independently. An ETag change is fatal.
func (r *ReferenceReassembler) Add(num uint32, m bool, szx uint8, payload, et []byte) error {
	if len(r.blocks) == 0 {
		r.szx = szx
		r.etag = append([]byte(nil), et...)
	} else {
		if !equalBytes(r.etag, et) {
			return fmt.Errorf("oracle: etag changed %x -> %x at block %d", r.etag, et, num)
		}
		if szx != r.szx {
			return fmt.Errorf("oracle: size changed %d -> %d at block %d", r.szx, szx, num)
		}
	}
	if existing, ok := r.blocks[num]; ok {
		if !equalBytes(existing, payload) {
			return fmt.Errorf("oracle: block %d duplicated with different bytes", num)
		}
		return nil // idempotent duplicate
	}
	r.blocks[num] = append([]byte(nil), payload...)
	if !m {
		if !r.haveTail || num > r.tail {
			r.tail, r.haveTail = num, true
		}
	}
	return nil
}

// Complete reports whether 0..tail are all present.
func (r *ReferenceReassembler) Complete() bool {
	if !r.haveTail {
		return false
	}
	for n := uint32(0); n <= r.tail; n++ {
		if _, ok := r.blocks[n]; !ok {
			return false
		}
	}
	return true
}

// Body concatenates blocks in number order.
func (r *ReferenceReassembler) Body() []byte {
	if !r.haveTail {
		return nil
	}
	var out []byte
	for n := uint32(0); n <= r.tail; n++ {
		out = append(out, r.blocks[n]...)
	}
	return out
}

func equalBytes(a, b []byte) bool {
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
