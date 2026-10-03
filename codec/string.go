package codec

// String literals per RFC 7541 Section 5.2: an H bit (0x80 of the first
// byte), a 7-bit-prefix length in raw bytes, then the payload, optionally
// Huffman-coded.

const huffmanFlag = 0x80

// AppendString encodes s as a string literal. If huffman is true and the
// Huffman encoding is strictly shorter than the raw bytes, the Huffman
// form is used; otherwise the raw form is emitted.
func AppendString(dst []byte, s string, huffman bool) []byte {
	if huffman {
		if hlen := HuffmanEncodedLen(s); hlen < len(s) {
			dst = AppendInteger(dst, uint64(hlen), 7, huffmanFlag)
			return AppendHuffman(dst, s)
		}
	}
	dst = AppendInteger(dst, uint64(len(s)), 7, 0)
	return append(dst, s...)
}

// DecodeString decodes a string literal from buf, returning the string and
// the number of bytes consumed. maxLen bounds both the declared payload
// length and the decoded length; zero means unbounded.
//
// Failures: ErrTruncated, ErrIntegerOverflow (declared length),
// ErrStringTooLong, ErrHuffmanEOS, ErrHuffmanPadding.
func DecodeString(buf []byte, maxLen int) (string, int, error) {
	if len(buf) == 0 {
		return "", 0, ErrTruncated
	}
	huffman := buf[0]&huffmanFlag != 0
	n, used, err := DecodeInteger(buf, 7)
	if err != nil {
		return "", 0, err
	}
	if maxLen > 0 && n > uint64(maxLen) {
		return "", 0, ErrStringTooLong
	}
	if uint64(len(buf)-used) < n {
		return "", 0, ErrTruncated
	}
	payload := buf[used : used+int(n)]
	if !huffman {
		return string(payload), used + int(n), nil
	}
	s, err := DecodeHuffman(payload)
	if err != nil {
		return "", 0, err
	}
	if maxLen > 0 && len(s) > maxLen {
		return "", 0, ErrStringTooLong
	}
	return s, used + int(n), nil
}
