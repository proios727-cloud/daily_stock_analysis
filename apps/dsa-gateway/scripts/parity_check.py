#!/usr/bin/env python3
"""Compare the Rust gateway with the Python backend, route by route.

Usage: parity_check.py [BACKEND_URL] [GATEWAY_URL]
Both services must be running and share the same DATABASE_PATH.
Exit code 0 means every compared response matched.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BACKEND = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
GATEWAY = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8080"

# (path, mode): "exact" compares status and JSON body; "health" ignores the
# timestamp value; "validation" compares status, error, and each detail loc and type;
# "as_of" compares status and JSON body after dropping the top-level as_of field.
CASES = [
    ("/health", "health"),
    ("/api/health", "health"),
    ("/api/v1/health", "health"),
    ("/api/v1/usage/summary", "exact"),
    ("/api/v1/usage/summary?period=today", "exact"),
    ("/api/v1/usage/summary?period=all", "exact"),
    ("/api/v1/usage/summary?period=bogus", "exact"),
    ("/api/v1/usage/dashboard", "exact"),
    ("/api/v1/usage/dashboard?period=all&limit=3", "exact"),
    ("/api/v1/usage/dashboard?period=all&limit=200", "exact"),
    ("/api/v1/usage/dashboard?limit=0", "validation"),
    ("/api/v1/usage/dashboard?limit=201", "validation"),
    ("/api/v1/usage/dashboard?limit=abc", "validation"),
    ("/api/v1/usage/dashboard?limit=1.5", "validation"),
    ("/api/v1/usage/dashboard?limit=0.0", "validation"),
    ("/api/v1/usage/dashboard?limit=99999999999999999999", "validation"),
    ("/api/v1/usage/dashboard?limit=-99999999999999999999", "validation"),
    ("/api/v1/usage/dashboard?period=all&limit=5.0", "exact"),
    ("/api/v1/usage/dashboard?period=all&limit=1_0", "exact"),
    ("/api/v1/usage/dashboard?period=all&limit=%2B2", "exact"),
    ("/api/v1/data/overview", "as_of"),
    ("/api/v1/auth/status", "exact"),
    ("/api/v1/no-such-route", "exact"),
]


def fetch(base: str, path: str):
    try:
        with urllib.request.urlopen(base + path, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def as_json(raw: bytes):
    try:
        return json.loads(raw)
    except ValueError:
        return raw.decode("utf-8", "replace")


def main() -> int:
    failures = 0
    for path, mode in CASES:
        b_status, b_raw = fetch(BACKEND, path)
        g_status, g_raw = fetch(GATEWAY, path)
        b_body, g_body = as_json(b_raw), as_json(g_raw)
        if mode == "health":
            ok = (
                b_status == g_status == 200
                and isinstance(g_body, dict)
                and set(b_body) == set(g_body)
                and g_body.get("status") == b_body.get("status")
                and isinstance(g_body.get("timestamp"), str)
            )
        elif mode == "validation":
            ok = (
                b_status == g_status
                and isinstance(g_body, dict)
                and g_body.get("error") == b_body.get("error")
                and g_body.get("message") == b_body.get("message")
                and [(d.get("loc"), d.get("type")) for d in g_body.get("detail", [])]
                == [(d.get("loc"), d.get("type")) for d in b_body.get("detail", [])]
            )
        elif mode == "as_of":
            ok = (
                b_status == g_status
                and isinstance(b_body, dict)
                and isinstance(g_body, dict)
                and "as_of" in g_body
                and {k: v for k, v in b_body.items() if k != "as_of"}
                == {k: v for k, v in g_body.items() if k != "as_of"}
            )
        else:
            ok = b_status == g_status and b_body == g_body
        print(f"{'PASS' if ok else 'FAIL'} [{mode}] {path} (backend {b_status}, gateway {g_status})")
        if not ok:
            failures += 1
            print(f"  backend: {str(b_body)[:400]}")
            print(f"  gateway: {str(g_body)[:400]}")
    print(f"{len(CASES) - failures}/{len(CASES)} matched")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
