// Package lineio implements byte-level CRLF line decoding for SMTP sessions.
//
// The Reader enforces a hard line-length cap while keeping the byte stream
// in sync: an overlong line is consumed up to its terminator before the
// error is reported, so the next ReadLine call starts at a real boundary.
package lineio

import (
	"bufio"
	"errors"
	"io"
)

// ErrLineTooLong is returned by ReadLine when a line exceeds the configured
// cap. The offending line has already been consumed through its terminator.
var ErrLineTooLong = errors.New("lineio: line exceeds maximum length")

// DefaultMaxLine bounds a command line. RFC 5321 §4.5.3.1 mandates
// accepting at least 512 octets; 1000 leaves headroom and rejects floods.
const DefaultMaxLine = 1000

// Reader reads CRLF-terminated lines from an SMTP connection.
type Reader struct {
	r       *bufio.Reader
	maxLine int
}

// NewReader wraps r. maxLine <= 0 selects DefaultMaxLine.
func NewReader(r io.Reader, maxLine int) *Reader {
	if maxLine <= 0 {
		maxLine = DefaultMaxLine
	}
	return &Reader{r: bufio.NewReader(r), maxLine: maxLine}
}

// Buffered exposes the underlying buffered reader so a DotReader can take
// over the same byte stream during DATA without losing buffered bytes.
func (rd *Reader) Buffered() *bufio.Reader { return rd.r }

// ReadLine reads one line and returns it without the trailing CRLF.
// LF-only terminators are tolerated for naive test clients; a bare CR
// inside a line is preserved as data. The length cap applies to the
// line content excluding the CRLF terminator.
func (rd *Reader) ReadLine() ([]byte, error) {
	buf := make([]byte, 0, 128)
	tooLong := false
	for {
		b, err := rd.r.ReadByte()
		if err != nil {
			return nil, err
		}
		if b == '\n' {
			if n := len(buf); n > 0 && buf[n-1] == '\r' {
				buf = buf[:n-1]
			}
			if tooLong || len(buf) > rd.maxLine {
				return nil, ErrLineTooLong
			}
			return buf, nil
		}
		if !tooLong {
			buf = append(buf, b)
			// Content plus the optional CR may fill maxLine+1 bytes
			// before the LF arrives; anything beyond is overlong.
			if len(buf) > rd.maxLine+1 {
				tooLong = true
				buf = nil
			}
		}
	}
}
