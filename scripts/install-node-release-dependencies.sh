#!/usr/bin/env bash
set +x
set -euo pipefail

registry_token="${NODE_AUTH_TOKEN-}"
export -n registry_token
unset NODE_AUTH_TOKEN
NODE_AUTH_TOKEN="$registry_token" npm ci --ignore-scripts
unset registry_token

npm ci --prefer-offline
