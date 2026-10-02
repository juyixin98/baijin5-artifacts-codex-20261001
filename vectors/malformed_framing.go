package vectors

// Framing failure category strings. Values deliberately match mbap.Kind
// string values so independent tests can compare without vectors having to
// import the mbap module.
const (
	CatHeaderTruncated = "header_truncated"
	CatBodyTruncated   = "body_truncated"
	CatLengthTooSmall  = "length_too_small"
	CatLengthTooLarge  = "length_too_large"
	CatWrongProtocol   = "wrong_protocol_id"
	CatTrailingBytes   = "trailing_bytes"
)

// FramingCase is a byte stream that must be rejected at MBAP level. A
// well-behaved server must not emit a normal-data response and must close
// the connection once stream framing can no longer be trusted.
type FramingCase struct {
	Name     string
	Desc     string
	Hex      string
	Category string
}

// FramingMalformed vectors are derived from Golden[0] with one field
// corrupted at a time, so each failure class has an isolated cause.
var FramingMalformed = []FramingCase{
	{
		Name:     "header_only_six_bytes",
		Desc:     "stream ends halfway through the 7-byte MBAP header",
		Hex:      "123400000006",
		Category: CatHeaderTruncated,
	},
	{
		Name:     "protocol_id_nonzero",
		Desc:     "protocol id 0x0001 instead of 0x0000",
		Hex:      "123400010006050300000002",
		Category: CatWrongProtocol,
	},
	{
		Name:     "length_field_one",
		Desc:     "length=1 cannot even cover the unit id plus function code",
		Hex:      "1234000000010503",
		Category: CatLengthTooSmall,
	},
	{
		Name: "length_field_255",
		Desc: "length=255 exceeds the Modbus TCP maximum of 254 " +
			"(1 unit byte + 253 PDU bytes)",
		Hex:      "1234000000ff050300000002",
		Category: CatLengthTooLarge,
	},
	{
		Name:     "body_truncated",
		Desc:     "length announces 12 total bytes but only 9 arrived",
		Hex:      "12340000000605030000",
		Category: CatBodyTruncated,
	},
	{
		Name:     "trailing_extra_byte",
		Desc:     "one extra 0xFF byte after the announced frame",
		Hex:      "123400000006050300000002ff",
		Category: CatTrailingBytes,
	},
}
