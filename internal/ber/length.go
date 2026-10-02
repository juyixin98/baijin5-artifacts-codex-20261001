package ber

// length describes a parsed length field.
type length struct {
	indefinite bool
	n          int  // content octets (meaningless when indefinite)
	size       int  // octets consumed by the length field itself
	minimal    bool // false when a long form was used where short form sufficed
}

// parseLength reads the length octets starting at data[off] (X.690 8.1.3).
//
// Rules enforced:
//   - 0xFF (length-of-length 127) is reserved and rejected;
//   - length-of-length is capped at lim.MaxLengthBytes (resource limit);
//   - the content length must fit in an int and must not exceed
//     lim.MaxInputBytes (resource limit).
func parseLength(data []byte, off int, lim Limits) (length, *Error) {
	if off >= len(data) {
		return length{}, errf(CatTruncation, off, "length octet missing")
	}
	b := data[off]
	if b < 0x80 {
		return length{n: int(b), size: 1, minimal: true}, nil
	}
	if b == 0x80 {
		return length{indefinite: true, size: 1, minimal: true}, nil
	}
	nlen := int(b & 0x7f)
	if nlen == 0x7f {
		return length{}, errf(CatSyntax, off, "length-of-length 0x7f is reserved")
	}
	if nlen > lim.MaxLengthBytes {
		return length{}, errf(CatResource, off,
			"length-of-length %d exceeds limit %d", nlen, lim.MaxLengthBytes)
	}
	if off+1+nlen > len(data) {
		return length{}, errf(CatTruncation, len(data),
			"long-form length truncated: need %d octets, have %d",
			nlen, len(data)-off-1)
	}
	var v uint64
	for i := 0; i < nlen; i++ {
		v = v<<8 | uint64(data[off+1+i])
	}
	maxInt := uint64(^uint(0) >> 1)
	if v > maxInt {
		return length{}, errf(CatResource, off, "length %d overflows int", v)
	}
	if v > uint64(lim.MaxInputBytes) {
		return length{}, errf(CatResource, off,
			"content length %d exceeds limit %d", v, lim.MaxInputBytes)
	}
	return length{
		n:       int(v),
		size:    1 + nlen,
		minimal: nlen == 1 && v >= 0x80 || nlen > 1 && data[off+1] != 0,
	}, nil
}
