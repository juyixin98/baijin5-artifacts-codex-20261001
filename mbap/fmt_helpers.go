package mbap

import "strconv"

// Small local int/hex helpers keep allocations out of error formatting and
// avoid pulling fmt into the hot encode/decode paths.
func itoaLen(n int) string { return strconv.Itoa(n) }

func hex16(v uint16) string {
	const digits = "0123456789ABCDEF"
	b := [4]byte{
		digits[(v>>12)&0xF],
		digits[(v>>8)&0xF],
		digits[(v>>4)&0xF],
		digits[v&0xF],
	}
	return string(b[:])
}
