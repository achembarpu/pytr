#!/usr/bin/env python3
import logging
import sys

from pytr.main import main

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log = logging.getLogger(__name__)
        log.info("Exiting...")
        sys.exit()
    except Exception as e:
        log = logging.getLogger(__name__)
        log.error("An unexpected error occurred (%s)", type(e).__name__)
        log.debug("Error details: %s", e)
        sys.exit(1)
