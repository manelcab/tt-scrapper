#!/usr/bin/env bash
# Entorno para ejecutar tt-scraper en el VPS. Lo sourcea el cron / run.
export PATH="/home/manu/tt-scrapper/.node/bin:$PATH"   # node para wrangler
export TTS_WRANGLER_CWD="/home/manu/tt-scrapper"       # wrangler + wrangler.toml aquí
export SEASON="2026-2027"
export NODE_OPTIONS="--dns-result-order=ipv4first"
export CLOUDFLARE_ACCOUNT_ID="5a47f3cd28d3ff2167eb1126cdac6fdc"
# El token (secreto) vive fuera del repo:
[ -f "$HOME/.tt-scrapper-secrets" ] && . "$HOME/.tt-scrapper-secrets"
export CLOUDFLARE_API_TOKEN CLOUDFLARE_ACCOUNT_ID
