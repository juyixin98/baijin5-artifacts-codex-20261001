package mbap

import "fmt"

// Kind is the exact failure class of a framing error. Tests assert on the
// concrete kind rather than on the presence of an error.
type Kind string

const (
	// KindHeaderTruncated: fewer than 7 bytes were delivered for the header.
	KindHeaderTruncated Kind = "header_truncated"
	// KindBodyTruncated: the header announced more bytes than the stream held.
	KindBodyTruncated Kind = "body_truncated"
	// KindLengthTooSmall: the length field is below MinLength (2).
	KindLengthTooSmall Kind = "length_too_small"
	// KindLengthTooLarge: the length field is above MaxLength (254).
	KindLengthTooLarge Kind = "length_too_large"
	// KindWrongProtocol: the protocol id is not 0x0000.
	KindWrongProtocol Kind = "wrong_protocol_id"
	// KindTrailingBytes: the input held bytes beyond the announced frame.
	KindTrailingBytes Kind = "trailing_bytes"
)

// FrameError is a deterministic, non-retryable framing failure.
type FrameError struct {
	Kind   Kind
	Detail string
}

func (e *FrameError) Error() string {
	return fmt.Sprintf("mbap: %s: %s", e.Kind, e.Detail)
}
