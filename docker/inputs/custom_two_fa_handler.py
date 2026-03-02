"""Custom IBeam 2FA handler for IBKR SMS-based authentication.

Polls a file for the SMS code. The external process writes the code
to /srv/inputs/2fa_code.txt, this handler picks it up and returns it.

Timeout: 180 seconds (poll every 3s).
"""

import time
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

CODE_FILE = Path("/srv/inputs/2fa_code.txt")
POLL_INTERVAL = 3  # seconds
MAX_WAIT = 180  # seconds


class CustomTwoFaHandler:

    def __init__(self, driver, two_fa_el):
        self.driver = driver
        self.two_fa_el = two_fa_el

    def get_two_fa_code(self):
        """Poll for 2FA code from file. Returns code string or None."""
        logger.info("SMS 2FA handler: waiting for code in %s (max %ds)", CODE_FILE, MAX_WAIT)

        # Clear any stale code file
        if CODE_FILE.exists():
            try:
                old_code = CODE_FILE.read_text().strip()
                if old_code:
                    logger.info("Found existing code file, clearing stale content")
                    CODE_FILE.unlink()
            except Exception:
                pass

        elapsed = 0
        while elapsed < MAX_WAIT:
            if CODE_FILE.exists():
                try:
                    code = CODE_FILE.read_text().strip()
                    if code and len(code) >= 4:
                        logger.info("Got 2FA code: %s***", code[:2])
                        # Remove file after reading
                        CODE_FILE.unlink(missing_ok=True)
                        return code
                except Exception as e:
                    logger.warning("Error reading code file: %s", e)

            time.sleep(POLL_INTERVAL)
            elapsed += POLL_INTERVAL

            if elapsed % 30 == 0:
                logger.info("Still waiting for 2FA code... (%ds/%ds)", elapsed, MAX_WAIT)

        logger.error("2FA handler timed out after %ds — no code received", MAX_WAIT)
        return None
