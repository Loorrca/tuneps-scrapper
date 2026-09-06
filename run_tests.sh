#!/usr/bin/env bash
set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$ROOT/.venv/bin/python"; [ -x "$PY" ] || PY=python3
cd "$ROOT"
"$PY" tests/test_matcher.py
"$PY" tests/test_corpus.py
"$PY" tests/test_pipeline.py
"$PY" tests/test_tls.py
"$PY" tests/test_web.py
"$PY" tests/test_results.py
"$PY" tests/test_deadlines.py
