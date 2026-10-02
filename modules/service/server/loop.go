package server

import (
	"context"
	"errors"
	"io"

	"hpacklab.local/service/frame"
)

// hbState is the in-flight HEADERS/CONTINUATION assembly.
type hbState struct {
	streamID  uint32
	endStream bool
	block     []byte
}

func (c *conn) loop(ctx context.Context) error {
	var hb *hbState
	// Streams whose END_STREAM we have seen; value tracks whether we replied.
	endSeen := map[uint32]bool{}

	for {
		if err := ctx.Err(); err != nil {
			return err
		}
		h, payload, err := c.fr.ReadFrame()
		if err != nil {
			if errors.Is(err, io.EOF) || errors.Is(err, io.ErrUnexpectedEOF) {
				return nil
			}
			return err
		}

		// While a header block is fragmented, only CONTINUATION is legal
		// (RFC 7540 section 6.10).
		if hb != nil && h.Type != frame.TypeContinuation {
			return c.fatal(errCodeProtocol, "frame other than CONTINUATION during header block")
		}

		switch h.Type {
		case frame.TypeSettings:
			if h.Stream != 0 {
				return c.fatal(errCodeProtocol, "SETTINGS on a stream")
			}
			if h.Flags&frame.FlagAck != 0 {
				continue
			}
			if err := c.applySettings(payload); err != nil {
				return c.fatal(errCodeProtocol, err.Error())
			}
			if err := c.fw.Settings(true); err != nil {
				return err
			}
			if err := c.flush(); err != nil {
				return err
			}

		case frame.TypePing:
			if h.Stream != 0 {
				return c.fatal(errCodeProtocol, "PING on a stream")
			}
			if h.Flags&frame.FlagAck == 0 {
				var pong [8]byte
				copy(pong[:], payload)
				if err := c.fw.Ping(true, pong); err != nil {
					return err
				}
				if err := c.flush(); err != nil {
					return err
				}
			}

		case frame.TypeWindowUpdate, frame.TypePriority:
			// Tolerated; flow-control values are not enforced in this
			// controlled, local-only service.

		case frame.TypeGoAway:
			c.log.Info("peer sent GOAWAY", "last_stream", c.lastStreamID)
			return nil

		case frame.TypeHeaders:
			if h.Stream == 0 || h.Stream%2 == 0 {
				return c.fatal(errCodeProtocol, "HEADERS on invalid stream id")
			}
			frag, err := stripHeadersPadding(payload, h.Flags)
			if err != nil {
				return c.fatal(errCodeProtocol, err.Error())
			}
			hb = &hbState{streamID: h.Stream, endStream: h.EndStream(), block: append([]byte(nil), frag...)}
			c.lastStreamID = h.Stream
			if h.EndHeaders() {
				if replied, err := c.completeHeaderBlock(ctx, hb); err != nil {
					return err
				} else if replied {
					endSeen[h.Stream] = true
				}
				hb = nil
			}

		case frame.TypeContinuation:
			if hb == nil || h.Stream != hb.streamID {
				return c.fatal(errCodeProtocol, "unexpected CONTINUATION")
			}
			hb.block = append(hb.block, payload...)
			if h.EndHeaders() {
				if _, err := c.completeHeaderBlock(ctx, hb); err != nil {
					return err
				}
				hb = nil
			}

		case frame.TypeData:
			if h.Stream == 0 {
				return c.fatal(errCodeProtocol, "DATA on stream 0")
			}
			// We do not consume request bodies meaningfully; END_STREAM is
			// the signal to answer if the HEADERS frame did not carry it.
			if h.EndStream() && !endSeen[h.Stream] {
				if err := c.respond(ctx, h.Stream); err != nil {
					return err
				}
				endSeen[h.Stream] = true
			}

		case frame.TypeRSTStream:
			c.log.Info("peer reset stream", "stream_id", h.Stream)

		default:
			// Ignore unknown frame types per RFC 7540 section 5.5.
		}
	}
}

// stripHeadersPadding removes the PADDED and PRIORITY prefixes and trailing
// padding from a HEADERS payload, returning the header block fragment.
func stripHeadersPadding(payload []byte, flags byte) ([]byte, error) {
	p := payload
	if flags&frame.FlagPadded != 0 {
		if len(p) < 1 {
			return nil, errors.New("PADDED HEADERS too short")
		}
		padLen := int(p[0])
		p = p[1:]
		if padLen >= len(p) {
			return nil, errors.New("padding longer than payload")
		}
		p = p[:len(p)-padLen]
	}
	if flags&frame.FlagPriority != 0 {
		if len(p) < 5 {
			return nil, errors.New("PRIORITY HEADERS too short")
		}
		p = p[5:]
	}
	return p, nil
}
