// Package ctl is the loopback-only administrative control surface: a tiny
// line protocol (NOT IMAP) to rotate a mailbox's UIDVALIDITY, inspect counters
// and reseed fixtures. It exists so stale-UIDVALIDITY behaviour can be driven
// externally from the demo script and tests without touching the database.
package ctl

import (
	"bufio"
	"context"
	"fmt"
	"io"
	"log/slog"
	"net"
	"strings"
	"sync"
)

// Admin is the subset of the server the control plane needs.
type Admin interface {
	RotateUIDValidity(ctx context.Context, mailbox string) (uint32, error)
	MailboxInfo(ctx context.Context, mailbox string) (MailboxInfo, error)
	Reseed(ctx context.Context) error
}

// MailboxInfo is a control-plane snapshot.
type MailboxInfo struct {
	Name        string
	Exists      int
	UIDNext     uint32
	UIDValidity uint32
}

// Server is the loopback control listener.
type Server struct {
	addr   string
	admin  Admin
	log    *slog.Logger
	ln     net.Listener
	wg     sync.WaitGroup
	closeM sync.Mutex
	closed bool
}

// New creates a control server bound to addr.
func New(addr string, admin Admin, log *slog.Logger) *Server {
	if log == nil {
		log = slog.Default()
	}
	return &Server{addr: addr, admin: admin, log: log}
}

// Addr reports the bound address.
func (s *Server) Addr() string {
	if s.ln != nil {
		return s.ln.Addr().String()
	}
	return s.addr
}

// Listen binds the port, rejecting non-loopback configuration defensively.
func (s *Server) Listen() error {
	host := s.addr
	if i := strings.LastIndex(s.addr, ":"); i >= 0 {
		host = s.addr[:i]
	}
	if host != "127.0.0.1" && host != "localhost" && host != "[::1]" && host != "::1" && host != "" {
		return fmt.Errorf("control listener must be loopback, got %q", s.addr)
	}
	ln, err := net.Listen("tcp", s.addr)
	if err != nil {
		return fmt.Errorf("ctl listen: %w", err)
	}
	s.ln = ln
	return nil
}

// Serve accepts control connections until Close.
func (s *Server) Serve(ctx context.Context) error {
	for {
		c, err := s.ln.Accept()
		if err != nil {
			s.closeM.Lock()
			closed := s.closed
			s.closeM.Unlock()
			if closed {
				return nil
			}
			return err
		}
		s.wg.Add(1)
		go func(conn net.Conn) {
			defer s.wg.Done()
			s.handle(ctx, conn)
		}(c)
	}
}

// Close stops the listener.
func (s *Server) Close() error {
	s.closeM.Lock()
	s.closed = true
	ln := s.ln
	s.closeM.Unlock()
	if ln != nil {
		ln.Close()
	}
	s.wg.Wait()
	return nil
}

func (s *Server) handle(ctx context.Context, c net.Conn) {
	defer c.Close()
	// Control connections are trusted only via loopback binding.
	if host, _, _ := net.SplitHostPort(c.RemoteAddr().String()); !isLoopback(host) {
		fmt.Fprintln(c, "ERR rejected: not loopback")
		return
	}
	fmt.Fprintln(c, "OK imaplite control; commands: INFO <mb>, ROTATE <mb>, RESEED, QUIT")
	sc := bufio.NewScanner(c)
	sc.Buffer(make([]byte, 0, 4096), 64*1024)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" {
			continue
		}
		fields := strings.Fields(line)
		cmd := strings.ToUpper(fields[0])
		switch cmd {
		case "QUIT":
			fmt.Fprintln(c, "BYE")
			return
		case "INFO":
			if len(fields) != 2 {
				fmt.Fprintln(c, "ERR usage: INFO <mailbox>")
				continue
			}
			info, err := s.admin.MailboxInfo(ctx, fields[1])
			if err != nil {
				fmt.Fprintf(c, "ERR %s\n", sanitize(err.Error()))
				continue
			}
			fmt.Fprintf(c, "OK name=%s exists=%d uidnext=%d uidvalidity=%d\n",
				info.Name, info.Exists, info.UIDNext, info.UIDValidity)
		case "ROTATE":
			if len(fields) != 2 {
				fmt.Fprintln(c, "ERR usage: ROTATE <mailbox>")
				continue
			}
			v, err := s.admin.RotateUIDValidity(ctx, fields[1])
			if err != nil {
				fmt.Fprintf(c, "ERR %s\n", sanitize(err.Error()))
				continue
			}
			fmt.Fprintf(c, "OK uidvalidity=%d (old UIDs now invalid)\n", v)
		case "RESEED":
			if err := s.admin.Reseed(ctx); err != nil {
				fmt.Fprintf(c, "ERR %s\n", sanitize(err.Error()))
				continue
			}
			fmt.Fprintln(c, "OK reseeded")
		default:
			fmt.Fprintf(c, "ERR unknown command %q\n", sanitize(cmd))
		}
	}
	if err := sc.Err(); err != nil && err != io.EOF {
		s.log.Warn("ctl scan error", "err", err.Error())
	}
}

func isLoopback(host string) bool {
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback()
}

// sanitize keeps control replies single-line.
func sanitize(s string) string {
	s = strings.ReplaceAll(s, "\r", " ")
	s = strings.ReplaceAll(s, "\n", " ")
	return s
}
