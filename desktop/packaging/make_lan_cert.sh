#!/usr/bin/env bash
# OPTIONAL. Makes a self-signed certificate so the phone can talk to ULTRON over your home Wi-Fi
# without the token crossing the network in clear text. You run it; ULTRON never does.
#   usage: make_lan_cert.sh <LAN-IP-or-name-of-this-computer>     e.g. 192.168.1.20
# Writes ~/.config/ultron/tls/{cert,key}.pem (key readable only by you). Valid 2 years.
set -euo pipefail
host="${1:?give the LAN address of this computer, e.g. 192.168.1.20}"
dir="${XDG_CONFIG_HOME:-$HOME/.config}/ultron/tls"
mkdir -p "$dir"; chmod 700 "$dir"
if [[ "$host" =~ ^[0-9.]+$ ]]; then san="IP:$host,IP:127.0.0.1"; else san="DNS:$host,IP:127.0.0.1"; fi
umask 077
openssl req -x509 -newkey rsa:3072 -sha256 -days 730 -nodes \
  -keyout "$dir/key.pem" -out "$dir/cert.pem" -subj "/CN=ULTRON" -addext "subjectAltName=$san"
chmod 600 "$dir/key.pem"; chmod 644 "$dir/cert.pem"
echo "Certificate: $dir/cert.pem"
echo "SHA-256 fingerprint (the phone will show the same; compare them when pairing):"
openssl x509 -in "$dir/cert.pem" -noout -fingerprint -sha256
echo
echo "Start the backend with:"
echo "  ULTRON_TLS_CERT_FILE=$dir/cert.pem uvicorn app.main:app_factory --factory \\"
echo "    --host $host --port 8443 --ssl-certfile $dir/cert.pem --ssl-keyfile $dir/key.pem"
