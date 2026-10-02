package server

import (
	"log/slog"

	"hpacklab.local/hpack"
)

// eventLogger implements hpack.Observer, turning decoder events into
// structured log lines carrying the connection and request identity. It is
// reset per header block.
type eventLogger struct {
	log    *slog.Logger
	connID string
	// requestID/streamID are set at the start of each block.
	requestID string
	streamID  uint32
	echo      bool
}

func newEventLogger(log *slog.Logger, connID string, echoHeaders bool) *eventLogger {
	return &eventLogger{log: log, connID: connID, echo: echoHeaders}
}

func (l *eventLogger) beginBlock(requestID string, streamID uint32) {
	l.requestID = requestID
	l.streamID = streamID
}

func (l *eventLogger) OnEvent(ev hpack.Event) {
	attrs := []any{
		"conn_id", l.connID,
		"request_id", l.requestID,
		"stream_id", l.streamID,
		"step", stepName(ev.Kind),
		"offset", ev.Offset,
		"table_size", ev.TableSize,
		"block_bytes", ev.BlockBytes,
	}
	switch ev.Kind {
	case hpack.EvSizeUpdate:
		attrs = append(attrs, "old_max", ev.OldMax, "new_max", ev.NewMax, "evicted", ev.Evicted)
	case hpack.EvEviction:
		attrs = append(attrs, "evicted", ev.Evicted)
	case hpack.EvIndexed:
		attrs = append(attrs, "index", ev.Index)
	}
	if l.echo && ev.EmittedName != "" {
		attrs = append(attrs, "name", ev.EmittedName)
		if !ev.Sensitive {
			attrs = append(attrs, "value", ev.EmittedValue)
		} else {
			// Sensitive values are logged by presence only, never in clear.
			attrs = append(attrs, "value", "<redacted>", "sensitive", true)
		}
	}
	l.log.Debug("hpack step", attrs...)
}

func stepName(k hpack.EventKind) string {
	switch k {
	case hpack.EvBlockStart:
		return "block-start"
	case hpack.EvSizeUpdate:
		return "dynamic-table-size-update"
	case hpack.EvIndexed:
		return "indexed"
	case hpack.EvLiteralIndexed:
		return "literal-incremental"
	case hpack.EvLiteral:
		return "literal-without-indexing"
	case hpack.EvLiteralNever:
		return "literal-never-indexed"
	case hpack.EvBlockEnd:
		return "block-end"
	case hpack.EvEviction:
		return "eviction"
	default:
		return "unknown"
	}
}
