#!/usr/bin/env bash
set -euo pipefail

BASE="${API_BASE_URL:-http://localhost:8000}"
KEY="${DEMO_API_KEY:-sk-alice-secret-key-001}"
AUTH="Authorization: Bearer $KEY"

echo "══════════════════════════════════════════════════"
echo " Solution 4 — TigerBeetle + Restate Demo"
echo "══════════════════════════════════════════════════"
echo

# Health check
echo "1. Health check"
curl -sf "$BASE/health" | python3 -m json.tool
echo

# Ready check
echo "2. Readiness check"
curl -sf "$BASE/ready" | python3 -m json.tool
echo

# Admin topup
echo "3. Admin credit topup (+500 credits)"
curl -sf -X POST "$BASE/v1/admin/credits" \
  -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"user_id":"a0000000-0000-0000-0000-000000000001","amount":500}' \
  | python3 -m json.tool
echo

# Submit task
echo "4. Submit task (x=42, y=58)"
SUBMIT=$(curl -sf -X POST "$BASE/v1/task" \
  -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"x":42,"y":58}')
echo "$SUBMIT" | python3 -m json.tool
TASK_ID=$(echo "$SUBMIT" | python3 -c "import sys,json; print(json.load(sys.stdin)['task_id'])")
echo

# Poll until complete
echo "5. Polling task $TASK_ID..."
for i in $(seq 1 20); do
  POLL=$(curl -sf "$BASE/v1/poll?task_id=$TASK_ID" -H "$AUTH")
  STATUS=$(echo "$POLL" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status','unknown'))" 2>/dev/null || echo "unknown")
  echo "   [$i] status=$STATUS"
  if [ "$STATUS" = "COMPLETED" ]; then
    echo "$POLL" | python3 -m json.tool
    break
  fi
  sleep 1
done
echo

# Submit and cancel
echo "6. Submit + immediate cancel"
SUBMIT2=$(curl -sf -X POST "$BASE/v1/task" \
  -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"x":1,"y":1}')
TASK_ID2=$(echo "$SUBMIT2" | python3 -c "import sys,json; print(json.load(sys.stdin)['task_id'])")
echo "   Submitted: $TASK_ID2"
CANCEL_RESP=$(curl -s -w "\n%{http_code}" -X POST "$BASE/v1/task/$TASK_ID2/cancel" -H "$AUTH")
CANCEL_BODY=$(echo "$CANCEL_RESP" | sed '$d')
CANCEL_CODE=$(echo "$CANCEL_RESP" | tail -1)
if [ "$CANCEL_CODE" = "200" ]; then
  echo "$CANCEL_BODY" | python3 -m json.tool
else
  echo "   Cancel returned HTTP $CANCEL_CODE (task may have completed already)"
  echo "   $CANCEL_BODY"
fi
echo

echo "══════════════════════════════════════════════════"
echo " Demo complete"
echo "══════════════════════════════════════════════════"
