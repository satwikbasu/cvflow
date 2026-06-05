"""Fernet TokenVault — key persistence, round-trip, chmod 600. No network."""

import pytest

from cvflow.auth import TokenVault, VaultError


def _mode(path):
    return oct(path.stat().st_mode & 0o777)


def test_create_generates_key_file_chmod_600(tmp_path):
    key_path = tmp_path / "keys" / ".fernet_key"
    TokenVault.create_or_load(key_path)
    assert key_path.exists()
    assert _mode(key_path) == "0o600"


def test_encrypt_decrypt_round_trip(tmp_path):
    vault = TokenVault.create_or_load(tmp_path / ".fernet_key")
    token = vault.encrypt("secret-cookie")
    assert token != b"secret-cookie"
    assert vault.decrypt(token) == "secret-cookie"


def test_second_load_reuses_same_key(tmp_path):
    key_path = tmp_path / ".fernet_key"
    v1 = TokenVault.create_or_load(key_path)
    token = v1.encrypt("hello")
    v2 = TokenVault.create_or_load(key_path)  # must load, not regenerate
    assert v2.decrypt(token) == "hello"


def test_decrypt_garbage_raises_vaulterror(tmp_path):
    vault = TokenVault.create_or_load(tmp_path / ".fernet_key")
    with pytest.raises(VaultError):
        vault.decrypt(b"not-a-valid-token")


def test_save_and_load_blob_round_trip_chmod_600(tmp_path):
    vault = TokenVault.create_or_load(tmp_path / ".fernet_key")
    blob = tmp_path / "state" / "cookies.enc"
    vault.save_blob(blob, '{"cookies": []}')
    assert _mode(blob) == "0o600"
    assert blob.read_bytes() != b'{"cookies": []}'  # stored encrypted
    assert vault.load_blob(blob) == '{"cookies": []}'
