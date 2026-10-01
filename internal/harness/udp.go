// Package harness provides controlled local network services used by the
// compatibility tests: a real loopback UDP NTP server built from the same
// protocol state machine, and a misbehaving peer that replays stale replies.
//
// These services bind only to 127.0.0.1 ephemeral ports and require no
// external accounts or network access. They never modify the system clock.
package harness

import (
	"context"
	"sync"
	"time"

	"ntpsim/internal/clock"
	"ntpsim/internal/protocol"
	"ntpsim/internal/transport"
)

// UDPServer is a real UDP server driven on a goroutine.
type UDPServer struct {
	dg     *transport.UDPDatagram
	srv    *transport.Server
	cancel context.CancelFunc
	wg     sync.WaitGroup
}

// ServeUDPServer binds a loopback UDP NTP server with the given profile.
// clk may be nil (uses the wall clock, read-only).
func ServeUDPServer(prof transport.ServerProfile, clk clock.Clock) (*UDPServer, error) {
	dg, err := transport.ListenUDP("127.0.0.1:0")
	if err != nil {
		return nil, err
	}
	if clk == nil {
		clk = clock.WallClock{}
	}
	ctx, cancel := context.WithCancel(context.Background())
	s := &UDPServer{dg: dg, srv: transport.NewServer(clk, prof), cancel: cancel}
	s.wg.Add(1)
	go s.loop(ctx)
	return s, nil
}

func (s *UDPServer) loop(ctx context.Context) {
	defer s.wg.Done()
	for {
		select {
		case <-ctx.Done():
			return
		default:
		}
		if _, err := s.srv.ServeOnce(s.dg); err != nil {
			// On close the read errors; exit quietly after cancellation.
			select {
			case <-ctx.Done():
				return
			default:
			}
		}
	}
}

// Addr returns the server's host:port.
func (s *UDPServer) Addr() string { return s.dg.LocalAddr() }

// Close stops the server and closes the socket.
func (s *UDPServer) Close() error {
	s.cancel()
	err := s.dg.Close()
	s.wg.Wait()
	return err
}

// ReplayUDPServer answers every request with the SAME fixed packet (a stale
// origin timestamp), so a conformant client must reject it.
type ReplayUDPServer struct {
	dg     *transport.UDPDatagram
	cancel context.CancelFunc
	wg     sync.WaitGroup
	pkt    []byte
}

// ServeReplayUDPServer binds a loopback UDP peer that always emits canned.
func ServeReplayUDPServer() (*ReplayUDPServer, error) {
	dg, err := transport.ListenUDP("127.0.0.1:0")
	if err != nil {
		return nil, err
	}
	ctx, cancel := context.WithCancel(context.Background())
	s := &ReplayUDPServer{dg: dg, cancel: cancel}
	s.wg.Add(1)
	go s.loop(ctx)
	return s, nil
}

func (s *ReplayUDPServer) loop(ctx context.Context) {
	defer s.wg.Done()
	for {
		select {
		case <-ctx.Done():
			return
		default:
		}
		b, from, err := s.dg.Recv(time.Time{})
		if err != nil {
			select {
			case <-ctx.Done():
				return
			default:
			}
			continue
		}
		// Build a well-formed server packet with an origin that cannot match
		// any fresh request (fixed ancient transmit stamp).
		p := &protocol.Packet{
			LI: protocol.LINoWarning, Version: 4, Mode: protocol.ModeServer,
			Stratum:    2,
			OriginTime: protocol.TimestampFromTime(fixedPast()),
		}
		// Round-trip decode/re-encode of the inbound request is deliberately
		// NOT done: we ignore the client's transmit timestamp entirely.
		_ = b
		s.pkt = p.Encode()
		_ = s.dg.Send(s.pkt, from)
	}
}

// Addr returns the replay peer's host:port.
func (s *ReplayUDPServer) Addr() string { return s.dg.LocalAddr() }

// Close stops the peer.
func (s *ReplayUDPServer) Close() error {
	s.cancel()
	err := s.dg.Close()
	s.wg.Wait()
	return err
}
