package wire

import (
	"fmt"
)

// Decode parses one CoAP datagram. It performs structural validation only:
// version, TKL bounds, strictly ascending options, legal block options. Any
// semantic policy (duplicate MIDs, block continuity) belongs elsewhere.
func Decode(datagram []byte) (*Message, error) {
	if len(datagram) < 4 {
		return nil, fmt.Errorf("%w: datagram shorter than 4 bytes", ErrMalformed)
	}
	first := datagram[0]
	if ver := first >> 6; ver != Version {
		return nil, fmt.Errorf("%w: version %d", ErrVersion, ver)
	}
	t := Type((first >> 4) & 0x3)
	tkl := int(first & 0xf)
	code := Code(datagram[1])
	mid := uint16(datagram[2])<<8 | uint16(datagram[3])

	if 4+tkl > len(datagram) {
		return nil, fmt.Errorf("%w: tkl=%d but only %d bytes remain",
			ErrBadTokenLength, tkl, len(datagram)-4)
	}
	token := append([]byte(nil), datagram[4:4+tkl]...)
	m := &Message{Type: t, Code: code, MessageID: mid, Token: token}

	pos := 4 + tkl
	prevNumber := 0
	for pos < len(datagram) {
		b := datagram[pos]
		pos++
		if b == PayloadMarker {
			if pos >= len(datagram) {
				return nil, ErrEmptyPayload
			}
			m.Payload = append([]byte(nil), datagram[pos:]...)
			pos = len(datagram)
			break
		}
		deltaNib := int(b >> 4)
		lenNib := int(b & 0xf)
		if deltaNib == 15 || lenNib == 15 {
			return nil, fmt.Errorf("%w: reserved nibble 15 at byte %d",
				ErrMalformed, pos-1)
		}
		delta, n, err := readExt(datagram, pos, deltaNib)
		if err != nil {
			return nil, err
		}
		pos += n
		optLen, n, err := readExt(datagram, pos, lenNib)
		if err != nil {
			return nil, err
		}
		pos += n
		if pos+optLen > len(datagram) {
			return nil, fmt.Errorf("%w: option value runs past datagram at %d",
				ErrMalformed, pos)
		}
		number := prevNumber + delta
		if number < prevNumber {
			return nil, fmt.Errorf("%w: option numbers not ascending", ErrOptionOrder)
		}
		value := append([]byte(nil), datagram[pos:pos+optLen]...)
		if number == OptBlock1 || number == OptBlock2 {
			if _, err := DecodeBlock(value); err != nil {
				return nil, err
			}
		}
		m.Options = append(m.Options, Option{Number: number, Value: value})
		prevNumber = number
		pos += optLen
	}
	if err := validateDecoded(m); err != nil {
		return nil, err
	}
	return m, nil
}

// readExt decodes a 4-bit delta/length nibble plus its extension bytes.
func readExt(d []byte, pos, nib int) (int, int, error) {
	switch nib {
	case 13:
		if pos >= len(d) {
			return 0, 0, fmt.Errorf("%w: missing 1-byte extension", ErrMalformed)
		}
		return 13 + int(d[pos]), 1, nil
	case 14:
		if pos+1 >= len(d) {
			return 0, 0, fmt.Errorf("%w: missing 2-byte extension", ErrMalformed)
		}
		return 269 + int(d[pos])<<8 + int(d[pos+1]), 2, nil
	default:
		return nib, 0, nil
	}
}

func validateDecoded(m *Message) error {
	if m.Code == CodeEmpty {
		if !m.IsEmpty() {
			return fmt.Errorf("%w: code 0.00 carries token/options/payload",
				ErrMalformed)
		}
		return nil
	}
	if len(m.Token) > MaxTokenLength {
		return fmt.Errorf("%w: token length %d", ErrBadTokenLength, len(m.Token))
	}
	return nil
}
