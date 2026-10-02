package ctl

import (
	"bufio"
	"context"
	"io"
	"log/slog"
	"net"
	"strings"
	"testing"
	"time"
)

type fakeAdmin struct {
	rotated string
	info    MailboxInfo
	reseed  bool
	failIt  bool
}

func (f *fakeAdmin) RotateUIDValidity(_ context.Context, mb string) (uint32, error) {
	f.rotated = mb
	return 7777, nil
}
func (f *fakeAdmin) MailboxInfo(_ context.Context, mb string) (MailboxInfo, error) {
	return MailboxInfo{Name: mb, Exists: 3, UIDNext: 4, UIDValidity: 1001}, nil
}
func (f *fakeAdmin) Reseed(_ context.Context) error { f.reseed = true; return nil }

func startCtl(t *testing.T) (*Server, net.Conn) {
	t.Helper()
	s := New("127.0.0.1:0", &fakeAdmin{}, slog.New(slog.NewTextHandler(io.Discard, nil)))
	if err := s.Listen(); err != nil {
		t.Fatal(err)
	}
	go func() { _ = s.Serve(context.Background()) }()
	t.Cleanup(func() { _ = s.Close() })
	c, err := net.Dial("tcp", s.Addr())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = c.Close() })
	return s, c
}

func roundTrip(t *testing.T, c net.Conn, cmd string) string {
	t.Helper()
	br := bufio.NewReader(c)
	br.ReadString('\n') // banner
	if _, err := c.Write([]byte(cmd + "\r\n")); err != nil {
		t.Fatal(err)
	}
	line, err := br.ReadString('\n')
	if err != nil {
		t.Fatal(err)
	}
	return strings.TrimRight(line, "\r\n")
}

func TestCtlCommands(t *testing.T) {
	_, c := startCtl(t)
	c.SetDeadline(time.Now().Add(3 * time.Second))
	br := bufio.NewReader(c)
	getReply := func(cmd string) string {
		if _, err := c.Write([]byte(cmd + "\r\n")); err != nil {
			t.Fatal(err)
		}
		l, _ := br.ReadString('\n')
		return strings.TrimRight(l, "\r\n")
	}
	_, _ = br.ReadString('\n') // banner

	if r := getReply("INFO INBOX"); !strings.Contains(r, "uidvalidity=1001") || !strings.Contains(r, "exists=3") {
		t.Fatalf("INFO wrong: %q", r)
	}
	if r := getReply("ROTATE INBOX"); !strings.Contains(r, "uidvalidity=7777") {
		t.Fatalf("ROTATE wrong: %q", r)
	}
	if r := getReply("RESEED"); r != "OK reseeded" {
		t.Fatalf("RESEED wrong: %q", r)
	}
	if r := getReply("BOGUS"); !strings.HasPrefix(r, "ERR unknown command") {
		t.Fatalf("unknown command wrong: %q", r)
	}
	if r := getReply("INFO"); !strings.Contains(r, "usage") {
		t.Fatalf("usage error wrong: %q", r)
	}
}

func TestCtlRejectsNonLoopbackBind(t *testing.T) {
	s := New("8.8.8.8:1144", &fakeAdmin{}, nil)
	if err := s.Listen(); err == nil {
		_ = s.Close()
		t.Fatal("non-loopback control bind must be rejected")
	}
}

var _ = roundTrip
