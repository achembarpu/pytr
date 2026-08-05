"""Tests for credential storage via keyring and legacy file fallback."""

import logging
import os
from unittest.mock import patch

from pytr.account import login
from pytr.api import (
    _delete_credentials_file,
    _read_credentials_from_keyring,
    _store_credentials_to_keyring,
)

PHONE = "+491234567890"
PIN = "1234"


# ---------------------------------------------------------------------------
# Unit tests for the keyring helpers
# ---------------------------------------------------------------------------


class TestKeyringHelpers:
    def test_read_returns_none_when_no_credentials(self):
        """_read_credentials_from_keyring returns None when keyring has no entry."""
        with patch("pytr.api.keyring") as mock_kr:
            mock_kr.get_password.return_value = None
            assert _read_credentials_from_keyring() is None

    def test_read_returns_tuple_when_both_present(self):
        """_read_credentials_from_keyring returns (phone, pin) when both exist."""
        with patch("pytr.api.keyring") as mock_kr:
            mock_kr.get_password.side_effect = lambda svc, key: {  # noqa: ARG005
                "phone": PHONE,
                "pin": PIN,
            }[key]
            result = _read_credentials_from_keyring()
            assert result == (PHONE, PIN)

    def test_read_returns_none_when_phone_missing(self):
        """_read_credentials_from_keyring returns None when only pin exists."""
        with patch("pytr.api.keyring") as mock_kr:
            mock_kr.get_password.side_effect = lambda svc, key: {  # noqa: ARG005
                "phone": None,
                "pin": PIN,
            }[key]
            assert _read_credentials_from_keyring() is None

    def test_read_returns_none_when_pin_missing(self):
        """_read_credentials_from_keyring returns None when only phone exists."""
        with patch("pytr.api.keyring") as mock_kr:
            mock_kr.get_password.side_effect = lambda svc, key: {  # noqa: ARG005
                "phone": PHONE,
                "pin": None,
            }[key]
            assert _read_credentials_from_keyring() is None

    def test_read_returns_none_on_keyring_error(self):
        """_read_credentials_from_keyring returns None when keyring raises KeyringError."""
        import keyring.errors

        with patch("pytr.api.keyring") as mock_kr:
            mock_kr.get_password.side_effect = keyring.errors.KeyringError("locked")
            mock_kr.errors = keyring.errors
            assert _read_credentials_from_keyring() is None

    def test_store_returns_true_on_success(self):
        """_store_credentials_to_keyring returns True when store succeeds."""
        with patch("pytr.api.keyring") as mock_kr:
            assert _store_credentials_to_keyring(PHONE, PIN) is True
            assert mock_kr.set_password.call_count == 2
            mock_kr.set_password.assert_any_call("pytr", "phone", PHONE)
            mock_kr.set_password.assert_any_call("pytr", "pin", PIN)

    def test_store_returns_false_on_keyring_error(self):
        """_store_credentials_to_keyring returns False when keyring raises KeyringError."""
        import keyring.errors

        with patch("pytr.api.keyring") as mock_kr:
            mock_kr.set_password.side_effect = keyring.errors.KeyringError("no backend")
            mock_kr.errors = keyring.errors
            assert _store_credentials_to_keyring(PHONE, PIN) is False


# ---------------------------------------------------------------------------
# Tests for _delete_credentials_file
# ---------------------------------------------------------------------------


class TestDeleteCredentialsFile:
    def test_deletes_file_when_present(self, tmp_path, monkeypatch):
        """Legacy file is removed when it exists."""
        cred_file = tmp_path / "credentials"
        cred_file.write_text(f"{PHONE}\n{PIN}\n")
        monkeypatch.setattr("pytr.api.CREDENTIALS_FILE", cred_file)
        _delete_credentials_file()
        assert not cred_file.exists()

    def test_noop_when_file_absent(self, tmp_path, monkeypatch):
        """No error when the legacy file is already absent."""
        cred_file = tmp_path / "nonexistent"
        monkeypatch.setattr("pytr.api.CREDENTIALS_FILE", cred_file)
        _delete_credentials_file()  # must not raise


# ---------------------------------------------------------------------------
# Integration tests for login()
# ---------------------------------------------------------------------------


class TestLoginKeyringSuccess:
    """When keyring works, credentials come from keyring and legacy file is deleted."""

    def test_reads_from_keyring(self, tmp_path, monkeypatch):
        """login() returns a TradeRepublicApi with correct phone_no/pin from keyring."""
        base = tmp_path / ".pytr"
        monkeypatch.setattr("pytr.account.BASE_DIR", base)
        monkeypatch.setattr("pytr.account.CREDENTIALS_FILE", base / "credentials")

        with patch("pytr.account._read_credentials_from_keyring") as mock_read:
            mock_read.return_value = (PHONE, PIN)
            with patch("pytr.account.TradeRepublicApi") as MockApi:
                with patch("pytr.account.get_settings"):  # suppress JSON serialization
                    login()
                MockApi.assert_called_once()
                _, kwargs = MockApi.call_args
                assert kwargs["phone_no"] == PHONE
                assert kwargs["pin"] == PIN
                assert kwargs["save_cookies"] is True

    def test_stores_credentials_and_deletes_legacy(self, tmp_path, monkeypatch):
        """With --store-credentials, login() stores to keyring and deletes the old file."""
        base = tmp_path / ".pytr"
        base.mkdir()
        cred_file = base / "credentials"
        cred_file.write_text("old_phone\nold_pin\n")  # legacy file exists
        monkeypatch.setattr("pytr.account.BASE_DIR", base)
        monkeypatch.setattr("pytr.account.CREDENTIALS_FILE", cred_file)

        with patch("pytr.account._read_credentials_from_keyring", return_value=None):
            with patch("pytr.account._store_credentials_to_keyring", return_value=True) as mock_store:
                with patch("pytr.account._delete_credentials_file") as mock_delete:
                    with patch("pytr.account.TradeRepublicApi"):
                        with patch("pytr.account.get_settings"):  # suppress JSON serialization
                            login(phone_no=PHONE, pin=PIN, store_credentials=True)
                        mock_store.assert_called_once_with(PHONE, PIN)
                        mock_delete.assert_called_once()

    def test_pin_not_logged(self, tmp_path, monkeypatch, caplog):
        """Login log messages never contain the raw PIN value."""
        base = tmp_path / ".pytr"
        monkeypatch.setattr("pytr.account.BASE_DIR", base)
        monkeypatch.setattr("pytr.account.CREDENTIALS_FILE", base / "credentials")

        with patch("pytr.account._read_credentials_from_keyring") as mock_read:
            mock_read.return_value = (PHONE, PIN)
            with patch("pytr.account.TradeRepublicApi"):
                with patch("pytr.account.get_settings"):  # suppress JSON serialization
                    with caplog.at_level(logging.DEBUG, logger="account"):
                        login()
        # All log messages must not contain the raw PIN.
        for record in caplog.records:
            assert PIN not in record.getMessage(), f"PIN leaked in log: {record.getMessage()}"

    def test_phone_masked_in_logs(self, tmp_path, monkeypatch, caplog):
        """Login log messages mask the phone number."""
        base = tmp_path / ".pytr"
        monkeypatch.setattr("pytr.account.BASE_DIR", base)
        monkeypatch.setattr("pytr.account.CREDENTIALS_FILE", base / "credentials")

        with patch("pytr.account._read_credentials_from_keyring") as mock_read:
            mock_read.return_value = (PHONE, PIN)
            with patch("pytr.account.TradeRepublicApi"):
                with patch("pytr.account.get_settings"):  # suppress JSON serialization
                    with caplog.at_level(logging.DEBUG, logger="account"):
                        login()
        # Logs should contain the masked phone (last 8 digits replaced with *)
        assert any("********" in r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG)


class TestLoginKeyringUnavailable:
    """When keyring is unavailable, login() falls back to the legacy file."""

    def test_reads_from_file_when_keyring_fails(self, tmp_path, monkeypatch, caplog):
        """login() reads from plaintext file when keyring returns None."""
        base = tmp_path / ".pytr"
        base.mkdir()
        cred_file = base / "credentials"
        cred_file.write_text(f"{PHONE}\n{PIN}\n")
        os.chmod(cred_file, 0o600)

        monkeypatch.setattr("pytr.account.BASE_DIR", base)
        monkeypatch.setattr("pytr.account.CREDENTIALS_FILE", cred_file)

        with patch("pytr.account._read_credentials_from_keyring", return_value=None):
            with patch("pytr.account.TradeRepublicApi") as MockApi:
                with patch("pytr.account.get_settings"):  # suppress JSON serialization
                    with caplog.at_level(logging.DEBUG, logger="account"):
                        login()
                MockApi.assert_called_once()
                _, kwargs = MockApi.call_args
                assert kwargs["phone_no"] == PHONE
                assert kwargs["pin"] == PIN

        # Verify the log message indicates file source.
        debug_messages = [r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG]
        assert any("legacy file" in msg for msg in debug_messages)

    def test_store_falls_back_to_file_when_keyring_fails(self, tmp_path, monkeypatch, caplog):
        """When keyring store fails, login() writes to file with chmod 0o600."""
        base = tmp_path / ".pytr"
        cred_file = base / "credentials"
        monkeypatch.setattr("pytr.account.BASE_DIR", base)
        monkeypatch.setattr("pytr.account.CREDENTIALS_FILE", cred_file)

        with patch("pytr.account._read_credentials_from_keyring", return_value=None):
            with patch("pytr.account._store_credentials_to_keyring", return_value=False):
                with patch("pytr.account.TradeRepublicApi"):
                    with patch("pytr.account.get_settings"):  # suppress JSON serialization
                        with caplog.at_level(logging.WARNING, logger="account"):
                            login(phone_no=PHONE, pin=PIN, store_credentials=True)

        # File must exist with correct content and permissions.
        assert cred_file.exists()
        assert cred_file.read_text() == f"{PHONE}\n{PIN}\n"
        assert cred_file.stat().st_mode & 0o777 == 0o600

        # Warning must have been logged.
        warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any("Keyring unavailable" in msg for msg in warnings)

    def test_no_warning_when_not_storing(self, tmp_path, monkeypatch, caplog):
        """When store_credentials is False, no warning about keyring is logged."""
        base = tmp_path / ".pytr"
        monkeypatch.setattr("pytr.account.BASE_DIR", base)
        monkeypatch.setattr("pytr.account.CREDENTIALS_FILE", base / "credentials")

        with patch("pytr.account._read_credentials_from_keyring", return_value=None):
            with patch("pytr.account.TradeRepublicApi"):
                with patch("pytr.account.get_settings"):  # suppress JSON serialization
                    with caplog.at_level(logging.WARNING, logger="account"):
                        login(phone_no=PHONE, pin=PIN, store_credentials=False)

        warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert not any("Keyring" in msg for msg in warnings)


class TestLoginInMemoryPath:
    """The non-store (in-memory) path must not touch keyring or file for writing."""

    def test_no_storage_when_store_credentials_is_false(self, tmp_path, monkeypatch):
        """login() without --store-credentials does not attempt to persist credentials."""
        base = tmp_path / ".pytr"
        monkeypatch.setattr("pytr.account.BASE_DIR", base)
        monkeypatch.setattr("pytr.account.CREDENTIALS_FILE", base / "credentials")

        with patch("pytr.account._read_credentials_from_keyring", return_value=None):
            with patch("pytr.account._store_credentials_to_keyring") as mock_store:
                with patch("pytr.account.TradeRepublicApi") as MockApi:
                    with patch("pytr.account.get_settings"):  # suppress JSON serialization
                        login(phone_no=PHONE, pin=PIN, store_credentials=False)

        mock_store.assert_not_called()
        MockApi.assert_called_once()
        _, kwargs = MockApi.call_args
        assert kwargs["save_cookies"] is False
