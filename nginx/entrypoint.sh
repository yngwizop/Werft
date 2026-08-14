#!/bin/sh
set -eu

TLS_DIR=/etc/nginx/tls
mkdir -p "$TLS_DIR"

if [ ! -f "$TLS_DIR/cert.pem" ] || [ ! -f "$TLS_DIR/key.pem" ]; then
  CN="${TLS_CN:-werft}"
  echo "Generating self-signed TLS certificate for CN=$CN"
  openssl req -x509 -newkey rsa:2048 -sha256 -days 3650 -nodes \
    -keyout "$TLS_DIR/key.pem" \
    -out "$TLS_DIR/cert.pem" \
    -subj "/CN=$CN"
  chmod 600 "$TLS_DIR/key.pem"
fi

exec nginx -g "daemon off;"
