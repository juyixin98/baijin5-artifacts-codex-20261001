// Package wire implements the byte-level SMTP framing: line reading that is
// transparent to TCP segmentation, strict line-length enforcement and the
// DATA dot-unstuffing transform from RFC 5321 section 4.5.2.
package wire

import (
	"bufio"
	"errors"
	"io"
	"strconv"
	"strings"
)

const (
	// CR and LF are the SMTP line terminators. Receivers are required to
	// tolerate a bare LF; stored mail is always normalized back to CRLF.
	CR byte = '\r'
	LF byte = '\n'

	// HardOverflowFactor bounds how many bytes are drained while resynchronizing
	// past an over-long line. A peer that keeps sending without a newline is
	// considered unrecoverable and the connection is closed.
	HardOverflowFactor = 16
)

var (
	// ErrLineTooLong reports a line longer than the negotiated limit. The
	// offending physical line has already been consumed up to its newline, so
	// the session can continue with the next command.
	ErrLineTooLong = errors.New("wire: line longer than permitted length")
	// ErrFatalOverflow reports that an over-long line could not be drained and
	// the transport stream is no longer synchronized.
	ErrFatalOverflow = errors.New("wire: over-long line with no terminator, closing connection")
)

// Reader reads newline-delimited SMTP records from an underlying transport.
// bufio.Reader assembles records across arbitrary Read boundaries, so a
// command or a dot-terminator split across TCP segments is handled
// transparently.
type Reader struct {
	br    *bufio.Reader
	limit int
	// partialServed hands back a final partial line followed by io.EOF
	// exactly once when the peer closes mid-line.
	partialServed bool
}

// NewReader creates a Reader enforcing the supplied per-line byte limit.
func NewReader(r io.Reader, limit int) *Reader {
	return &Reader{br: bufio.NewReaderSize(r, 4096), limit: limit}
}

// SetLimit changes the per-line byte limit. The state machine uses it to apply
// the wider DATA body-line allowance (RFC 5321: 1000 octets incl. CRLF for
// commands/text) only while message content is being read.
func (r *Reader) SetLimit(limit int) { r.limit = limit }

// ReadLine returns one line without its CRLF (or bare LF) terminator.
// ErrLineTooLong is returned when the line exceeded limit; the stream has
// been resynchronized to the next newline whenever possible.
func (r *Reader) ReadLine() (string, error) {
	if r.partialServed {
		return "", io.EOF
	}

	var sb strings.Builder
	for {
		chunk, err := r.br.ReadSlice('\n')
		sb.Write(chunk)

		switch {
		case err == nil:
			return finishLine(sb.String(), r.limit)
		case errors.Is(err, bufio.ErrBufferFull):
			// A normal line may span multiple buffer fills; only bail out once
			// it is already provably over the limit.
			if sb.Len() > r.limit && r.discardRest(sb.Len()) {
				return "", ErrFatalOverflow
			} else if sb.Len() > r.limit {
				return "", ErrLineTooLong
			}
		case errors.Is(err, io.EOF):
			if sb.Len() == 0 {
				return "", io.EOF
			}
			r.partialServed = true
			return finishLine(sb.String(), r.limit)
		default:
			return "", err
		}
	}
}

// discardRest consumes the remainder of an over-long physical line without
// retaining it. It returns true when the hard ceiling is crossed without a
// newline, meaning framing can no longer be recovered.
func (r *Reader) discardRest(already int) bool {
	total := already
	for total < r.limit*HardOverflowFactor {
		chunk, err := r.br.ReadSlice('\n')
		total += len(chunk)
		if err == nil {
			return false
		}
		if !errors.Is(err, bufio.ErrBufferFull) {
			return true
		}
	}
	return true
}

func finishLine(raw string, limit int) (string, error) {
	line := strings.TrimSuffix(raw, "\n")
	line = strings.TrimSuffix(line, "\r")
	if len(line) > limit {
		return "", ErrLineTooLong
	}
	return line, nil
}

// DataLineReader decorates a Reader with the RFC 5321 DATA transparency
// transform. Logical lines are returned without terminators; callers append
// CRLF themselves when persisting canonical content.
type DataLineReader struct {
	r *Reader
}

// NewDataLineReader wraps r for reading DATA content.
func NewDataLineReader(r *Reader) *DataLineReader {
	return &DataLineReader{r: r}
}

// ReadLine returns the next decoded content line. end is true only for the
// single-dot terminator.
func (d *DataLineReader) ReadLine() (line string, end bool, err error) {
	raw, err := d.r.ReadLine()
	if err != nil {
		return "", false, err
	}
	if raw == "." {
		return "", true, nil
	}
	// Transparency: exactly one leading dot, added by the sender, is removed.
	// A body line that itself begins with dot arrives double-dotted.
	if strings.HasPrefix(raw, ".") {
		raw = raw[1:]
	}
	return raw, false, nil
}

// Reply writes one (possibly multi-line) SMTP reply using RFC 5321 rules:
// continuation lines use "code-" and the final line uses "code ".
func Reply(w io.Writer, code int, lines ...string) error {
	var b strings.Builder
	last := len(lines) - 1
	for i, text := range lines {
		sep := byte('-')
		if i == last {
			sep = ' '
		}
		b.WriteString(strconv.Itoa(code))
		b.WriteByte(sep)
		b.WriteString(text)
		b.WriteString("\r\n")
	}
	_, err := io.WriteString(w, b.String())
	return err
}
