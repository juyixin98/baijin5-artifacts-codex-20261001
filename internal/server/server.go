// Package server is the controlled STUN Binding responder used in the lab.
// It answers on a local UDP socket with the packet source's reflected address
// (XOR-MAPPED-ADDRESS plus a legacy MAPPED-ADDRESS), rejects malformed
// requests with 400, unknown comprehension-required attributes with 420, and
// silently discards messages that fail MESSAGE-INTEGRITY, per RFC 5389.
package server

import (
	"context"
	"encoding/hex"
	"fmt"
	"net"
	"time"

	"stunlab/internal/evidence"
	"stunlab/internal/store"
	"stunlab/internal/stun"
)

// Software is the lab server SOFTWARE attribute value.
const Software = "stunlab-test/1.0"

// Config configures the controlled server.
type Config struct {
	Network   string // "udp4" or "udp6"
	Addr      string // listen address, e.g. "127.0.0.1:3478" or "[::1]:3478"
	Key       []byte // shared short-term key; nil disables integrity
	Logger    *evidence.Logger
	Store     *store.Store
	ReadLimit int // maximum accepted datagram size; 0 defaults to 1024
}

// Server is a running STUN lab responder.
type Server struct {
	cfg  Config
	conn *net.UDPConn
}

// Listen creates and binds the UDP socket.
func Listen(cfg Config) (*Server, error) {
	if cfg.Network == "" {
		cfg.Network = "udp4"
	}
	if cfg.ReadLimit == 0 {
		cfg.ReadLimit = 1024
	}
	addr, err := net.ResolveUDPAddr(cfg.Network, cfg.Addr)
	if err != nil {
		return nil, fmt.Errorf("stund: resolve %q: %w", cfg.Addr, err)
	}
	conn, err := net.ListenUDP(cfg.Network, addr)
	if err != nil {
		return nil, fmt.Errorf("stund: listen %s %s: %w", cfg.Network, cfg.Addr, err)
	}
	return &Server{cfg: cfg, conn: conn}, nil
}

// LocalAddr reports the bound address (useful with port 0 in tests).
func (s *Server) LocalAddr() *net.UDPAddr { return s.conn.LocalAddr().(*net.UDPAddr) }

// Close stops the server.
func (s *Server) Close() error { return s.conn.Close() }

// Serve runs the read loop until the context is cancelled.
func (s *Server) Serve(ctx context.Context) error {
	go func() { <-ctx.Done(); _ = s.conn.SetReadDeadline(time.Now()) }()
	buf := make([]byte, s.cfg.ReadLimit)
	for {
		n, src, err := s.conn.ReadFromUDP(buf)
		if err != nil {
			if ctx.Err() != nil {
				return nil
			}
			return fmt.Errorf("stund: read: %w", err)
		}
		pkt := make([]byte, n)
		copy(pkt, buf[:n])
		s.handle(ctx, pkt, src)
	}
}

func (s *Server) log() *evidence.Logger {
	if s.cfg.Logger != nil {
		return s.cfg.Logger
	}
	return nil
}

func (s *Server) record(ts string, x store.Exchange) {
	if s.cfg.Store != nil {
		_ = s.cfg.Store.RecordExchange(ts, x)
	}
}

// handle processes one datagram. All branches log their decision with enough
// intermediate state (txn id when parseable, remote address, outcome kind) to
// replay the exchange from the JSONL/SQLite evidence alone.
func (s *Server) handle(ctx context.Context, pkt []byte, src *net.UDPAddr) {
	now := time.Now().UTC().Format(time.RFC3339Nano)
	family := addrFamily(src.IP)
	base := func(txn string) store.Exchange {
		return store.Exchange{
			RunID: s.runID(), TxnID: txn, RemoteAddr: src.String(), Family: family,
		}
	}
	lg := s.log()

	m, err := stun.UnmarshalMessage(pkt)
	if err != nil {
		// Only reply 400 to datagrams that look like STUN (leading bits 0).
		// Random traffic gets dropped without a response.
		kind := stun.ErrorOf(err)
		if len(pkt) > 0 && pkt[0]&0xC0 == 0 {
			s.sendError(ctx, src, zeroTxn(pkt), 400, "Bad Request", nil)
		}
		x := base(txnHexOrUnknown(pkt))
		x.Outcome = "bad_request"
		x.Detail = err.Error()
		s.record(now, x)
		if lg != nil {
			lg.Error("request_rejected", string(kind), map[string]any{
				"remote_addr": src.String(), "packet_hex": hex.EncodeToString(pkt),
				"detail": err.Error(), "responded": len(pkt) > 0 && pkt[0]&0xC0 == 0,
			})
		}
		return
	}

	x := base(hex.EncodeToString(m.TransactionID[:]))

	if m.Class != stun.ClassRequest {
		x.Outcome = "dropped"
		x.Detail = "not a request, class=0x" + fmt.Sprintf("%04x", uint16(m.Class))
		s.record(now, x)
		if lg != nil {
			lg.Warn("request_rejected", map[string]any{
				"txn_id": x.TxnID, "remote_addr": src.String(),
				"class": fmt.Sprintf("0x%04x", uint16(m.Class)),
			})
		}
		return
	}

	// RFC 5389 processing order for a request: integrity gate first (silent
	// discard on failure), then comprehension-required attributes (420),
	// then method handling.
	if len(s.cfg.Key) > 0 {
		if verr := stun.VerifyMessageIntegrity(pkt, s.cfg.Key); verr != nil {
			// RFC 5389 15.4: silently discard; the evidence log keeps the
			// reason so the tamper vector is still observable.
			x.Outcome = "integrity_failure"
			x.Detail = verr.Error()
			s.record(now, x)
			if lg != nil {
				lg.Error("request_discarded", string(stun.ErrorOf(verr)), map[string]any{
					"txn_id": x.TxnID, "remote_addr": src.String(),
					"detail": verr.Error(), "packet_hex": hex.EncodeToString(pkt),
				})
			}
			return
		}
	}

	unknown := stun.UnknownRequired(m.Attributes, isUnderstood)
	if len(unknown) > 0 {
		s.sendError(ctx, src, m.TransactionID, 420, "Unknown Attribute", unknown)
		x.Outcome = "error_response"
		x.ErrorCode = 420
		x.Detail = "unknown comprehension-required attributes"
		s.record(now, x)
		if lg != nil {
			lg.Warn("request_420", map[string]any{
				"txn_id": x.TxnID, "unknown_attributes": fmtUnknown(unknown),
			})
		}
		return
	}

	if m.Method != stun.MethodBinding {
		s.sendError(ctx, src, m.TransactionID, 400, "Bad Request: only Binding supported", nil)
		x.Outcome = "error_response"
		x.ErrorCode = 400
		x.Detail = "unsupported method 0x" + fmt.Sprintf("%03x", uint16(m.Method))
		s.record(now, x)
		return
	}

	reflected := stun.Address{IP: src.IP, Port: src.Port}
	if err := s.sendSuccess(ctx, src, m.TransactionID, reflected); err != nil {
		x.Outcome = "compute_failure"
		x.Detail = err.Error()
		s.record(now, x)
		if lg != nil {
			lg.Error("response_build_failed", string(stun.KindCompute), map[string]any{
				"txn_id": x.TxnID, "detail": err.Error(),
			})
		}
		return
	}
	x.Outcome = "success"
	x.MappedIP = src.IP.String()
	x.MappedPort = src.Port
	s.record(now, x)
	if lg != nil {
		lg.Info("binding_success", map[string]any{
			"txn_id": x.TxnID, "remote_addr": src.String(),
			"family": family, "mapped": reflected.IP.String(), "port": reflected.Port,
		})
	}
}

func (s *Server) sendSuccess(ctx context.Context, src *net.UDPAddr, txn stun.TransactionID, mapped stun.Address) error {
	xorVal, err := stun.EncodeXORMappedAddress(mapped, txn)
	if err != nil {
		return err
	}
	mapVal, err := stun.EncodeMappedAddress(mapped)
	if err != nil {
		return err
	}
	attrs := []stun.Attribute{
		{Type: stun.AttrXORMappedAddress, Value: xorVal},
		{Type: stun.AttrMappedAddress, Value: mapVal},
		{Type: stun.AttrSoftware, Value: []byte(Software)},
	}
	var out []byte
	if len(s.cfg.Key) > 0 {
		out, err = stun.AddMessageIntegrity(stun.MethodBinding, stun.ClassSuccessResponse, txn, attrs, s.cfg.Key)
	} else {
		out, err = stun.Marshal(stun.MethodBinding, stun.ClassSuccessResponse, txn, attrs)
	}
	if err != nil {
		return err
	}
	return s.write(ctx, out, src)
}

func (s *Server) sendError(ctx context.Context, src *net.UDPAddr, txn stun.TransactionID, code int, reason string, unknown []stun.AttributeType) {
	ecVal, err := stun.EncodeErrorCode(stun.ErrorCode{Code: code, Reason: reason})
	if err != nil {
		return
	}
	attrs := []stun.Attribute{{Type: stun.AttrErrorCode, Value: ecVal}}
	if len(unknown) > 0 {
		attrs = append(attrs, stun.Attribute{
			Type: stun.AttrUnknownAttributes, Value: stun.EncodeUnknownAttributes(unknown),
		})
	}
	var out []byte
	if len(s.cfg.Key) > 0 {
		out, err = stun.AddMessageIntegrity(stun.MethodBinding, stun.ClassErrorResponse, txn, attrs, s.cfg.Key)
	} else {
		out, err = stun.Marshal(stun.MethodBinding, stun.ClassErrorResponse, txn, attrs)
	}
	if err != nil {
		return
	}
	_ = s.write(ctx, out, src)
}

func (s *Server) write(ctx context.Context, b []byte, dst *net.UDPAddr) error {
	_ = ctx
	_ = s.conn.SetWriteDeadline(time.Now().Add(2 * time.Second))
	if _, err := s.conn.WriteToUDP(b, dst); err != nil {
		return fmt.Errorf("stund: write: %w", err)
	}
	return nil
}

func (s *Server) runID() string {
	if s.cfg.Logger != nil {
		return s.cfg.Logger.RunID()
	}
	return "stund-no-logger"
}

func isUnderstood(t stun.AttributeType) bool {
	switch t {
	case stun.AttrMappedAddress, stun.AttrUsername, stun.AttrMessageIntegrity,
		stun.AttrErrorCode, stun.AttrXORMappedAddress, stun.AttrSoftware,
		stun.AttrFingerprint:
		return true
	}
	return false
}

func addrFamily(ip net.IP) string {
	if ip.To4() != nil {
		return "ipv4"
	}
	if ip.To16() != nil {
		return "ipv6"
	}
	return "unknown"
}

func zeroTxn(pkt []byte) stun.TransactionID {
	var id stun.TransactionID
	if len(pkt) >= 20 {
		copy(id[:], pkt[8:20])
	}
	return id
}

func txnHexOrUnknown(pkt []byte) string {
	if len(pkt) >= 20 {
		return hex.EncodeToString(pkt[8:20])
	}
	return "unparseable"
}

func fmtUnknown(ts []stun.AttributeType) []string {
	out := make([]string, len(ts))
	for i, t := range ts {
		out[i] = fmt.Sprintf("0x%04x", uint16(t))
	}
	return out
}
