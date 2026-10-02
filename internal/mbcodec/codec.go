// Package mbcodec implements the byte-level encoding and decoding of Modbus
// TCP frames: the 7-byte MBAP header, frame reassembly from a stream (which
// transparently handles TCP half-packets and coalesced/"sticky" packets),
// and the PDUs for function codes 0x03 and 0x10 in both directions.
//
// Byte-order contract: every multi-byte field on the wire (MBAP fields,
// addresses, quantities, register values) is big-endian, per the Modbus
// specification. Register values are uint16 in the range [0, 65535].
package mbcodec

import (
	"encoding/binary"
	"errors"
	"fmt"
	"io"

	"mbfixture/internal/mbproto"
)

// MBAPHeaderLen is the fixed size of the MBAP header in bytes.
const MBAPHeaderLen = 7

// Header is a decoded MBAP header.
type Header struct {
	TxID    uint16 // transaction identifier, echoed by the slave
	ProtoID uint16 // protocol identifier, must be 0 for Modbus
	Length  uint16 // number of following bytes = UnitID(1) + PDU
	UnitID  uint8  // unit identifier of the slave
}

// Frame-level validation errors. Each is a distinct sentinel so callers can
// classify the failure category instead of string-matching.
var (
	ErrProtocolID  = errors.New("mbap: protocol id is not 0")
	ErrLengthRange = errors.New("mbap: length field out of valid range")
)

// EncodeHeader serializes an MBAP header into exactly 7 bytes.
func EncodeHeader(h Header) [MBAPHeaderLen]byte {
	var b [MBAPHeaderLen]byte
	binary.BigEndian.PutUint16(b[0:2], h.TxID)
	binary.BigEndian.PutUint16(b[2:4], h.ProtoID)
	binary.BigEndian.PutUint16(b[4:6], h.Length)
	b[6] = h.UnitID
	return b
}

// DecodeHeader parses and validates a 7-byte MBAP header. Protocol ID and
// the Length field range are validated independently so the caller can
// report which check failed.
func DecodeHeader(b []byte) (Header, error) {
	if len(b) < MBAPHeaderLen {
		return Header{}, fmt.Errorf("mbap: header too short: %d bytes", len(b))
	}
	h := Header{
		TxID:    binary.BigEndian.Uint16(b[0:2]),
		ProtoID: binary.BigEndian.Uint16(b[2:4]),
		Length:  binary.BigEndian.Uint16(b[4:6]),
		UnitID:  b[6],
	}
	if h.ProtoID != mbproto.ProtocolIDModbus {
		return h, fmt.Errorf("%w: got %d", ErrProtocolID, h.ProtoID)
	}
	if h.Length < mbproto.MinMBAPLength || h.Length > mbproto.MaxMBAPLength {
		return h, fmt.Errorf("%w: got %d", ErrLengthRange, h.Length)
	}
	return h, nil
}

// Frame is one complete Modbus TCP ADU: header plus PDU (without UnitID).
type Frame struct {
	Header Header
	PDU    []byte
}

// ReadFrame reads exactly one frame from r. It first reads the 7-byte MBAP
// header, validates it, then reads Length-1 PDU bytes. Because it uses
// io.ReadFull, arbitrarily fragmented TCP delivery (half-packets) and
// coalesced delivery of multiple frames (sticky packets) are both handled:
// fragmentation only affects how many Read calls happen underneath, and any
// surplus bytes stay in the stream for the next ReadFrame call.
//
// A header validation failure returns ErrProtocolID or ErrLengthRange; the
// stream position is then unrecoverable and the caller should close the
// connection.
func ReadFrame(r io.Reader) (Frame, error) {
	var hb [MBAPHeaderLen]byte
	if _, err := io.ReadFull(r, hb[:]); err != nil {
		return Frame{}, fmt.Errorf("mbap: reading header: %w", err)
	}
	h, err := DecodeHeader(hb[:])
	if err != nil {
		return Frame{}, err
	}
	pdu := make([]byte, int(h.Length)-1)
	if _, err := io.ReadFull(r, pdu); err != nil {
		return Frame{}, fmt.Errorf("mbap: reading %d pdu bytes: %w", len(pdu), err)
	}
	return Frame{Header: h, PDU: pdu}, nil
}

// WriteFrame serializes header + PDU and writes the complete ADU.
func WriteFrame(w io.Writer, h Header, pdu []byte) error {
	h.Length = uint16(1 + len(pdu))
	hb := EncodeHeader(h)
	if _, err := w.Write(hb[:]); err != nil {
		return err
	}
	_, err := w.Write(pdu)
	return err
}

// ---- Function 0x03: Read Holding Registers ----

// EncodeReadHoldingRequest builds the request PDU: func, addr BE, qty BE.
func EncodeReadHoldingRequest(addr, qty uint16) []byte {
	pdu := make([]byte, 5)
	pdu[0] = mbproto.FuncReadHoldingRegisters
	binary.BigEndian.PutUint16(pdu[1:3], addr)
	binary.BigEndian.PutUint16(pdu[3:5], qty)
	return pdu
}

// DecodeReadHoldingRequest parses a read request PDU.
func DecodeReadHoldingRequest(pdu []byte) (addr, qty uint16, err error) {
	if len(pdu) != 5 || pdu[0] != mbproto.FuncReadHoldingRegisters {
		return 0, 0, fmt.Errorf("fc03: malformed request pdu (len=%d)", len(pdu))
	}
	return binary.BigEndian.Uint16(pdu[1:3]), binary.BigEndian.Uint16(pdu[3:5]), nil
}

// EncodeReadHoldingResponse builds the response PDU: func, byte count, data.
func EncodeReadHoldingResponse(values []uint16) []byte {
	pdu := make([]byte, 2+2*len(values))
	pdu[0] = mbproto.FuncReadHoldingRegisters
	pdu[1] = byte(2 * len(values))
	for i, v := range values {
		binary.BigEndian.PutUint16(pdu[2+2*i:4+2*i], v)
	}
	return pdu
}

// DecodeReadHoldingResponse parses a read response PDU. wantQty is the
// quantity from the originating request; the byte count must match it
// exactly so a response can never be silently applied to the wrong shape.
func DecodeReadHoldingResponse(pdu []byte, wantQty uint16) ([]uint16, error) {
	if len(pdu) < 2 || pdu[0] != mbproto.FuncReadHoldingRegisters {
		return nil, fmt.Errorf("fc03: malformed response pdu (len=%d)", len(pdu))
	}
	byteCount := int(pdu[1])
	if len(pdu) != 2+byteCount {
		return nil, fmt.Errorf("fc03: byte count %d but pdu carries %d data bytes",
			byteCount, len(pdu)-2)
	}
	if byteCount != 2*int(wantQty) {
		return nil, fmt.Errorf("fc03: byte count %d does not match requested qty %d",
			byteCount, wantQty)
	}
	values := make([]uint16, byteCount/2)
	for i := range values {
		values[i] = binary.BigEndian.Uint16(pdu[2+2*i : 4+2*i])
	}
	return values, nil
}

// ---- Function 0x10: Write Multiple Registers ----

// EncodeWriteMultipleRequest builds the write-multiple request PDU.
func EncodeWriteMultipleRequest(addr uint16, values []uint16) []byte {
	pdu := make([]byte, 6+2*len(values))
	pdu[0] = mbproto.FuncWriteMultipleRegisters
	binary.BigEndian.PutUint16(pdu[1:3], addr)
	binary.BigEndian.PutUint16(pdu[3:5], uint16(len(values)))
	pdu[5] = byte(2 * len(values))
	for i, v := range values {
		binary.BigEndian.PutUint16(pdu[6+2*i:8+2*i], v)
	}
	return pdu
}

// DecodeWriteMultipleRequest parses a write-multiple request PDU. The
// quantity field and the byte-count field must agree (byteCount == 2*qty)
// and the PDU must carry exactly that many data bytes; any mismatch is a
// distinct error so the slave can answer ILLEGAL_DATA_VALUE.
func DecodeWriteMultipleRequest(pdu []byte) (addr uint16, values []uint16, err error) {
	if len(pdu) < 6 || pdu[0] != mbproto.FuncWriteMultipleRegisters {
		return 0, nil, fmt.Errorf("fc16: malformed request pdu (len=%d)", len(pdu))
	}
	addr = binary.BigEndian.Uint16(pdu[1:3])
	qty := binary.BigEndian.Uint16(pdu[3:5])
	byteCount := int(pdu[5])
	if byteCount != 2*int(qty) {
		return 0, nil, fmt.Errorf("fc16: byte count %d != 2*qty %d", byteCount, qty)
	}
	if len(pdu) != 6+byteCount {
		return 0, nil, fmt.Errorf("fc16: byte count %d but pdu carries %d data bytes",
			byteCount, len(pdu)-6)
	}
	values = make([]uint16, qty)
	for i := range values {
		values[i] = binary.BigEndian.Uint16(pdu[6+2*i : 8+2*i])
	}
	return addr, values, nil
}

// EncodeWriteMultipleResponse builds the write-multiple response PDU: an
// echo of function, starting address and quantity.
func EncodeWriteMultipleResponse(addr, qty uint16) []byte {
	pdu := make([]byte, 5)
	pdu[0] = mbproto.FuncWriteMultipleRegisters
	binary.BigEndian.PutUint16(pdu[1:3], addr)
	binary.BigEndian.PutUint16(pdu[3:5], qty)
	return pdu
}

// DecodeWriteMultipleResponse parses the write-multiple response PDU.
func DecodeWriteMultipleResponse(pdu []byte) (addr, qty uint16, err error) {
	if len(pdu) != 5 || pdu[0] != mbproto.FuncWriteMultipleRegisters {
		return 0, 0, fmt.Errorf("fc16: malformed response pdu (len=%d)", len(pdu))
	}
	return binary.BigEndian.Uint16(pdu[1:3]), binary.BigEndian.Uint16(pdu[3:5]), nil
}

// ---- Exception responses ----

// EncodeException builds an exception response PDU: func|0x80, code.
func EncodeException(funcCode, excCode uint8) []byte {
	return []byte{funcCode | 0x80, excCode}
}

// DecodeException reports whether pdu is an exception response and, if so,
// returns the original function code and the exception code.
func DecodeException(pdu []byte) (funcCode, excCode uint8, ok bool) {
	if len(pdu) == 2 && pdu[0]&0x80 != 0 {
		return pdu[0] & 0x7F, pdu[1], true
	}
	return 0, 0, false
}
