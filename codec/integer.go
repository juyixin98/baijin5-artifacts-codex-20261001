package codec

import "math/bits"

// AppendInteger encodes v as an RFC 7541 Section 5.1 prefixed integer with
// the given prefix width (1..8). The high bits of the first byte are taken
// from flags (the representation tag), the low prefixBits hold the value or
// the all-ones continuation marker.
func AppendInteger(dst []byte, v uint64, prefixBits uint, flags byte) []byte {
	if prefixBits < 1 || prefixBits > 8 {
		panic("codec: prefixBits out of range")
	}
	prefixMax := uint64(1)<<prefixBits - 1
	if v < prefixMax {
		return append(dst, flags|byte(v))
	}
	dst = append(dst, flags|byte(prefixMax))
	v -= prefixMax
	for v >= 0x80 {
		dst = append(dst, byte(v&0x7f)|0x80)
		v >>= 7
	}
	return append(dst, byte(v))
}

// DecodeInteger decodes an RFC 7541 Section 5.1 prefixed integer from buf.
// prefixBits is the width of the prefix in the first byte; the high bits of
// buf[0] are ignored. It returns the value and the number of bytes consumed.
//
// Failures: ErrTruncated if the continuation runs past the end of buf,
// ErrIntegerOverflow if the value does not fit in 64 bits.
func DecodeInteger(buf []byte, prefixBits uint) (v uint64, n int, err error) {
	if prefixBits < 1 || prefixBits > 8 {
		panic("codec: prefixBits out of range")
	}
	if len(buf) == 0 {
		return 0, 0, ErrTruncated
	}
	prefixMax := uint64(1)<<prefixBits - 1
	v = uint64(buf[0]) & prefixMax
	if v < prefixMax {
		return v, 1, nil
	}
	// Continuation bytes: 7 bits each, little-endian groups.
	shift := uint(0)
	for i := 1; i < len(buf); i++ {
		b := buf[i]
		chunk := uint64(b & 0x7f)
		if chunk != 0 {
			if shift >= 64 || bits.Len64(chunk)+int(shift) > 64 {
				return 0, 0, ErrIntegerOverflow
			}
			add := chunk << shift
			if add > ^uint64(0)-v {
				return 0, 0, ErrIntegerOverflow
			}
			v += add
		}
		shift += 7
		if b&0x80 == 0 {
			return v, i + 1, nil
		}
	}
	return 0, 0, ErrTruncated
}
