package ber

// appendTag encodes the identifier octets for (class, constructed, number).
// Numbers >= 31 use the minimal long form.
func appendTag(dst []byte, class Class, constructed bool, number uint64) []byte {
	b := byte(class) << 6
	if constructed {
		b |= 0x20
	}
	if number < 0x1f {
		return append(dst, b|byte(number))
	}
	dst = append(dst, b|0x1f)
	// Minimal base-128 encoding of the tag number.
	var tmp [10]byte
	i := len(tmp)
	for {
		i--
		tmp[i] = byte(number & 0x7f)
		number >>= 7
		if number == 0 {
			break
		}
	}
	for j := i; j < len(tmp)-1; j++ {
		tmp[j] |= 0x80
	}
	return append(dst, tmp[i:]...)
}

// appendLength encodes a definite length in the shortest form (X.690 8.1.3.4/
// 8.1.3.5), which is also the DER-required form (X.690 10.1).
func appendLength(dst []byte, n int) []byte {
	if n < 0x80 {
		return append(dst, byte(n))
	}
	var tmp [8]byte
	i := len(tmp)
	for v := n; v > 0; v >>= 8 {
		i--
		tmp[i] = byte(v)
	}
	dst = append(dst, 0x80|byte(len(tmp)-i))
	return append(dst, tmp[i:]...)
}

// EncodeBER re-encodes a node tree using definite lengths throughout. The
// original length form (indefinite or not) is normalised away; use EncodeDER
// when canonical output is required.
func EncodeBER(n *Node) []byte {
	dst := appendTag(nil, n.Class, n.Constructed, n.Tag)
	if !n.Constructed {
		dst = appendLength(dst, len(n.Content))
		return append(dst, n.Content...)
	}
	var body []byte
	for _, c := range n.Children {
		body = EncodeBERInto(body, c)
	}
	dst = appendLength(dst, len(body))
	return append(dst, body...)
}

// EncodeBERInto is EncodeBER with caller-provided buffer.
func EncodeBERInto(dst []byte, n *Node) []byte {
	return append(dst, EncodeBER(n)...)
}
