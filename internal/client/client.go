package client

import (
	"context"
	"encoding/hex"
	"errors"
	"fmt"
	"net"
	"time"

	"stunlab/internal/evidence"
	"stunlab/internal/store"
	"stunlab/internal/stun"
)

// Result is the outcome of a Binding call.
type Result struct {
	TxnID     stun.TransactionID
	Endpoint  stun.Address // reflected endpoint from XOR-MAPPED-ADDRESS
	Server    string
	Attempts  int
	ErrorCode int    // 0 on success; STUN error code on an error response
	Detail    string // error response reason or failure detail
}

// Config configures a Client.
type Config struct {
	Network        string        // "udp4" (default) or "udp6"
	Key            []byte        // shared short-term integrity key; nil disables
	Timeout        time.Duration // overall deadline per Bind; default 1s
	Retransmit     time.Duration // initial RTO, doubled each retry; default 200ms
	MaxAttempts    int           // request transmissions; default 3
	MaxPending     int           // transaction table cap; default 256
	Logger         *evidence.Logger
	Store          *store.Store
	VerifyResponse bool // verify MESSAGE-INTEGRITY on responses when Key is set
}

// Client owns one UDP socket and its read loop.
type Client struct {
	cfg  Config
	conn *net.UDPConn
	tbl  *TransactionTable
	log  *evidence.Logger
}

// Dial creates a client bound to an ephemeral local address and starts its read
// loop. ServerAddr is only used as the default Bind target; the socket itself
// is unconnected so multiple servers could be queried.
func Dial(ctx context.Context, cfg Config) (*Client, error) {
	if cfg.Network == "" {
		cfg.Network = "udp4"
	}
	if cfg.Timeout == 0 {
		cfg.Timeout = time.Second
	}
	if cfg.Retransmit == 0 {
		cfg.Retransmit = 200 * time.Millisecond
	}
	if cfg.MaxAttempts == 0 {
		cfg.MaxAttempts = 3
	}
	if cfg.MaxPending == 0 {
		cfg.MaxPending = 256
	}
	if len(cfg.Key) > 0 {
		cfg.VerifyResponse = true
	}
	conn, err := net.ListenUDP(cfg.Network, &net.UDPAddr{})
	if err != nil {
		return nil, fmt.Errorf("stunc: listen: %w", err)
	}
	c := &Client{
		cfg:  cfg,
		conn: conn,
		tbl:  NewTransactionTable(cfg.MaxPending),
		log:  cfg.Logger,
	}
	go c.readLoop(ctx)
	return c, nil
}

// Close releases the socket.
func (c *Client) Close() error { return c.conn.Close() }

// LocalAddr reports the client's bound address.
func (c *Client) LocalAddr() *net.UDPAddr { return c.conn.LocalAddr().(*net.UDPAddr) }

func (c *Client) readLoop(ctx context.Context) {
	buf := make([]byte, 1024)
	for {
		if err := c.conn.SetReadDeadline(time.Now().Add(500 * time.Millisecond)); err != nil {
			return
		}
		n, addr, err := c.conn.ReadFromUDP(buf)
		if err != nil {
			if ctx.Err() != nil {
				return
			}
			var ne net.Error
			if errors.As(err, &ne) && ne.Timeout() {
				continue
			}
			return
		}
		pkt := make([]byte, n)
		copy(pkt, buf[:n])
		verdict := c.tbl.Deliver(&inbound{raw: pkt, source: addr.String()})
		if c.log != nil && verdict != "matched" {
			c.log.Warn("datagram_rejected", map[string]any{
				"source": addr.String(), "reason": verdict,
				"packet_hex": hex.EncodeToString(pkt),
			})
		}
	}
}

// Bind performs one Binding transaction against serverAddr ("host:port").
func (c *Client) Bind(ctx context.Context, serverAddr string) (*Result, error) {
	raddr, err := net.ResolveUDPAddr(c.cfg.Network, serverAddr)
	if err != nil {
		return nil, &stun.Error{Kind: stun.KindInput, Op: "client.Bind",
			Detail: "resolve server address", Err: err}
	}
	txn, err := stun.NewTransactionID()
	if err != nil {
		return nil, err
	}
	attrs := []stun.Attribute{{Type: stun.AttrSoftware, Value: []byte("stunlab-client/1.0")}}
	var pkt []byte
	if len(c.cfg.Key) > 0 {
		pkt, err = stun.AddMessageIntegrity(stun.MethodBinding, stun.ClassRequest, txn, attrs, c.cfg.Key)
	} else {
		pkt, err = stun.Marshal(stun.MethodBinding, stun.ClassRequest, txn, attrs)
	}
	if err != nil {
		return nil, err
	}

	replyCh, err := c.tbl.Add(txn, raddr.String(), c.cfg.Timeout)
	if err != nil {
		return nil, err
	}

	deadline := time.Now().Add(c.cfg.Timeout)
	rto := c.cfg.Retransmit
	res := &Result{TxnID: txn, Server: raddr.String()}

	for attempt := 1; attempt <= c.cfg.MaxAttempts; attempt++ {
		res.Attempts = attempt
		if _, werr := c.conn.WriteToUDP(pkt, raddr); werr != nil {
			c.tbl.Take(txn)
			return nil, &stun.Error{Kind: stun.KindCompute, Op: "client.Bind",
				Detail: "send request", Err: werr}
		}
		if c.log != nil {
			c.log.Info("request_sent", map[string]any{
				"txn_id": hex.EncodeToString(txn[:]), "attempt": attempt,
				"server": raddr.String(), "rto_ms": rto.Milliseconds(),
			})
		}
		remaining := time.Until(deadline)
		if remaining <= 0 {
			break
		}
		wait := rto
		if wait > remaining {
			wait = remaining
		}
		select {
		case <-ctx.Done():
			c.tbl.Take(txn)
			return nil, ctx.Err()
		case pktIn := <-replyCh:
			return c.processReply(txn, pktIn, res)
		case <-time.After(wait):
			rto *= 2
		}
	}
	c.tbl.Take(txn)
	if c.log != nil {
		c.recordOutcome(hex.EncodeToString(txn[:]), raddr.String(), "timeout", 0, "")
		c.log.Error("binding_timeout", string(stun.KindTimeout), map[string]any{
			"txn_id": hex.EncodeToString(txn[:]), "attempts": res.Attempts,
			"timeout_ms": c.cfg.Timeout.Milliseconds(),
		})
	}
	return nil, &stun.Error{Kind: stun.KindTimeout, Op: "client.Bind",
		Detail: fmt.Sprintf("no response after %d attempts", res.Attempts)}
}

func (c *Client) processReply(txn stun.TransactionID, in *inbound, res *Result) (*Result, error) {
	txnHex := hex.EncodeToString(txn[:])
	m, err := stun.UnmarshalMessage(in.raw)
	if err != nil {
		c.recordOutcome(txnHex, in.source, "bad_request", 0, err.Error())
		return nil, err
	}
	if m.TransactionID != txn {
		// The transaction table matches by id already; this is defence in
		// depth and yields a distinct failure category.
		err := &stun.Error{Kind: stun.KindSourceMismatch, Op: "client.processReply",
			Detail: "response transaction id mismatch"}
		c.recordOutcome(txnHex, in.source, "source_mismatch", 0, err.Error())
		return nil, err
	}
	if len(c.cfg.Key) > 0 && c.cfg.VerifyResponse {
		if verr := stun.VerifyMessageIntegrity(in.raw, c.cfg.Key); verr != nil {
			c.recordOutcome(txnHex, in.source, "integrity_failure", 0, verr.Error())
			return nil, verr
		}
	}
	if m.Class == stun.ClassErrorResponse {
		v, ok := m.Attribute(stun.AttrErrorCode)
		detail := "error response without ERROR-CODE attribute"
		code := 0
		if ok {
			if ec, derr := stun.DecodeErrorCode(v); derr == nil {
				code = ec.Code
				detail = ec.Reason
			}
		}
		res.ErrorCode = code
		res.Detail = detail
		c.recordOutcome(txnHex, in.source, "error_response", code, detail)
		if c.log != nil {
			c.log.Warn("binding_error_response", map[string]any{
				"txn_id": txnHex, "error_code": code, "reason": detail,
			})
		}
		return res, nil
	}
	if m.Class != stun.ClassSuccessResponse {
		err := &stun.Error{Kind: stun.KindInput, Op: "client.processReply",
			Detail: fmt.Sprintf("unexpected message class 0x%04x", uint16(m.Class))}
		c.recordOutcome(txnHex, in.source, "bad_request", 0, err.Error())
		return nil, err
	}
	v, ok := m.Attribute(stun.AttrXORMappedAddress)
	if !ok {
		err := &stun.Error{Kind: stun.KindInput, Op: "client.processReply",
			Detail: "success response missing XOR-MAPPED-ADDRESS"}
		c.recordOutcome(txnHex, in.source, "bad_request", 0, err.Error())
		return nil, err
	}
	addr, err := stun.DecodeXORMappedAddress(v, txn)
	if err != nil {
		c.recordOutcome(txnHex, in.source, "bad_request", 0, err.Error())
		return nil, err
	}
	res.Endpoint = addr
	c.recordOutcome(txnHex, in.source, "success", 0, "")
	if c.log != nil {
		c.log.Info("binding_success", map[string]any{
			"txn_id": txnHex, "family": familyOf(addr.IP),
			"endpoint": fmt.Sprintf("%s:%d", addr.IP, addr.Port),
			"attempts": res.Attempts,
		})
	}
	return res, nil
}

func (c *Client) recordOutcome(txn, server, outcome string, code int, detail string) {
	if c.cfg.Store == nil {
		return
	}
	runID := "stunc-no-logger"
	if c.log != nil {
		runID = c.log.RunID()
	}
	x := store.Exchange{
		RunID: runID, TxnID: txn, RemoteAddr: server,
		Family: "", Outcome: outcome, ErrorCode: code, Detail: detail,
	}
	_ = c.cfg.Store.RecordExchange(time.Now().UTC().Format(time.RFC3339Nano), x)
}

func familyOf(ip net.IP) string {
	if ip.To4() != nil {
		return "ipv4"
	}
	return "ipv6"
}
