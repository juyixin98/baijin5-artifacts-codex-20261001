// Package mbap encodes and decodes the MBAP (Modbus Application Protocol)
// framing used by Modbus TCP. It performs no I/O and has no knowledge of
// function codes: it only frames a PDU with a transaction id, protocol id,
// length and unit id.
//
// Wire layout (all integers big-endian):
//
//	0                   1                   2                   3
//	0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
//	+---------------+---------------+----------------------------------+
//	| Transaction ID (2)           | Protocol ID (2, must be 0x0000)  |
//	+---------------+---------------+----------------------------------+
//	| Length (2) = 1 (unit id) + n (PDU bytes)                        |
//	+---------------+---------------+---------------+------------------+
//	| Unit ID (1)   | PDU (n bytes, Length-1) ...                       |
//	+---------------+---------------------------------------------------+
package mbap

// HeaderLen is the fixed MBAP prefix size: transaction id, protocol id,
// length and unit id.
const HeaderLen = 7

// Length field bounds. The length counts the unit id plus the PDU. Every
// Modbus TCP frame carries at least a unit id and a function code, and the
// maximum legal PDU on Modbus TCP is 253 bytes, so Length is in [2,254].
const (
	MinLength    = 2
	MaxLength    = 254
	MaxPDULength = MaxLength - 1
)

// Header is the decoded MBAP prefix (the PDU is carried separately).
type Header struct {
	TxnID      uint16
	ProtocolID uint16
	Length     uint16
	UnitID     byte
}
