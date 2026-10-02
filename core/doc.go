// Package core is the transport-free Modbus protocol state machine for the
// local fixture. It operates purely on PDU bytes and in-memory register
// banks: there is no network, MBAP or SQLite code here, so the exact same
// state machine backs both the TCP server and the unit tests.
//
// Supported function codes:
//
//	0x03 Read Holding Registers   (response: byte count + register data)
//	0x10 Write Multiple Registers (atomic; response echoes addr + qty)
//
// Anything else is answered with a Modbus exception (function code with
// bit 7 set + exception code), never with ordinary data.
//
// Validation order (fixed so the exception category is deterministic):
//
//  1. unit id is bound to a register bank        -> 0x0B (gateway target)
//  2. function code is supported                  -> 0x01 illegal function
//  3. PDU shape: fixed length, quantity 1..125/123,
//     FC16 byte count equals 2*quantity and the frame holds all data
//     -> 0x03 illegal value
//  4. address span lies inside the register bank  -> 0x02 illegal address
//
// Register encoding is big-endian uint16; the valid value range is the
// whole uint16 range (0x0000..0xFFFF), so no per-value rejection exists.
package core

// Function codes.
const (
	FCReadHolding    byte = 0x03
	FCWriteMultiple  byte = 0x10
	ExceptionBit     byte = 0x80
	MaxReadQuantity       = 125 // FC03 limit: response byte count fits one byte
	MaxWriteQuantity      = 123 // FC16 limit: 123*2 + overhead <= 253 PDU bytes
)

// Exception codes (MODBUS Application Protocol v1.1b3, Table 21).
const (
	ExcIllegalFunction byte = 0x01
	ExcIllegalAddress  byte = 0x02
	ExcIllegalValue    byte = 0x03
	ExcServerFailure   byte = 0x04
	// 0x0B "Gateway Target Device Failed To Respond": used only for an
	// unbound unit id, i.e. the addressed slave is absent behind this
	// local gateway fixture.
	ExcGatewayTarget byte = 0x0B
)
