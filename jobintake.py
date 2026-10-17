#!/usr/bin/env python3
"""jobintake — collect job postings from many sources, classify them, export to CSV.

    python3 jobintake.py run            collect everything → jobs.csv + jobs_lite.csv
    python3 jobintake.py --help         all commands

The code lives in the intake/ package next to this file.
"""

import sys

if sys.version_info < (3, 8):
    sys.exit("jobintake needs Python 3.8 or newer")

from intake.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
