// Package wire implements RFC 7252 message and RFC 7959 block option
// byte-level encoding. It contains no I/O and no transfer state so that
// codec decisions can be unit tested against fixed byte vectors.
package wire

import "strconv"

// Type is the 2-bit CoAP message type (Ver/T/... header byte, bits 4-5).
type Type uint8

// CoAP message types, RFC 7252 §4.2.
const (
	CON Type = 0 // Confirmable
	NON Type = 1 // Non-confirmable
	ACK Type = 2 // Acknowledgement
	RST Type = 3 // Reset
)

func (t Type) String() string {
	switch t {
	case CON:
		return "CON"
	case NON:
		return "NON"
	case ACK:
		return "ACK"
	case RST:
		return "RST"
	default:
		return "?"
	}
}

// Code is a raw CoAP code byte: class<<5 | detail (§3 and §5.9).
type Code uint8

// Request codes (§5.8) and the response codes used by this subset.
const (
	Empty Code = 0

	GET     Code = 1  // 0.01
	POST    Code = 2  // 0.02
	PUT     Code = 3  // 0.03
	DELETE  Code = 4  // 0.04
	Created Code = 65 // 2.01
	Deleted Code = 66 // 2.02
	Valid   Code = 67 // 2.03
	Changed Code = 68 // 2.04
	Content Code = 69 // 2.05

	Continue Code = 95 // 2.31, RFC 7959 §2.9.1

	BadRequest               Code = 128 // 4.00
	Unauthorized             Code = 129 // 4.01
	BadOption                Code = 130 // 4.02
	Forbidden                Code = 131 // 4.03
	NotFound                 Code = 132 // 4.04
	MethodNotAllowed         Code = 133 // 4.05
	NotAcceptable            Code = 134 // 4.06
	Conflict                 Code = 137 // 4.09
	PreconditionFailed       Code = 140 // 4.12
	RequestEntityIncomplete  Code = 136 // 4.08, RFC 7959 §2.9.2
	RequestEntityTooLarge    Code = 141 // 4.13
	UnsupportedContentFormat Code = 143 // 4.15

	InternalServerError Code = 160 // 5.00
	NotImplemented      Code = 161 // 5.01
	ServiceUnavailable  Code = 163 // 5.03
)

// Class returns the high-level class: 0 for the Empty message / requests,
// 2 success, 4 client error, 5 server error.
func (c Code) Class() int {
	if c == Empty {
		return 0
	}
	return int(c >> 5)
}

// Detail returns the detail part (c mod 32).
func (c Code) Detail() int { return int(c & 0x1f) }

// String renders codes as "2.05 Content".
func (c Code) String() string {
	if name, ok := codeNames[c]; ok {
		return name
	}
	return digit(c)
}

func digit(c Code) string {
	if c == Empty {
		return "0.00 Empty"
	}
	return strconv.Itoa(c.Class()) + "." + twoDigit(c.Detail())
}

// twoDigit renders the 0..31 detail part with a leading zero when < 10,
// matching CoAP's dotted code notation ("2.05", "4.08").
func twoDigit(n int) string {
	if n < 10 {
		return "0" + strconv.Itoa(n)
	}
	return strconv.Itoa(n)
}

var codeNames = map[Code]string{
	Empty:   "0.00 Empty",
	GET:     "0.01 GET",
	POST:    "0.02 POST",
	PUT:     "0.03 PUT",
	DELETE:  "0.04 DELETE",
	Created: "2.01 Created",
	Deleted: "2.02 Deleted",
	Valid:   "2.03 Valid",
	Changed: "2.04 Changed",
	Content: "2.05 Content",

	Continue: "2.31 Continue",

	BadRequest:               "4.00 Bad Request",
	Unauthorized:             "4.01 Unauthorized",
	BadOption:                "4.02 Bad Option",
	Forbidden:                "4.03 Forbidden",
	NotFound:                 "4.04 Not Found",
	MethodNotAllowed:         "4.05 Method Not Allowed",
	NotAcceptable:            "4.06 Not Acceptable",
	RequestEntityIncomplete:  "4.08 Request Entity Incomplete",
	Conflict:                 "4.09 Conflict",
	PreconditionFailed:       "4.12 Precondition Failed",
	RequestEntityTooLarge:    "4.13 Request Entity Too Large",
	UnsupportedContentFormat: "4.15 Unsupported Content-Format",

	InternalServerError: "5.00 Internal Server Error",
	NotImplemented:      "5.01 Not Implemented",
	ServiceUnavailable:  "5.03 Service Unavailable",
}
