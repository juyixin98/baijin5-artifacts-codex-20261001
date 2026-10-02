package vectors

import "fmt"

// MustHex decodes a contiguous hexadecimal string (spaces and 0x
// separators tolerated) into a fresh byte slice. It panics on bad input so
// test tables fail loudly at construction time; it never calls into any
// code under test.
func MustHex(s string) []byte {
	b := make([]byte, 0, len(s)/2)
	var hi byte = 0xff
	flush := func(c byte) {
		var v byte
		switch {
		case c >= '0' && c <= '9':
			v = c - '0'
		case c >= 'a' && c <= 'f':
			v = c - 'a' + 10
		case c >= 'A' && c <= 'F':
			v = c - 'A' + 10
		default:
			panic(fmt.Sprintf("vectors: invalid hex digit %q in %q", c, s))
		}
		if hi == 0xff {
			hi = v
			return
		}
		b = append(b, hi<<4|v)
		hi = 0xff
	}
	for i := 0; i < len(s); i++ {
		switch s[i] {
		case ' ', '\t', '\n', 'x', 'X':
			continue
		}
		flush(s[i])
	}
	if hi != 0xff {
		panic(fmt.Sprintf("vectors: odd number of hex digits in %q", s))
	}
	return b
}

// Frame independently assembles one MBAP-wrapped PDU with plain byte
// arithmetic (no dependency on the mbap module under test).
func Frame(txnID, unitID uint16, pdu ...byte) []byte {
	if unitID > 0xff {
		panic(fmt.Sprintf("vectors: unit id %d out of byte range", unitID))
	}
	length := uint16(1 + len(pdu))
	out := make([]byte, 0, 7+len(pdu))
	out = append(out,
		byte(txnID>>8), byte(txnID),
		0x00, 0x00,
		byte(length>>8), byte(length),
		byte(unitID))
	return append(out, pdu...)
}

// ReadHoldingReq builds an FC03 request: addr, quantity.
func ReadHoldingReq(txnID, unitID, addr, qty uint16) []byte {
	return Frame(txnID, unitID, 0x03,
		byte(addr>>8), byte(addr), byte(qty>>8), byte(qty))
}

// ReadHoldingResp builds a well-formed FC03 response independently.
func ReadHoldingResp(txnID, unitID uint16, vals []uint16) []byte {
	pdu := make([]byte, 0, 2+2*len(vals))
	pdu = append(pdu, 0x03, byte(2*len(vals)))
	for _, v := range vals {
		pdu = append(pdu, byte(v>>8), byte(v))
	}
	return Frame(txnID, unitID, pdu...)
}

// WriteMultiReq builds an FC16 request. byteCountOverride < 0 selects the
// correct 2*len(vals) value; passing a different value plus an independently
// supplied data slice lets tests craft inconsistent frames.
func WriteMultiReq(txnID, unitID, addr uint16, vals []uint16, byteCountOverride int) []byte {
	bc := 2 * len(vals)
	if byteCountOverride >= 0 {
		bc = byteCountOverride
	}
	pdu := make([]byte, 0, 6+2*len(vals))
	pdu = append(pdu, 0x10,
		byte(addr>>8), byte(addr),
		byte(len(vals)>>8), byte(len(vals)),
		byte(bc))
	for _, v := range vals {
		pdu = append(pdu, byte(v>>8), byte(v))
	}
	return Frame(txnID, unitID, pdu...)
}

// WriteMultiResp builds the FC16 echo response (starting address + quantity
// only — the data bytes are deliberately absent).
func WriteMultiResp(txnID, unitID, addr, qty uint16) []byte {
	return Frame(txnID, unitID, 0x10,
		byte(addr>>8), byte(addr), byte(qty>>8), byte(qty))
}

// ExceptionResp builds an exception response: function code with bit 7 set
// plus the exception code.
func ExceptionResp(txnID uint16, unitID, fc, exception byte) []byte {
	return Frame(txnID, uint16(unitID), fc|0x80, exception)
}
