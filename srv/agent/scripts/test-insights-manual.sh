#!/usr/bin/env bash
# Manual test script for insights API endpoints
# Tests the insights functionality without requiring full pytest infrastructure

set -euo pipefail

# Configuration
AGENT_API_URL="${AGENT_API_URL:-http://localhost:8000}"
USER_ID="${USER_ID:-test-user-$(date +%s)}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}=== Insights API Manual Tests ===${NC}"
echo "Agent API: $AGENT_API_URL"
echo "User ID: $USER_ID"
echo ""

# Test counter
TESTS_PASSED=0
TESTS_FAILED=0

# Helper function to run a test
run_test() {
    local test_name="$1"
    local method="$2"
    local endpoint="$3"
    local data="$4"
    
    echo -e "${YELLOW}Test: ${test_name}${NC}"
    
    if [ -z "$data" ]; then
        response=$(curl -s -w "\n%{http_code}" -X "$method" \
            -H "X-User-Id: $USER_ID" \
            "$AGENT_API_URL$endpoint")
    else
        response=$(curl -s -w "\n%{http_code}" -X "$method" \
            -H "X-User-Id: $USER_ID" \
            -H "Content-Type: application/json" \
            -d "$data" \
            "$AGENT_API_URL$endpoint")
    fi
    
    http_code=$(echo "$response" | tail -n 1)
    body=$(echo "$response" | sed '$d')
    
    if [ "$http_code" -ge 200 ] && [ "$http_code" -lt 300 ]; then
        echo -e "${GREEN}✓ PASS${NC} (HTTP $http_code)"
        echo "Response: ${body:0:200}"
        echo ""
        echo ""
        TESTS_PASSED=$((TESTS_PASSED + 1))
        return 0
    else
        echo -e "${RED}✗ FAIL${NC} (HTTP $http_code)"
        echo "Response: $body"
        echo ""
        TESTS_FAILED=$((TESTS_FAILED + 1))
        return 1
    fi
}

# Test 1: Initialize collection
run_test "Initialize insights collection" \
    "POST" \
    "/insights/init" \
    ""

# Test 2: Insert insights
TIMESTAMP=$(date +%s)

# Generate proper 1024-dimension embedding arrays
EMBEDDING_1=$(python3 -c "import json; print(json.dumps([0.1] * 1024))")
EMBEDDING_2=$(python3 -c "import json; print(json.dumps([0.2] * 1024))")

INSIGHT_DATA=$(cat <<EOF
{
  "insights": [
    {
      "id": "test-insight-${TIMESTAMP}-1",
      "userId": "${USER_ID}",
      "content": "User prefers Python for backend development",
      "embedding": ${EMBEDDING_1},
      "conversationId": "test-conv-1",
      "analyzedAt": ${TIMESTAMP}
    },
    {
      "id": "test-insight-${TIMESTAMP}-2",
      "userId": "${USER_ID}",
      "content": "User is interested in machine learning and AI",
      "embedding": ${EMBEDDING_2},
      "conversationId": "test-conv-1",
      "analyzedAt": ${TIMESTAMP}
    }
  ]
}
EOF
)

run_test "Insert insights" \
    "POST" \
    "/insights" \
    "$INSIGHT_DATA"

# Test 3: Flush collection
run_test "Flush collection" \
    "POST" \
    "/insights/flush" \
    ""

# Test 4: Get user stats
run_test "Get user statistics" \
    "GET" \
    "/insights/stats/$USER_ID" \
    ""

# Test 5: Search insights (note: this requires embedding service to be running)
SEARCH_DATA='{
  "query": "What programming languages does the user like?",
  "userId": "'$USER_ID'",
  "limit": 5,
  "scoreThreshold": 2.0
}'

echo -e "${YELLOW}Test: Search insights (may fail if embedding service unavailable)${NC}"
response=$(curl -s -w "\n%{http_code}" -X "POST" \
    -H "X-User-Id: $USER_ID" \
    -H "Content-Type: application/json" \
    -d "$SEARCH_DATA" \
    "$AGENT_API_URL/insights/search")

http_code=$(echo "$response" | tail -n 1)
body=$(echo "$response" | sed '$d')

if [ "$http_code" -ge 200 ] && [ "$http_code" -lt 300 ]; then
    echo -e "${GREEN}✓ PASS${NC} (HTTP $http_code)"
    echo "Response: ${body:0:200}"
    echo ""
    TESTS_PASSED=$((TESTS_PASSED + 1))
elif [ "$http_code" -eq 500 ]; then
    echo -e "${YELLOW}⚠ SKIP${NC} (HTTP $http_code - embedding service may be unavailable)"
    echo "Response: ${body:0:200}"
    echo ""
else
    echo -e "${RED}✗ FAIL${NC} (HTTP $http_code)"
    echo "Response: $body"
    echo ""
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi
echo ""

# Test 6: Delete conversation insights
run_test "Delete conversation insights" \
    "DELETE" \
    "/insights/conversation/test-conv-1" \
    ""

# Test 7: Delete user insights
run_test "Delete user insights" \
    "DELETE" \
    "/insights/user/$USER_ID" \
    ""

# Summary
echo -e "${BLUE}=== Test Summary ===${NC}"
echo -e "Passed: ${GREEN}$TESTS_PASSED${NC}"
echo -e "Failed: ${RED}$TESTS_FAILED${NC}"
echo ""

if [ $TESTS_FAILED -eq 0 ]; then
    echo -e "${GREEN}✓ All tests passed!${NC}"
    exit 0
else
    echo -e "${RED}✗ Some tests failed${NC}"
    exit 1
fi
