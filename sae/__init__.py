"""sae - Segmented Authenticated Encryption service.

Large messages are split into chunks; each chunk is encrypted with an
AEAD whose nonce and AAD bind the message identity, the chunk sequence
number and the end-of-stream flag.  Plaintext is only released after the
whole stream has been authenticated.
"""

__version__ = "0.1.0"
