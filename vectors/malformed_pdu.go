package vectors

// Modbus exception codes (MODBUS Application Protocol v1.1b3, Table 21).
const (
	ExcIllegalFunction = 0x01
	ExcIllegalAddress  = 0x02
	ExcIllegalValue    = 0x03
	ExcServerFailure   = 0x04
)

// PDUCase is an MBAP-well-formed request whose PDU must be rejected with a
// Modbus exception (function code | 0x80 plus the exception code), never
// with ordinary data.
type PDUCase struct {
	Name      string
	Desc      string
	ReqHex    string
	RespHex   string
	Exception byte
}

// The fixture under test is configured with 125 holding registers
// (addresses 0..124), the full Modbus TCP FC03 maximum span.
const (
	FixtureRegisters = 125
	FixtureUnit      = 0x01
)

// PDUMalformed enumerates isolated PDU failures. Each frame is MBAP-legal
// (its length field matches the bytes actually present) so rejection must
// happen at protocol/PDU level, not framing level.
var PDUMalformed = []PDUCase{
	// ---- FC03 read holding registers ----
	{
		Name:      "fc03_quantity_zero",
		Desc:      "read quantity 0 is illegal (valid range 1..125)",
		ReqHex:    "000300000006010300000000",
		RespHex:   "000300000003018303",
		Exception: ExcIllegalValue,
	},
	{
		Name: "fc03_quantity_126",
		Desc: "read quantity 126 (0x007E) exceeds the FC03 maximum " +
			"of 125 registers",
		ReqHex:    "00030000000601030000007e",
		RespHex:   "000300000003018303",
		Exception: ExcIllegalValue,
	},
	{
		Name: "fc03_addr_0001_qty_125_span_overflow",
		Desc: "addr=1 qty=125 reaches address 125, one past the " +
			"125-register bank (0..124)",
		ReqHex:    "00030000000601030001007d",
		RespHex:   "000300000003018302",
		Exception: ExcIllegalAddress,
	},
	{
		Name:      "fc03_addr_125_out_of_bank",
		Desc:      "start address 125 (0x007D) is itself outside the bank",
		ReqHex:    "0003000000060103007d0001",
		RespHex:   "000300000003018302",
		Exception: ExcIllegalAddress,
	},
	// ---- FC16 write multiple registers ----
	{
		Name: "fc16_addr_124_qty_2_span_overflow",
		Desc: "addr=124 qty=2 reaches 125; last register is out of bank",
		// 00 04 |00 00|00 0B|01|10 00 7C 00 02 04 11 11 22 22
		ReqHex:    "00040000000b0110007c00020411112222",
		RespHex:   "000400000003019002",
		Exception: ExcIllegalAddress,
	},
	{
		Name: "fc16_byte_count_mismatch_small",
		Desc: "qty=2 requires 4 data bytes but byte count field says 2",
		// length = 1 unit + 1 fc + 2 addr + 2 qty + 1 bc + 2 data = 9
		ReqHex:    "000400000009011000000002020001",
		RespHex:   "000400000003019003",
		Exception: ExcIllegalValue,
	},
	{
		Name: "fc16_byte_count_exceeds_frame",
		Desc: "qty=2 byte count=4 but the frame ends after only 2 data " +
			"bytes; MBAP length matches the short delivery",
		ReqHex:    "000400000009011000000002040001",
		RespHex:   "000400000003019003",
		Exception: ExcIllegalValue,
	},
	// ---- unsupported function codes ----
	{
		Name:      "fc04_unsupported",
		Desc:      "read input registers (0x04) is not implemented by this fixture",
		ReqHex:    "000500000006010400000001",
		RespHex:   "000500000003018401",
		Exception: ExcIllegalFunction,
	},
	{
		Name:      "fc06_unsupported",
		Desc:      "write single register (0x06) is not implemented; only FC16",
		ReqHex:    "000500000006010600001234",
		RespHex:   "000500000003018601",
		Exception: ExcIllegalFunction,
	},
}
