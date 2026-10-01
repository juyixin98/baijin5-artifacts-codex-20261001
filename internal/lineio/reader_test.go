package lineio

import (
	"errors"
	"io"
	"strings"
	"testing"
)

// chunkedReader delivers src in fixed-size chunks to prove correctness
// does not depend on how the network happens to segment bytes.
type chunkedReader struct {
	src   []byte
	chunk int
}

func (c *chunkedReader) Read(p []byte) (int, error) {
	if len(c.src) == 0 {
		return 0, io.EOF
	}
	n := c.chunk
	if n > len(c.src) {
		n = len(c.src)
	}
	if n > len(p) {
		n = len(p)
	}
	copy(p, c.src[:n])
	c.src = c.src[n:]
	return n, nil
}

func TestReadLineCRLF(t *testing.T) {
	rd := NewReader(strings.NewReader("EHLO a.test\r\nQUIT\r\n"), 100)
	line, err := rd.ReadLine()
	if err != nil || string(line) != "EHLO a.test" {
		t.Fatalf("got %q, %v", line, err)
	}
	line, err = rd.ReadLine()
	if err != nil || string(line) != "QUIT" {
		t.Fatalf("got %q, %v", line, err)
	}
}

func TestReadLineLFOnlyTolerated(t *testing.T) {
	rd := NewReader(strings.NewReader("NOOP\n"), 100)
	line, err := rd.ReadLine()
	if err != nil || string(line) != "NOOP" {
		t.Fatalf("got %q, %v", line, err)
	}
}

func TestReadLineTooLongKeepsStreamInSync(t *testing.T) {
	long := strings.Repeat("x", 50)
	rd := NewReader(strings.NewReader(long+"\r\nQUIT\r\n"), 10)
	if _, err := rd.ReadLine(); !errors.Is(err, ErrLineTooLong) {
		t.Fatalf("want ErrLineTooLong, got %v", err)
	}
	// The remainder of the overlong line must have been consumed; the
	// next line is a fresh command, not garbage.
	line, err := rd.ReadLine()
	if err != nil || string(line) != "QUIT" {
		t.Fatalf("stream desynced: got %q, %v", line, err)
	}
}

func TestReadLineExactLimitAccepted(t *testing.T) {
	rd := NewReader(strings.NewReader("1234567890\r\n"), 10)
	line, err := rd.ReadLine()
	if err != nil || len(line) != 10 {
		t.Fatalf("got %q, %v", line, err)
	}
}

func TestDotReaderBasic(t *testing.T) {
	dr := NewDotReader(strings.NewReader("hello\r\nworld\r\n.\r\n"))
	got, err := io.ReadAll(dr)
	if err != nil {
		t.Fatalf("err: %v", err)
	}
	if string(got) != "hello\r\nworld\r\n" {
		t.Fatalf("got %q", got)
	}
}

func TestDotReaderUnstuffing(t *testing.T) {
	dr := NewDotReader(strings.NewReader("..dot\r\n...two\r\n.\r\n"))
	got, err := io.ReadAll(dr)
	if err != nil {
		t.Fatalf("err: %v", err)
	}
	if string(got) != ".dot\r\n..two\r\n" {
		t.Fatalf("got %q", got)
	}
}

// The terminator and stuffed dots must decode correctly even when every
// byte arrives in its own read — the worst-case chunking.
func TestDotReaderSingleByteChunks(t *testing.T) {
	// Body lines "a", ".b", ".c" appear on the wire dot-stuffed.
	src := "a\r\n..b\r\n..c\r\n.\r\n"
	dr := NewDotReader(&chunkedReader{src: []byte(src), chunk: 1})
	got, err := io.ReadAll(dr)
	if err != nil {
		t.Fatalf("err: %v", err)
	}
	want := "a\r\n.b\r\n.c\r\n"
	if string(got) != want {
		t.Fatalf("got %q want %q", got, want)
	}
}

func TestDotReaderEmptyBody(t *testing.T) {
	dr := NewDotReader(strings.NewReader(".\r\n"))
	got, err := io.ReadAll(dr)
	if err != nil || len(got) != 0 {
		t.Fatalf("got %q, %v", got, err)
	}
}

func TestDotReaderUnexpectedEnd(t *testing.T) {
	// Client dropped mid-body: no terminator before EOF.
	dr := NewDotReader(strings.NewReader("partial body\r\n"))
	_, err := io.ReadAll(dr)
	if !errors.Is(err, ErrUnexpectedEnd) {
		t.Fatalf("want ErrUnexpectedEnd, got %v", err)
	}
}

func TestDotReaderBadDotSequence(t *testing.T) {
	dr := NewDotReader(strings.NewReader(".x\r\n.\r\n"))
	_, err := io.ReadAll(dr)
	if !errors.Is(err, ErrBadDotSequence) {
		t.Fatalf("want ErrBadDotSequence, got %v", err)
	}
}

func TestDotReaderLFOnlyTerminator(t *testing.T) {
	dr := NewDotReader(strings.NewReader("body\n.\n"))
	got, err := io.ReadAll(dr)
	if err != nil || string(got) != "body\n" {
		t.Fatalf("got %q, %v", got, err)
	}
}
