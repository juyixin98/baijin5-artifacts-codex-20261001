package protocol

import (
	"encoding/hex"
	"net"
	"sync/atomic"

	"coapblockwise/internal/diag"
	"coapblockwise/internal/wire"
)

// sendDatagram writes one datagram, logging only a payload digest on failure.
func (e *Endpoint) sendDatagram(raw []byte, addr *net.UDPAddr,
	ref diag.Ref, phase string,
) {
	if _, err := e.conn.WriteToUDP(raw, addr); err != nil {
		e.log.Error(ref, "UDP write failed", "phase", phase,
			"err", err.Error(), "bytes", len(raw))
	}
}

// responseWriter implements Responder. A handler may call Respond at most
// once; the response bytes are cached for MID-dedup replay.
type responseWriter struct {
	e       *Endpoint
	req     *wire.Message
	addr    *net.UDPAddr
	encoded []byte
	once    atomic.Bool
}

// Respond emits a piggybacked ACK carrying code/opts/payload and echoes the
// request token (the token associates the pair; the MID stays the request MID).
func (w *responseWriter) Respond(code wire.Code, opts wire.Options, payload []byte) {
	if !w.once.CompareAndSwap(false, true) {
		mid := w.req.MessageID
		w.e.log.Error(diag.Ref{MessageID: &mid,
			TokenHex: hex.EncodeToString(w.req.Token)},
			"handler attempted a second Respond; ignoring",
			"decision", "reject")
		return
	}
	resp := wire.ResponseTo(w.req, code)
	resp.Options = append(wire.Options(nil), opts...)
	resp.Payload = append([]byte(nil), payload...)
	raw, err := resp.Encode()
	if err != nil {
		w.e.log.Error(diag.Ref{MessageID: &w.req.MessageID,
			TokenHex: hex.EncodeToString(w.req.Token)},
			"failed to encode handler response", "err", err.Error())
		raw, _ = wire.EmptyACK(w.req.MessageID).Encode()
	}
	w.encoded = raw
}
