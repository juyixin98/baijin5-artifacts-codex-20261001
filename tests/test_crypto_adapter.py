"""密码适配层测试:真实密钥上的加解密、同态加、标量乘、序列化与拒绝路径。"""
import pytest

from app import crypto_adapter as crypto
from app import encoding
from app.errors import ErrorCategory, ServiceError

KEY_BITS = 1024


@pytest.fixture(scope="module")
def keypair():
    return crypto.generate_keypair(KEY_BITS)


def test_encrypt_decrypt_roundtrip(keypair, rlog):
    pub, priv = keypair
    for value in [0, 1, -1, 7, -7, 123456, -123456]:
        enc = crypto.encrypt_signed(pub, value)
        residue = crypto.decrypt_residue(priv, enc)
        got = encoding.decode_signed(residue, pub.n, bound=10**9)
        rlog("roundtrip", value=value, residue=str(residue), decoded=got,
             basis="加密-解密-居中解码应还原原值")
        assert got == value


def test_homomorphic_add_hand_computed(keypair, rlog):
    pub, priv = keypair
    acc = crypto.add(crypto.encrypt_signed(pub, 3),
                     crypto.encrypt_signed(pub, -2))
    got = encoding.decode_signed(crypto.decrypt_residue(priv, acc), pub.n,
                                 bound=100)
    rlog("homomorphic-add", inputs=[3, -2], expected=1, actual=got,
         basis="手算 3 + (-2) = 1")
    assert got == 1


def test_homomorphic_scalar_mul_negative(keypair, rlog):
    pub, priv = keypair
    enc = crypto.encrypt_signed(pub, 5)
    got = encoding.decode_signed(
        crypto.decrypt_residue(priv, crypto.scalar_mul(enc, -2)),
        pub.n, bound=100)
    rlog("scalar-mul", value=5, weight=-2, expected=-10, actual=got,
         basis="手算 5 * (-2) = -10,负标量必须支持")
    assert got == -10


def test_serialize_deserialize_roundtrip(keypair):
    pub, priv = keypair
    enc = crypto.encrypt_signed(pub, 42)
    blob = crypto.serialize(enc)
    assert isinstance(blob["c"], str) and blob["e"] == 0
    restored = crypto.deserialize(pub, int(blob["c"]), blob["e"])
    assert crypto.decrypt_residue(priv, restored) == 42


def test_deserialize_rejects_nonzero_exponent(keypair):
    pub, _ = keypair
    with pytest.raises(ServiceError) as exc_info:
        crypto.deserialize(pub, 12345, exponent=-2)
    assert exc_info.value.category is ErrorCategory.VALIDATION


def test_deserialize_rejects_out_of_range_ciphertext(keypair):
    pub, _ = keypair
    with pytest.raises(ServiceError) as exc_info:
        crypto.deserialize(pub, pub.n * pub.n, exponent=0)
    assert exc_info.value.category is ErrorCategory.OUT_OF_RANGE


def test_fingerprint_binds_to_key(keypair):
    pub, _ = keypair
    other_pub, _ = crypto.generate_keypair(KEY_BITS)
    fp1 = crypto.key_fingerprint(pub.n)
    assert fp1 == crypto.key_fingerprint(pub.n)      # 确定性
    assert fp1 != crypto.key_fingerprint(other_pub.n)  # 不同密钥不同指纹


def test_private_key_export_import_roundtrip(keypair, service):
    pub, priv = keypair
    blob = crypto.export_private_key(priv, service._fernet)
    restored = crypto.import_private_key(pub, blob, service._fernet)
    enc = crypto.encrypt_signed(pub, 99)
    assert crypto.decrypt_residue(restored, enc) == 99
