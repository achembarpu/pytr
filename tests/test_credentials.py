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


# ---------------------------------------------------------------------------
# Tests for environment variable precedence
# ---------------------------------------------------------------------------


class TestLoginEnvVars:
    """Credentials from PYTR_PHONE / PYTR_PIN have lower precedence than CLI
    args but higher precedence than stored credentials."""

    ENV_PHONE = "+491234567890"
    ENV_PIN = "9999"
    STORED_PHONE = "+499999999999"
    STORED_PIN = "0000"

    def test_env_vars_used_when_no_cli_args(self, tmp_path, monkeypatch):
        """PYTR_PHONE / PYTR_PIN are used when no CLI args are given."""
        base = tmp_path / ".pytr"
        monkeypatch.setattr("pytr.account.BASE_DIR", base)
        monkeypatch.setattr("pytr.account.CREDENTIALS_FILE", base / "credentials")
        monkeypatch.setenv("PYTR_PHONE", self.ENV_PHONE)
        monkeypatch.setenv("PYTR_PIN", self.ENV_PIN)

        with patch("pytr.account._read_credentials_from_keyring", return_value=None):
            with patch("pytr.account.TradeRepublicApi") as MockApi:
                with patch("pytr.account.get_settings"):
                    login()

        MockApi.assert_called_once()
        _, kwargs = MockApi.call_args
        assert kwargs["phone_no"] == self.ENV_PHONE
        assert kwargs["pin"] == self.ENV_PIN

    def test_cli_args_override_env_vars(self, tmp_path, monkeypatch):
        """Explicit CLI arguments take precedence over env vars."""
        base = tmp_path / ".pytr"
        monkeypatch.setattr("pytr.account.BASE_DIR", base)
        monkeypatch.setattr("pytr.account.CREDENTIALS_FILE", base / "credentials")
        monkeypatch.setenv("PYTR_PHONE", self.ENV_PHONE)
        monkeypatch.setenv("PYTR_PIN", self.ENV_PIN)

        cli_phone = "+498888888888"
        cli_pin = "8888"

        with patch("pytr.account.TradeRepublicApi") as MockApi:
            with patch("pytr.account.get_settings"):
                login(phone_no=cli_phone, pin=cli_pin)

        MockApi.assert_called_once()
        _, kwargs = MockApi.call_args
        assert kwargs["phone_no"] == cli_phone
        assert kwargs["pin"] == cli_pin

    def test_env_vars_override_stored_credentials(self, tmp_path, monkeypatch):
        """Env vars take precedence over keyring / legacy file."""
        base = tmp_path / ".pytr"
        base.mkdir()
        cred_file = base / "credentials"
        cred_file.write_text(f"{self.STORED_PHONE}\n{self.STORED_PIN}\n")
        monkeypatch.setattr("pytr.account.BASE_DIR", base)
        monkeypatch.setattr("pytr.account.CREDENTIALS_FILE", cred_file)
        monkeypatch.setenv("PYTR_PHONE", self.ENV_PHONE)
        monkeypatch.setenv("PYTR_PIN", self.ENV_PIN)

        with patch("pytr.account._read_credentials_from_keyring", return_value=None):
            with patch("pytr.account.TradeRepublicApi") as MockApi:
                with patch("pytr.account.get_settings"):
                    login()

        MockApi.assert_called_once()
        _, kwargs = MockApi.call_args
        assert kwargs["phone_no"] == self.ENV_PHONE
        assert kwargs["pin"] == self.ENV_PIN

    def test_env_phone_only_pin_from_stored(self, tmp_path, monkeypatch):
        """When only PYTR_PHONE is set, the PIN is taken from stored credentials."""
        base = tmp_path / ".pytr"
        base.mkdir()
        cred_file = base / "credentials"
        cred_file.write_text(f"{self.STORED_PHONE}\n{self.STORED_PIN}\n")
        monkeypatch.setattr("pytr.account.BASE_DIR", base)
        monkeypatch.setattr("pytr.account.CREDENTIALS_FILE", cred_file)
        monkeypatch.setenv("PYTR_PHONE", self.ENV_PHONE)
        monkeypatch.delenv("PYTR_PIN", raising=False)

        with patch("pytr.account._read_credentials_from_keyring", return_value=None):
            with patch("pytr.account.TradeRepublicApi") as MockApi:
                with patch("pytr.account.get_settings"):
                    login()

        MockApi.assert_called_once()
        _, kwargs = MockApi.call_args
        assert kwargs["phone_no"] == self.ENV_PHONE
        # PIN falls back to stored credentials because PYTR_PIN is not set.
        assert kwargs["pin"] == self.STORED_PIN

    def test_env_pin_only_phone_from_stored(self, tmp_path, monkeypatch):
        """When only PYTR_PIN is set, the phone number is taken from stored credentials."""
        base = tmp_path / ".pytr"
        base.mkdir()
        cred_file = base / "credentials"
        cred_file.write_text(f"{self.STORED_PHONE}\n{self.STORED_PIN}\n")
        monkeypatch.setattr("pytr.account.BASE_DIR", base)
        monkeypatch.setattr("pytr.account.CREDENTIALS_FILE", cred_file)
        monkeypatch.delenv("PYTR_PHONE", raising=False)
        monkeypatch.setenv("PYTR_PIN", self.ENV_PIN)

        with patch("pytr.account._read_credentials_from_keyring", return_value=None):
            with patch("pytr.account.TradeRepublicApi") as MockApi:
                with patch("pytr.account.get_settings"):
                    login()

        MockApi.assert_called_once()
        _, kwargs = MockApi.call_args
        assert kwargs["phone_no"] == self.STORED_PHONE
        assert kwargs["pin"] == self.ENV_PIN

    def test_stored_skipped_when_both_env_vars_set(self, tmp_path, monkeypatch):
        """When both env vars are set, stored credentials are not read."""
        base = tmp_path / ".pytr"
        monkeypatch.setattr("pytr.account.BASE_DIR", base)
        monkeypatch.setattr("pytr.account.CREDENTIALS_FILE", base / "credentials")
        monkeypatch.setenv("PYTR_PHONE", self.ENV_PHONE)
        monkeypatch.setenv("PYTR_PIN", self.ENV_PIN)

        with patch("pytr.account._read_credentials_from_keyring") as mock_read:
            with patch("pytr.account.TradeRepublicApi"):
                with patch("pytr.account.get_settings"):
                    login()
            # Stored credentials should not have been consulted because both
            # env vars satisfied the requirement.
            mock_read.assert_not_called()


# ---------------------------------------------------------------------------
# Tests for CLI-args security warning
# ---------------------------------------------------------------------------


class TestCliArgsWarning:
    """When --phone_no or --pin are passed on the command line, main()
    emits a visible warning to stderr (CWE-214 mitigation)."""

    def test_phone_no_triggers_warning(self, monkeypatch, capsys):
        """Warning is printed to stderr when --phone_no is passed."""
        import sys

        from pytr.main import main

        monkeypatch.setattr(sys, "argv", ["pytr", "login", "-n", PHONE])
        with patch("pytr.main.login"):
            with patch("pytr.main.get_logger"):
                try:
                    main()
                except SystemExit:
                    pass
        captured = capsys.readouterr()
        assert "process list" in captured.err, f"stderr was: {captured.err!r}"
        assert PHONE not in captured.err, "warning must not contain the credential value"

    def test_pin_triggers_warning(self, monkeypatch, capsys):
        """Warning is printed to stderr when --pin is passed."""
        import sys

        from pytr.main import main

        monkeypatch.setattr(sys, "argv", ["pytr", "login", "-p", PIN])
        with patch("pytr.main.login"):
            with patch("pytr.main.get_logger"):
                try:
                    main()
                except SystemExit:
                    pass
        captured = capsys.readouterr()
        assert "process list" in captured.err, f"stderr was: {captured.err!r}"
        assert PIN not in captured.err, "warning must not contain the credential value"

    def test_no_warning_without_cli_creds(self, monkeypatch, capsys):
        """No warning when credentials are not passed on the command line."""
        import sys

        from pytr.main import main

        monkeypatch.setattr(sys, "argv", ["pytr", "login"])
        with patch("pytr.main.login"):
            with patch("pytr.main.get_logger"):
                try:
                    main()
                except SystemExit:
                    pass
        captured = capsys.readouterr()
        assert "process list" not in captured.err, f"stderr was: {captured.err!r}"
