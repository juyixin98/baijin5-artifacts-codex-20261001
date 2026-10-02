package mbap

import "encoding/binary"

// Encode appends one complete Modbus TCP frame (MBAP header + pdu) to dst
// and returns the new slice. Passing nil for dst allocates a fresh frame.
//
// Encode validates that pdu length plus the unit byte fits the length
// field's legal range [MinLength, MaxLength]; an invalid pdu length is a
// programming error and returns nil with an error rather than emitting a
// malformed frame.
func Encode(dst []byte, h Header, pdu []byte) ([]byte, error) {
	bodyLen := 1 + len(pdu)
	if bodyLen < MinLength {
		return nil, &FrameError{Kind: KindLengthTooSmall,
			Detail: "pdu is empty; a function code is required"}
	}
	if bodyLen > MaxLength {
		return nil, &FrameError{Kind: KindLengthTooLarge,
			Detail: itoaLen(len(pdu)) + " pdu bytes exceed the 253-byte PDU limit"}
	}

	out := append(dst, make([]byte, HeaderLen+len(pdu))...)
	base := len(out) - HeaderLen - len(pdu)
	binary.BigEndian.PutUint16(out[base+0:], h.TxnID)
	binary.BigEndian.PutUint16(out[base+2:], h.ProtocolID)
	binary.BigEndian.PutUint16(out[base+4:], uint16(bodyLen))
	out[base+6] = h.UnitID
	copy(out[base+HeaderLen:], pdu)
	return out, nil
}

// Decode parses one complete frame that must already be fully buffered. It
// verifies the protocol id, the length field's range, the consistency of
// frame size with the length field, and rejects trailing bytes so callers
// cannot accidentally interpret two pipelined frames as one.
//
// The returned pdu aliases frame and stays valid as long as frame does.
func Decode(frame []byte) (Header, []byte, error) {
	if len(frame) < HeaderLen {
		return Header{}, nil, &FrameError{Kind: KindHeaderTruncated,
			Detail: "got " + itoaLen(len(frame)) + " bytes, need at least 7"}
	}
	h := Header{
		TxnID:      binary.BigEndian.Uint16(frame[0:]),
		ProtocolID: binary.BigEndian.Uint16(frame[2:]),
		Length:     binary.BigEndian.Uint16(frame[4:]),
		UnitID:     frame[6],
	}
	if h.ProtocolID != 0 {
		return h, nil, &FrameError{Kind: KindWrongProtocol,
			Detail: "protocol id 0x" + hex16(h.ProtocolID) + ", want 0x0000"}
	}
	if h.Length < MinLength {
		return h, nil, &FrameError{Kind: KindLengthTooSmall,
			Detail: "length field " + itoaLen(int(h.Length)) + " is below 2"}
	}
	if h.Length > MaxLength {
		return h, nil, &FrameError{Kind: KindLengthTooLarge,
			Detail: "length field " + itoaLen(int(h.Length)) + " exceeds 254"}
	}
	want := HeaderLen + int(h.Length) - 1
	if len(frame) < want {
		return h, nil, &FrameError{Kind: KindBodyTruncated,
			Detail: "length field announces " + itoaLen(want) +
				" total bytes, got " + itoaLen(len(frame))}
	}
	if len(frame) > want {
		return h, nil, &FrameError{Kind: KindTrailingBytes,
			Detail: "frame holds " + itoaLen(len(frame)) +
				" bytes but length field announces " + itoaLen(want)}
	}
	pdu := frame[HeaderLen:want]
	return h, pdu, nil
}
