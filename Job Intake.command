#!/bin/bash
# Double-click to start Job Intake and open it in your browser. Close this window (or press Ctrl-C) to stop it.
cd "$(dirname "$0")"
PORT=8780
if curl -s -o /dev/null "http://localhost:$PORT/api/meta"; then
  echo "Job Intake is already running — opening it."
  open "http://localhost:$PORT"
  exit 0
fi
exec python3 jobintake.py serve --port "$PORT" --open
