package codec

// String is a decoded HPACK literal string (RFC 7541 section 5.2).
type String struct {
	Data    []byte
	Huffman bool
}

// AppendString appends an HPACK string representation. When huff is true the
// content is Huffman encoded and the H bit is set on the length prefix.
func AppendString(dst []byte, s []byte, huff bool) []byte {
	if huff {
		encoded := AppendHuffman(make([]byte, 0, HuffmanEncodedLen(s)), s)
		dst = AppendInteger(dst, 0x80, 7, uint64(len(encoded)))
		return append(dst, encoded...)
	}
	dst = AppendInteger(dst, 0x00, 7, uint64(len(s)))
	return append(dst, s...)
}

// ConsumeString reads one HPACK string.
//
// lead is the representation's first octet (the H flag lives in its top
// bit); rest is the unread tail. maxLen bounds the decompressed/raw content
// length in bytes (0 disables the limit). It returns the string, whether
// Huffman was used, the unconsumed remainder, and an error.
func ConsumeString(lead byte, rest []byte, maxLen int) (s String, out []byte, err error) {
	huff := lead&0x80 != 0
	length, tail, err := ConsumeInteger(lead, rest, 7)
	if err != nil {
		return String{}, nil, err
	}
	if length > uint64(len(tail)) {
		return String{}, nil, ErrTruncatedString
	}
	payload := tail[:length]
	if huff {
		// Huffman codes run 5..30 bits, so output length cannot be inferred
		// from wire length (either direction); the decoder enforces the
		// post-decompression bound directly.
		data, derr := DecodeHuffman(payload, maxLen)
		if derr != nil {
			return String{}, nil, derr
		}
		return String{Data: data, Huffman: true}, tail[length:], nil
	}
	if maxLen > 0 && length > uint64(maxLen) {
		return String{}, nil, ErrStringLengthLimit
	}
	data := make([]byte, length)
	copy(data, payload)
	return String{Data: data, Huffman: false}, tail[length:], nil
}
