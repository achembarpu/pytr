import json
import os
import sys
import time
from getpass import getpass

from pygments import formatters, highlight, lexers

from .api import (
    BASE_DIR,
    CREDENTIALS_FILE,
    TradeRepublicApi,
    _delete_credentials_file,
    _read_credentials_from_keyring,
    _store_credentials_to_keyring,
)
from .utils import get_logger


def get_settings(tr):
    formatted_json = json.dumps(tr.settings(), indent=2)
    if sys.stdout.isatty():
        colorful_json = highlight(formatted_json, lexers.JsonLexer(), formatters.TerminalFormatter())
        return colorful_json
    else:
        return formatted_json


def login(phone_no=None, pin=None, store_credentials=False, waf_token="playwright"):
    """
    Handle credentials parameters and store to credentials file if requested.
    If no parameters are set but are needed then ask for input.

    Credential precedence (highest to lowest):
      1. Explicit CLI arguments (phone_no / pin parameters)
      2. Environment variables (PYTR_PHONE / PYTR_PIN)
      3. Stored credentials (system keyring, then legacy file ~/.pytr/credentials)
      4. Interactive prompt (stdin / getpass)
    """
    log = get_logger(__name__)
    save_cookies = True
    read_from_stored = False

    # ---- Step 1: Environment variables (safer than CLI args) ----
    if phone_no is None:
        phone_no = os.environ.get("PYTR_PHONE")
        if phone_no:
            log.debug("Using phone number from PYTR_PHONE environment variable")
    if pin is None:
        pin = os.environ.get("PYTR_PIN")
        if pin:
            log.debug("Using PIN from PYTR_PIN environment variable")

    # ---- Step 2: Stored credentials (keyring, then legacy file) ----
    # Read stored credentials whenever at least one credential is still missing.
    stored_phone = None
    stored_pin = None
    if phone_no is None or pin is None:
        keyring_creds = _read_credentials_from_keyring()
        if keyring_creds is not None:
            stored_phone, stored_pin = keyring_creds
            phone_no_masked = stored_phone[:-8] + "********"
            pin_masked = len(stored_pin) * "*"
            log.debug("Using credentials from system keyring. Phone: %s, PIN: %s", phone_no_masked, pin_masked)
            read_from_stored = True
        elif CREDENTIALS_FILE.is_file():
            with open(CREDENTIALS_FILE) as f:
                lines = f.readlines()
            stored_phone = lines[0].strip()
            stored_pin = lines[1].strip()
            phone_no_masked = stored_phone[:-8] + "********"
            pin_masked = len(stored_pin) * "*"
            log.debug("Using credentials from legacy file %s. Phone: %s, PIN: %s", CREDENTIALS_FILE, phone_no_masked, pin_masked)
            read_from_stored = True

    # Keep CLI/env values when they are already set; only fill in missing ones.
    if phone_no is None:
        phone_no = stored_phone
    if pin is None:
        pin = stored_pin

    # ---- Step 3: Interactive prompts (last resort) ----
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    if phone_no is None:
        print("Please enter your TradeRepublic phone number in the format +4912345678:")
        phone_no = input()

    if pin is None:
        print("Please enter your TradeRepublic pin:")
        pin = getpass(prompt="Pin (Input is hidden):")

    if read_from_stored or store_credentials:
        if store_credentials:
            stored = _store_credentials_to_keyring(phone_no, pin)
            if stored:
                log.info("Stored credentials in system keyring (cookies: %s)", BASE_DIR)
                # Migration: remove the legacy plaintext file if it exists.
                _delete_credentials_file()
            else:
                log.warning("Keyring unavailable, falling back to legacy credentials file")
                with open(CREDENTIALS_FILE, "w") as f:
                    f.writelines([phone_no + "\n", pin + "\n"])
                os.chmod(CREDENTIALS_FILE, 0o600)
                log.info("Storing credentials/cookies in %s", BASE_DIR)
    else:
        save_cookies = False

    tr = TradeRepublicApi(phone_no=phone_no, pin=pin, save_cookies=save_cookies, waf_token=waf_token)

    # Use same login as app.traderepublic.com
    if not tr.resume_websession():
        try:
            countdown = tr.initiate_weblogin()
        except ValueError as e:
            log.error("Login failed: API returned an unexpected response (%s)", type(e).__name__)
            log.debug("Login error details: %s", e)
            sys.exit(1)
        request_time = time.time()
        print("Enter the code you received to your mobile app as a notification.")
        print(f"Enter nothing if you want to receive the (same) code as SMS. (Countdown: {countdown})")
        code = input("Code: ")
        if code == "":
            countdown = countdown - (time.time() - request_time)
            for remaining in range(int(countdown)):
                print(
                    f"Need to wait {int(countdown - remaining)} seconds before requesting SMS...",
                    end="\r",
                )
                time.sleep(1)
            print()
            tr.resend_weblogin()
            code = input("SMS requested. Enter the confirmation code:")
        tr.complete_weblogin(code)
        log.info("Logged in.")

    log.debug(get_settings(tr))
    return tr
