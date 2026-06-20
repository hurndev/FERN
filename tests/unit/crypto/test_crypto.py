
from fern.crypto.keys import Keypair
from fern.crypto.hashes import sha256_hex
from fern.crypto.encoding import (
    to_hex,
    from_hex,
    is_valid_pubkey_hex,
    is_valid_sig_hex,
)


class TestKeypair:
    def test_generate_creates_valid_keypair(self) -> None:
        kp = Keypair.generate()
        assert len(kp.pubkey_bytes) == 32
        assert len(kp.privkey_bytes) == 32
        assert len(kp.pubkey_hex) == 64
        assert len(kp.privkey_hex) == 64

    def test_sign_and_verify_roundtrip(self) -> None:
        kp = Keypair.generate()
        msg = b"hello, fern"
        sig = kp.sign(msg)
        assert len(sig) == 64
        assert kp.verify(kp.pubkey_bytes, msg, sig)

    def test_invalid_signature_fails(self) -> None:
        kp = Keypair.generate()
        msg = b"hello, fern"
        sig = kp.sign(msg)
        tampered = msg + b"!"
        assert not kp.verify(kp.pubkey_bytes, tampered, sig)

    def test_wrong_key_fails(self) -> None:
        alice = Keypair.generate()
        bob = Keypair.generate()
        msg = b"hello"
        sig = alice.sign(msg)
        assert not bob.verify(bob.pubkey_bytes, msg, sig)

    def test_from_privkey_roundtrip(self) -> None:
        orig = Keypair.generate()
        kp = Keypair.from_privkey(orig.privkey_bytes)
        assert kp.pubkey_bytes == orig.pubkey_bytes
        assert kp.pubkey_hex == orig.pubkey_hex


class TestHashes:
    def test_sha256_hex_is_lowercase(self) -> None:
        h = sha256_hex(b"test")
        assert h == h.lower()
        assert len(h) == 64

    def test_sha256_hex_deterministic(self) -> None:
        a = sha256_hex(b"hello")
        b = sha256_hex(b"hello")
        assert a == b

    def test_sha256_hex_different_for_different_input(self) -> None:
        a = sha256_hex(b"a")
        b = sha256_hex(b"b")
        assert a != b


class TestEncoding:
    def test_to_hex_lowercase(self) -> None:
        h = to_hex(bytes([0xAB, 0xCD, 0xEF]))
        assert h == "abcdef"

    def test_from_hex_roundtrip(self) -> None:
        data = bytes([0, 1, 2, 255, 254, 253])
        assert from_hex(to_hex(data)) == data

    def test_is_valid_pubkey_hex(self) -> None:
        assert is_valid_pubkey_hex("a" * 64)
        assert not is_valid_pubkey_hex("A" * 64)
        assert not is_valid_pubkey_hex("a" * 63)

    def test_is_valid_sig_hex(self) -> None:
        assert is_valid_sig_hex("f" * 128)
        assert not is_valid_sig_hex("f" * 127)
