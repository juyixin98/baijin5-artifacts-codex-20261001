package lineio

import (
	"bufio"
	"errors"
	"fmt"
	"io"
)

// ErrUnexpectedEnd is returned when the byte stream ends (or fails) before
// the DATA terminator <CRLF>.<CRLF> is seen — e.g. the client dropped the
// connection mid-body. The message MUST NOT be committed in this case.
var ErrUnexpectedEnd = errors.New("lineio: DATA body ended before terminator")

// ErrBadDotSequence is returned when a line starts with '.' but is neither
// the terminator nor a stuffed ".." line. The stream cannot be resynced.
var ErrBadDotSequence = errors.New("lineio: invalid dot sequence in DATA body")

// DotReader decodes an SMTP DATA body: dot-stuffed lines ("..x") are
// transparently un-stuffed to ".x" and the terminating <CRLF>.<CRLF>
// produces io.EOF. Correctness does not depend on how the underlying
// reader chunks bytes; single-byte delivery is handled identically.
type DotReader struct {
	r     *bufio.Reader
	out   []byte // decoded bytes pending delivery
	atSOL bool   // next input byte starts a new line
	done  bool
	err   error // terminal error: io.EOF on clean terminator
}

// NewDotReader wraps r, which must be positioned at the first byte of the
// DATA body (typically the session reader's Buffered() reader).
func NewDotReader(r io.Reader) *DotReader {
	if br, ok := r.(*bufio.Reader); ok {
		return &DotReader{r: br, atSOL: true}
	}
	return &DotReader{r: bufio.NewReader(r), atSOL: true}
}

// fill decodes input until at least one byte is pending delivery or the
// stream terminates.
func (d *DotReader) fill() {
	for len(d.out) == 0 && !d.done {
		b, err := d.r.ReadByte()
		if err != nil {
			d.fail(fmt.Errorf("%w: %v", ErrUnexpectedEnd, err))
			return
		}
		if d.atSOL && b == '.' {
			d.consumeDot()
			continue
		}
		d.out = append(d.out, b)
		d.atSOL = b == '\n'
	}
}

// consumeDot handles a '.' at start of line: either un-stuffing (".."),
// the terminator ("." CRLF, LF-only tolerated), or a protocol error.
func (d *DotReader) consumeDot() {
	next, err := d.r.ReadByte()
	if err != nil {
		d.fail(fmt.Errorf("%w: %v", ErrUnexpectedEnd, err))
		return
	}
	switch next {
	case '.': // stuffed: ".." decodes to a single '.'
		d.out = append(d.out, '.')
		d.atSOL = false
	case '\r':
		lf, err := d.r.ReadByte()
		if err != nil {
			d.fail(fmt.Errorf("%w: %v", ErrUnexpectedEnd, err))
			return
		}
		if lf != '\n' {
			d.fail(ErrBadDotSequence)
			return
		}
		d.done = true
		d.err = io.EOF
	case '\n': // tolerate LF-only terminator
		d.done = true
		d.err = io.EOF
	default:
		d.fail(ErrBadDotSequence)
	}
}

func (d *DotReader) fail(err error) {
	d.done = true
	d.err = err
}

// Read implements io.Reader.
func (d *DotReader) Read(p []byte) (int, error) {
	d.fill()
	if len(d.out) == 0 {
		return 0, d.err
	}
	n := copy(p, d.out)
	d.out = d.out[n:]
	return n, nil
}
