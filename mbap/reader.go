package mbap

import (
	"bufio"
	"errors"
	"io"
)

// ErrStreamClosed wraps io.EOF-style conditions from Reader for callers that
// want to distinguish a clean peer shutdown from a protocol violation.
var ErrStreamClosed = errors.New("mbap: stream closed before a complete frame arrived")

// Reader extracts whole Modbus TCP frames from a byte stream, tolerating
// partial reads (half packets): the length field drives exactly how many
// body bytes are consumed. One Reader serializes access to one connection;
// concurrent requests on one connection must not share it without their own
// locking — Modbus clients multiplex by transaction id over a single
// connection and correlate replies themselves.
type Reader struct {
	buf *bufio.Reader
	hdr [HeaderLen]byte

	// partialKind/partial are set right before ErrStreamClosed is returned
	// when the stream ended in the middle of a frame. A clean immediate
	// EOF with no bytes consumed leaves them zero/false, so callers can
	// tell a peer that simply vanished from a truncated-frame attack.
	partialKind Kind
	partial     bool
}

// PartialFrame returns (header_truncated|body_truncated, true) if the last
// ReadFrame failed with ErrStreamClosed after consuming some bytes of a
// frame, or ("", false) on a clean close with nothing in flight.
func (r *Reader) PartialFrame() (Kind, bool) {
	return r.partialKind, r.partial
}

// NewReader wraps r. The buffer holds at least one maximum-sized frame so a
// pipelined second frame's header survives the read of the first.
func NewReader(r io.Reader) *Reader {
	return &Reader{buf: bufio.NewReaderSize(r, HeaderLen+MaxLength)}
}

// partialHeader reconstructs whatever header fields are available from a
// prefix of 1..6 header bytes; missing fields stay zero.
func partialHeader(b []byte) Header {
	var h Header
	if len(b) >= 2 {
		h.TxnID = uint16(b[0])<<8 | uint16(b[1])
	}
	if len(b) >= 4 {
		h.ProtocolID = uint16(b[2])<<8 | uint16(b[3])
	}
	if len(b) >= 6 {
		h.Length = uint16(b[4])<<8 | uint16(b[5])
	}
	if len(b) >= 7 {
		h.UnitID = b[6]
	}
	return h
}

// ReadFrame blocks until one complete frame is available. The returned
// frame is valid only until the next ReadFrame call; copy it if it must
// outlive the call. On a truncated stream it returns ErrStreamClosed.
func (r *Reader) ReadFrame() (Header, []byte, error) {
	r.partialKind, r.partial = "", false

	got := 0
	for got < HeaderLen {
		n, err := r.buf.Read(r.hdr[got:])
		got += n
		if err != nil {
			if errors.Is(err, io.EOF) {
				var partialH Header
				if got > 0 {
					// Some header bytes arrived before EOF: a half packet.
					r.partialKind, r.partial = KindHeaderTruncated, true
					partialH = partialHeader(r.hdr[:got])
				}
				return partialH, nil, ErrStreamClosed
			}
			return Header{}, nil, err
		}
	}
	length := uint16(r.hdr[4])<<8 | uint16(r.hdr[5])
	if length < MinLength || length > MaxLength {
		// The body position is undefined for an illegal length; surface the
		// error and let the caller close the connection.
		h := Header{
			TxnID:      uint16(r.hdr[0])<<8 | uint16(r.hdr[1]),
			ProtocolID: uint16(r.hdr[2])<<8 | uint16(r.hdr[3]),
			Length:     length,
			UnitID:     r.hdr[6],
		}
		kind := KindLengthTooSmall
		detail := "length field " + itoaLen(int(length)) + " is below 2"
		if length > MaxLength {
			kind = KindLengthTooLarge
			detail = "length field " + itoaLen(int(length)) + " exceeds 254"
		}
		return h, nil, &FrameError{Kind: kind, Detail: detail}
	}

	frame := make([]byte, HeaderLen+int(length)-1)
	copy(frame, r.hdr[:])
	if _, err := io.ReadFull(r.buf, frame[HeaderLen:]); err != nil {
		h := Header{
			TxnID:      uint16(r.hdr[0])<<8 | uint16(r.hdr[1]),
			ProtocolID: uint16(r.hdr[2])<<8 | uint16(r.hdr[3]),
			Length:     length,
			UnitID:     r.hdr[6],
		}
		if errors.Is(err, io.EOF) || errors.Is(err, io.ErrUnexpectedEOF) {
			// Header was complete and valid; the PDU bytes ran short.
			r.partialKind, r.partial = KindBodyTruncated, true
		}
		return h, nil, ErrStreamClosed
	}
	h, pdu, err := Decode(frame)
	if err != nil {
		return h, nil, err
	}
	return h, pdu, nil
}
