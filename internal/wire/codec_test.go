package wire_test

import (
	"bytes"
	"errors"
	"io"
	"strings"
	"testing"

	"smtpsink/internal/wire"
)

// segmentedReader hands out one fixed-size chunk per Read, independently of
// the logical lines. It is the test's own transport shim, not production code.
type segmentedReader struct {
	data []byte
	size int
	pos  int
}

func (s *segmentedReader) Read(p []byte) (int, error) {
	if s.pos >= len(s.data) {
		return 0, io.EOF
	}
	n := s.size
	if n > len(p) {
		n = len(p)
	}
	if s.pos+n > len(s.data) {
		n = len(s.data) - s.pos
	}
	copy(p, s.data[s.pos:s.pos+n])
	s.pos += n
	return n, nil
}

func TestReadLine_HandlesCRLF_BareLF_andSegmentation(t *testing.T) {
	// Every physical line is delivered one byte at a time; decoded lines must
	// be identical regardless of where TCP segment boundaries fall.
	in := []byte("EHLO x.com\r\nMAIL FROM:<a@b>\nRCPT TO:<c@d>\r\n")
	r := wire.NewReader(&segmentedReader{data: in, size: 1}, 512)

	want := []string{"EHLO x.com", "MAIL FROM:<a@b>", "RCPT TO:<c@d>"}
	for i, expected := range want {
		got, err := r.ReadLine()
		if err != nil {
			t.Fatalf("line %d: unexpected error %v", i, err)
		}
		if got != expected {
			t.Fatalf("line %d across segments: got %q want %q", i, got, expected)
		}
	}
	if _, err := r.ReadLine(); !errors.Is(err, io.EOF) {
		t.Fatalf("expected io.EOF, got %v", err)
	}
}

func TestReadLine_DotTerminatorSplitAcrossBlocks(t *testing.T) {
	// "...\r\n.\r" then "\n" in separate reads: the terminator must still be
	// recognized, proving DATA end-of-data is transparent to segmentation.
	parts := []string{"Subject: split\r\n", "body line\r\n", ".", "\r", "\n"}
	pr := &pieceReader{parts: parts}
	r := wire.NewReader(pr, 1000)
	dr := wire.NewDataLineReader(r)

	got := collectData(t, dr)
	if len(got) != 2 || got[0] != "Subject: split" || got[1] != "body line" {
		t.Fatalf("decoded lines = %v, want [Subject: split body line]", got)
	}
}

type pieceReader struct {
	parts []string
	i     int
}

func (p *pieceReader) Read(b []byte) (int, error) {
	if p.i >= len(p.parts) {
		return 0, io.EOF
	}
	n := copy(b, p.parts[p.i])
	if n == len(p.parts[p.i]) {
		p.i++
	} else {
		p.parts[p.i] = p.parts[p.i][n:]
	}
	return n, nil
}

func collectData(t *testing.T, dr *wire.DataLineReader) []string {
	t.Helper()
	var out []string
	for {
		line, end, err := dr.ReadLine()
		if err != nil {
			t.Fatalf("unexpected data read error: %v", err)
		}
		if end {
			return out
		}
		out = append(out, line)
	}
}

func TestDataLineReader_DotUnstuffing(t *testing.T) {
	cases := []struct {
		name string
		wire string // raw line on the wire (no terminator)
		want string // decoded logical line
	}{
		{"ordinary", "hello world", "hello world"},
		{"leading dot escaped", "..kept-dot", ".kept-dot"},
		{"two escaped dots", "....two", "...two"},
		{"dot not at start untouched", "a.b", "a.b"},
		{"single dot alone is terminator", "", ""},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if tc.name == "single dot alone is terminator" {
				dr := wire.NewDataLineReader(wire.NewReader(strings.NewReader(".\r\n"), 10))
				_, end, err := dr.ReadLine()
				if err != nil || !end {
					t.Fatalf("end=%v err=%v, want terminator", end, err)
				}
				return
			}
			dr := wire.NewDataLineReader(wire.NewReader(strings.NewReader(tc.wire+"\r\n"), 100))
			got, end, err := dr.ReadLine()
			if err != nil || end {
				t.Fatalf("end=%v err=%v", end, err)
			}
			if got != tc.want {
				t.Fatalf("dot unstuff %q: got %q want %q", tc.wire, got, tc.want)
			}
		})
	}
}

func TestReadLine_OverlongResyncsThenFatalOverflow(t *testing.T) {
	// Line of 20 'x' bytes over a 10-byte limit, followed by a good line:
	// the over-long line is reported and discarded, framing resynchronizes.
	in := strings.NewReader(strings.Repeat("x", 20) + "\r\nGOOD\r\n")
	r := wire.NewReader(in, 10)

	if _, err := r.ReadLine(); !errors.Is(err, wire.ErrLineTooLong) {
		t.Fatalf("want ErrLineTooLong, got %v", err)
	}
	got, err := r.ReadLine()
	if err != nil {
		t.Fatalf("framing did not resync: %v", err)
	}
	if got != "GOOD" {
		t.Fatalf("after over-long line got %q want GOOD", got)
	}

	// Now an unbounded stream with no newline at all: reader must give up with
	// ErrFatalOverflow rather than buffering forever.
	endless := &endlessReader{}
	r2 := wire.NewReader(endless, 10)
	if _, err := r2.ReadLine(); !errors.Is(err, wire.ErrFatalOverflow) {
		t.Fatalf("want ErrFatalOverflow, got %v", err)
	}
	if endless.read < 10*wire.HardOverflowFactor {
		t.Fatalf("hard ceiling not enforced, only read %d", endless.read)
	}
}

type endlessReader struct{ read int }

func (e *endlessReader) Read(p []byte) (int, error) {
	for i := range p {
		p[i] = 'z'
	}
	e.read += len(p)
	return len(p), nil
}

func TestReadLine_FinalPartialLineAtEOF(t *testing.T) {
	r := wire.NewReader(strings.NewReader("partial"), 512)
	got, err := r.ReadLine()
	if err != nil || got != "partial" {
		t.Fatalf("got %q, %v; want partial, nil", got, err)
	}
	if _, err := r.ReadLine(); !errors.Is(err, io.EOF) {
		t.Fatalf("second read: want EOF, got %v", err)
	}
}

func TestReply_MultilineFormat(t *testing.T) {
	var buf bytes.Buffer
	if err := wire.Reply(&buf, 250, "first", "second"); err != nil {
		t.Fatal(err)
	}
	want := "250-first\r\n250 second\r\n"
	if buf.String() != want {
		t.Fatalf("reply = %q, want %q", buf.String(), want)
	}
}

func TestSetLimit(t *testing.T) {
	r := wire.NewReader(strings.NewReader(strings.Repeat("a", 20)+"\r\n"), 10)
	if _, err := r.ReadLine(); !errors.Is(err, wire.ErrLineTooLong) {
		t.Fatalf("want ErrLineTooLong at limit 10, got %v", err)
	}
	r2 := wire.NewReader(strings.NewReader(strings.Repeat("a", 20)+"\r\n"), 10)
	r2.SetLimit(50)
	got, err := r2.ReadLine()
	if err != nil || got != strings.Repeat("a", 20) {
		t.Fatalf("after SetLimit got len=%d err=%v", len(got), err)
	}
}
