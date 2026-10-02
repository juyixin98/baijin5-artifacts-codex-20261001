package ber

// Class is the ASN.1 tag class.
type Class int

const (
	Universal   Class = 0
	Application Class = 1
	Context     Class = 2
	Private     Class = 3
)

func (c Class) String() string {
	switch c {
	case Universal:
		return "universal"
	case Application:
		return "application"
	case Context:
		return "context"
	case Private:
		return "private"
	}
	return "unknown"
}

// Universal tag numbers used by this implementation.
const (
	TagEOC       = 0
	TagInteger   = 2
	TagBitString = 3
	TagSequence  = 16
)

// tag describes a parsed identifier octet sequence.
type tag struct {
	class       Class
	constructed bool
	number      uint64
	size        int // octets consumed, including the leading identifier octet
}

// parseTag reads the identifier octets starting at data[off].
//
// Rules enforced (X.690 8.1.2):
//   - long form (tag number >= 31) uses at most lim.MaxTagBytes subsequent
//     octets (resource limit);
//   - the first subsequent octet of a long-form tag must not be 0x80
//     (X.690 8.1.2.4.2: leading zero groups are not permitted);
//   - tag number 0 in long form is rejected: EOC is exactly the octet 0x00.
func parseTag(data []byte, off int, lim Limits) (tag, *Error) {
	if off >= len(data) {
		return tag{}, errf(CatTruncation, off, "identifier octet missing")
	}
	b := data[off]
	t := tag{
		class:       Class(b >> 6),
		constructed: b&0x20 != 0,
		number:      uint64(b & 0x1f),
		size:        1,
	}
	if t.number < 0x1f {
		return t, nil
	}
	// Long form: base-128 tag number in subsequent octets.
	t.number = 0
	for i := 1; ; i++ {
		if i > lim.MaxTagBytes {
			return tag{}, errf(CatResource, off,
				"long-form tag exceeds %d subsequent octets", lim.MaxTagBytes)
		}
		if off+i >= len(data) {
			return tag{}, errf(CatTruncation, len(data),
				"long-form tag truncated after %d subsequent octets", i-1)
		}
		sb := data[off+i]
		if i == 1 && sb == 0x80 {
			return tag{}, errf(CatSyntax, off+i,
				"long-form tag has leading zero group (0x80)")
		}
		if t.number > (1<<57)-1 { // guard: keep number well inside uint64
			return tag{}, errf(CatResource, off, "tag number overflow")
		}
		t.number = t.number<<7 | uint64(sb&0x7f)
		t.size = i + 1
		if sb&0x80 == 0 {
			break
		}
	}
	if t.number < 0x1f {
		// Numbers 0..30 must use the short form; number 0 long-form is an
		// EOC impostor, the rest are non-minimal encodings.
		return tag{}, errf(CatSyntax, off,
			"tag number %d must use short-form identifier", t.number)
	}
	return t, nil
}
