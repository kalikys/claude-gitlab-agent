#!/bin/sh
# Stand-in for the claude CLI in tests. FAKE_MODE: sleep | error | ok (default).
pwd > "${FAKE_CWD_FILE:-/dev/null}"
case "$FAKE_MODE" in
  sleep) sleep 5 ;;
  error) echo '{"is_error":true,"result":"boom"}'; exit 1 ;;
  *) echo '{"is_error":false,"total_cost_usd":0.12,"session_id":"s1","result":"ok"}' ;;
esac
