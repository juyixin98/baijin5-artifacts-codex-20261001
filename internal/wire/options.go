package wire

// Option numbers (RFC 7252 §12.2, RFC 7959 Table 1).
const (
	OpIfMatch       = 1
	OpURIHost       = 3
	OpETag          = 4
	OpIfNoneMatch   = 5
	OpURIQuery      = 15
	OpBlock2        = 23 // RFC 7959
	OpBlock1        = 27 // RFC 7959
	OpSize2         = 28
	OpURIPort       = 7
	OpLocationPath  = 8
	OpURIPath       = 11
	OpContentFormat = 12
	OpMaxAge        = 14
	OpSize1         = 60
)

// Content formats (RFC 7252 §12.3).
const (
	CFTextPlain   = 0
	CFOctetStream = 42
	CFJSON        = 50
	CFCBOR        = 60
)

// MinBlockPayload / MaxBlockPayload bound SZX 0..6 (RFC 7959 §2.2).
const (
	MinBlockPayload = 16
	MaxBlockPayload = 1024
)

// Option is one option instance: options are ordered and may repeat.
type Option struct {
	Number int
	Value  []byte
}

// EncodeUint / DecodeUint are the unsigned integer option value format
// (RFC 7252 §3.2): big-endian, stripped of leading zero bytes; zero is
// represented by a zero-length value.
func EncodeUint(n uint64) []byte {
	if n == 0 {
		return []byte{}
	}
	var b [8]byte
	i := len(b)
	for n > 0 {
		i--
		b[i] = byte(n)
		n >>= 8
	}
	return append([]byte(nil), b[i:]...)
}

func DecodeUint(b []byte) uint64 {
	var n uint64
	for _, c := range b {
		n = n<<8 | uint64(c)
	}
	return n
}

// Block is a parsed Block1/Block2 option value (NUM/M/SZX, RFC 7959 §2.2).
type Block struct {
	NUM uint32 // block sequence number
	M   bool   // more flag
	SZX uint8  // size exponent; block size = 1 << (SZX + 4)
}

// Size returns 2**(SZX+4); for the reserved SZX 7 it returns 0.
func (b Block) Size() int {
	if b.SZX > 6 {
		return 0
	}
	return 1 << (b.SZX + 4)
}

// Offset returns the byte offset of NUM within the full body: NUM<<(SZX+4).
func (b Block) Offset() int64 {
	return int64(b.NUM) << (b.SZX + 4)
}

// Encode renders the 0..3 byte block option value.
func (b Block) Encode() []byte {
	v := uint64(b.NUM)<<4 | uint64(b.SZX&0x7)
	if b.M {
		v |= 8
	}
	return EncodeUint(v)
}

// DecodeBlock parses a block option value. It returns an error when SZX is
// the reserved value 7 or when NUM does not fit 20 bits (0..3 bytes).
func DecodeBlock(raw []byte) (Block, error) {
	if len(raw) > 3 {
		return Block{}, &ParseError{Reason: "block option longer than 3 bytes", Raw: raw}
	}
	v := DecodeUint(raw)
	b := Block{
		NUM: uint32(v >> 4),
		M:   v&8 != 0,
		SZX: uint8(v & 7),
	}
	if b.SZX == 7 {
		return Block{}, &ParseError{Reason: "reserved SZX 7 (2048 bytes)", Raw: raw}
	}
	return b, nil
}

// ParseError is returned for malformed option values.
type ParseError struct {
	Reason string
	Raw    []byte
}

func (e *ParseError) Error() string { return "wire: " + e.Reason }
