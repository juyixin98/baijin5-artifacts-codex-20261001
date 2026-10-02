package codec

// Integer represents an RFC 7541 section 5.1 prefix integer.
type Integer = uint64

// MaxIntegerValue is the largest legal integer value. RFC 7541 section 5.1
// requires decoders to treat any value greater than 2^62-1 as a decoding
// error.
const MaxIntegerValue = uint64(1)<<62 - 1

// AppendInteger encodes value into dst using an n-bit prefix (1 <= n <= 8)
// with the given leading byte. The non-prefix bits of lead are preserved and
// only its low n bits are (re)written. It returns the extended buffer.
func AppendInteger(dst []byte, lead byte, n uint, value uint64) []byte {
	prefix := uint64(1)<<n - 1
	if value < prefix {
		return append(dst, lead&^byte(prefix)|byte(value))
	}
	dst = append(dst, lead|byte(prefix))
	value -= prefix
	for value >= 128 {
		dst = append(dst, byte(value%128)+128)
		value /= 128
	}
	return append(dst, byte(value))
}

// ConsumeInteger decodes an n-bit prefix integer.
//
// lead is the representation's first octet (already read by the caller's
// state machine); rest is the unread tail after it. It returns the decoded
// value, the unconsumed remainder of rest, and an error when the integer is
// truncated or exceeds MaxIntegerValue.
func ConsumeInteger(lead byte, rest []byte, n uint) (value uint64, out []byte, err error) {
	prefix := uint64(1)<<n - 1
	value = uint64(lead) & prefix
	if value < prefix {
		return value, rest, nil
	}
	var shift uint
	for i := 0; ; i++ {
		if i >= len(rest) {
			return 0, nil, ErrTruncatedInteger
		}
		b := uint64(rest[i]) & 0x7f
		// Guard the shift itself and the RFC 7541 value bound.
		if shift >= 63 {
			return 0, nil, ErrIntegerOverflow
		}
		add := b << shift
		if add > MaxIntegerValue-value || value+add > MaxIntegerValue {
			return 0, nil, ErrIntegerOverflow
		}
		value += add
		if rest[i]&0x80 == 0 {
			return value, rest[i+1:], nil
		}
		shift += 7
	}
}
