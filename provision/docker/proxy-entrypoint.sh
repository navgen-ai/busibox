#!/bin/sh
# =============================================================================
# Busibox Proxy Entrypoint
# =============================================================================
#
# Execution Context:
# - Runs inside Docker proxy container
# - Ensures local TLS certificates exist for nginx
# - Starts nginx in foreground
#
# =============================================================================

set -eu

# Ensure openssl is available (not included in nginx:alpine by default)
if ! command -v openssl >/dev/null 2>&1; then
  apk add --no-cache openssl >/dev/null 2>&1
fi

SSL_DIR="/etc/nginx/ssl"
CERT_FILE="${SSL_DIR}/localhost.crt"
KEY_FILE="${SSL_DIR}/localhost.key"

mkdir -p "${SSL_DIR}"

if [ ! -f "${CERT_FILE}" ] || [ ! -f "${KEY_FILE}" ]; then
  # Build SAN list — always include localhost, add BASE_DOMAIN if it's not localhost
  SAN="DNS:localhost,DNS:*.localhost,IP:127.0.0.1"
  CN="localhost"
  if [ -n "${BASE_DOMAIN:-}" ] && [ "${BASE_DOMAIN}" != "localhost" ]; then
    SAN="${SAN},DNS:${BASE_DOMAIN}"
    CN="${BASE_DOMAIN}"
  fi
  echo "[proxy-entrypoint] Generating self-signed certificate (CN=${CN}, SANs: ${SAN})"
  openssl req -x509 -nodes -days 365 \
    -newkey rsa:2048 \
    -keyout "${KEY_FILE}" \
    -out "${CERT_FILE}" \
    -subj "/C=US/ST=CA/L=San Francisco/O=Busibox/CN=${CN}" \
    -addext "subjectAltName=${SAN}"
fi

echo "[proxy-entrypoint] Validating nginx configuration"
nginx -t

echo "[proxy-entrypoint] Starting nginx"
exec nginx -g 'daemon off;'
