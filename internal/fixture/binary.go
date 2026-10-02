package fixture

import (
	"bytes"
	"fmt"
)

// BinaryMessage deterministically builds a message whose body is not valid
// UTF-8 and contains NUL, 0xFF and embedded CRLF. It is transported as an
// IMAP literal and lets tests assert byte-for-byte identity (length counted
// in bytes, not runes).
func BinaryMessage(n int) []byte {
	var b bytes.Buffer
	fmt.Fprintf(&b, "Message-ID: <binary-%d@imaplite.local>\r\n", n)
	fmt.Fprintf(&b, "Date: Wed, 01 Oct 2025 12:00:%02d +0000\r\n", n)
	fmt.Fprintf(&b, "From: Binary Robot <robot%d@imaplite.local>\r\n", n)
	b.WriteString("To: Tester <tester@imaplite.local>\r\n")
	b.WriteString("Subject: binary literal probe\r\n")
	b.WriteString("MIME-Version: 1.0\r\n")
	b.WriteString("Content-Type: application/octet-stream\r\n")
	b.WriteString("Content-Transfer-Encoding: binary\r\n")
	b.WriteString("\r\n")
	// Exact, reproducible payload: byte values 0..255 three times, plus an
	// embedded CRLF in the middle and a trailing non-newline byte.
	for round := 0; round < 3; round++ {
		for v := 0; v < 256; v++ {
			b.WriteByte(byte(v))
		}
		b.WriteString("\r\n")
	}
	b.Write([]byte{0x00, 0xFF, 0xFE, 0x01})
	return b.Bytes()
}
