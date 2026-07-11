#!/usr/bin/env python3
"""Docker HEALTHCHECK probe for the aniGamerPlus api/scheduler containers.

Standalone stdlib-only script (deliberately outside ``backend/app``) so it
can be invoked directly by ``HEALTHCHECK`` / compose ``healthcheck.test``
without pulling in the application package. Exits 0 on an HTTP response
under 400, non-zero otherwise — matching Docker's healthy/unhealthy exit
code contract.

Usage:
    healthcheck.py <url>
    healthcheck.py <url> <header-name> <env-var-holding-header-value>

The second form is for the scheduler's ``/internal/health``, which is
guarded by the ``X-Internal-Secret`` header:

    healthcheck.py http://localhost:5001/internal/health X-Internal-Secret ANIGAMERPLUS_INTERNAL_SECRET
"""

from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 4):
        print('usage: healthcheck.py <url> [header-name env-var-name]', file=sys.stderr)
        return 2

    url = argv[1]
    headers: dict[str, str] = {}
    if len(argv) == 4:
        header_name, env_var_name = argv[2], argv[3]
        headers[header_name] = os.environ.get(env_var_name, '')

    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return 0 if response.status < 400 else 1
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f'healthcheck failed: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
