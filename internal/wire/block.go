package wire

import (
	"fmt"
)

// MinBlockSize / MaxBlockSize bound the powers-of-two block sizes defined by
// RFC 7959: SZX 0..6 maps to 16..1024 bytes.
const (
	MinBlockSize = 16
	MaxBlockSize = 1024
)

// Block is the decoded value of a Block1 or Block2 option.
type Block struct {
	Num  int  // block number within the body
	More bool // more blocks follow
	SZX  int  // block size exponent; size == 1 << (SZX + 4)
}

// Size returns the payload size implied by SZX (16..1024 bytes).
func (b Block) Size() int { return 1 << uint(b.SZX+4) }

// Offset returns the byte offset at which this block starts.
func (b Block) Offset() int { return b.Num * b.Size() }

// String renders e.g. "num=3/more/szx=2(size=32)" for diagnostics.
func (b Block) String() string {
	m := "last"
	if b.More {
		m = "more"
	}
	return fmt.Sprintf("num=%d/%s/szx=%d(size=%d)", b.Num, m, b.SZX, b.Size())
}

// SZXFor returns the SZX exponent for a requested block size, rounding down to
// the nearest legal power of two. Sizes outside [16,1024] clamp to the ends.
func SZXFor(size int) int {
	switch {
	case size <= MinBlockSize:
		return 0
	case size >= MaxBlockSize:
		return 6
	}
	szx := 0
	for (1 << uint(szx+5)) <= size {
		szx++
	}
	return szx
}

// EncodeBlock serializes a Block to its option value (1..3 bytes).
func EncodeBlock(b Block) ([]byte, error) {
	if b.SZX < 0 || b.SZX > 6 {
		return nil, fmt.Errorf("%w: szx %d out of range 0..6", ErrBlockOption, b.SZX)
	}
	if b.Num < 0 {
		return nil, fmt.Errorf("%w: negative block num %d", ErrBlockOption, b.Num)
	}
	m := 0
	if b.More {
		m = 1
	}
	tail := byte(m<<3 | b.SZX&0x7)
	switch {
	case b.Num <= 0xf:
		return []byte{byte(b.Num)<<4 | tail}, nil
	case b.Num <= 0xfff:
		v := uint(b.Num<<4) | uint(tail)
		return []byte{byte(v >> 8), byte(v)}, nil
	case b.Num <= 0xfffff:
		v := uint(b.Num<<4) | uint(tail)
		return []byte{byte(v >> 16), byte(v >> 8), byte(v)}, nil
	default:
		return nil, fmt.Errorf("%w: block num %d exceeds 20 bits",
			ErrBlockOption, b.Num)
	}
}

// DecodeBlock parses a Block1/Block2 option value.
func DecodeBlock(raw []byte) (Block, error) {
	switch len(raw) {
	case 0:
		return Block{}, fmt.Errorf("%w: empty option", ErrBlockOption)
	case 1:
		v := int(raw[0])
		return blockFrom(v >> 4, v&0x8 != 0, v&0x7)
	case 2:
		v := int(raw[0])<<8 | int(raw[1])
		return blockFrom(v>>4, v&0x8 != 0, v&0x7)
	case 3:
		v := int(raw[0])<<16 | int(raw[1])<<8 | int(raw[2])
		return blockFrom(v>>4, v&0x8 != 0, v&0x7)
	default:
		return Block{}, fmt.Errorf("%w: option %d bytes, want 1..3",
			ErrBlockOption, len(raw))
	}
}

func blockFrom(num int, more bool, szx int) (Block, error) {
	if szx == 7 {
		return Block{}, fmt.Errorf("%w: reserved szx=7", ErrBlockOption)
	}
	return Block{Num: num, More: more, SZX: szx}, nil
}

// BlockOption is a convenience accessor used by protocol code for Block1.
func (m *Message) BlockOption(number int) (Block, bool, error) {
	raw, ok := m.Options.Lookup(number)
	if !ok {
		return Block{}, false, nil
	}
	b, err := DecodeBlock(raw)
	if err != nil {
		return Block{}, true, err
	}
	return b, true, nil
}

// AddBlockOption serializes and attaches a Block1/Block2 option.
func (m *Message) AddBlockOption(number int, b Block) error {
	raw, err := EncodeBlock(b)
	if err != nil {
		return err
	}
	m.Options = m.Options.Add(number, raw)
	return nil
}
