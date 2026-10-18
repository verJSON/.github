#!/usr/bin/env bash
# Present the private package token to npm for exactly the locked GitHub
# Packages downloads, and to nothing else (ADR 0220).
#
# `npm ci --ignore-scripts` is not a credential boundary: npm 10 still runs
# `prepare` for linked and directory dependencies, and npm 10 and 11 prepare a
# git dependency through a child `npm install` that inherits the token and
# reads the dependency's own .npmrc. So the credentialed step builds no
# dependency tree. It `npm cache add`s each exact locked private download URL
# into an explicit npm_config_cache and checks the cache holds the lockfile's
# sha512 content; a later step installs from that cache with no token.
#
# One implementation for every credentialed install: node-release.yml and the
# release callers scripts/gen-changelog-caller.sh generates run this file from
# a checkout of the contract at an immutable commit.
#
# Environment: NODE_AUTH_TOKEN (read by npm through its user config),
# PRIVATE_PACKAGE_REGISTRY, npm_config_cache (absolute, shared with the
# tokenless install), GITHUB_WORKSPACE, RUNNER_TEMP. Run from the project root.
set -euo pipefail
acquisition_dir="$(mktemp -d "$RUNNER_TEMP/verjson-private-acquisition.XXXXXX")"
project_npmrc="$GITHUB_WORKSPACE/.npmrc"
quarantined=false
finish_acquisition() {
  local status=$?
  trap - EXIT
  if [[ "$quarantined" == true ]]; then
    if [[ -e "$project_npmrc" || -L "$project_npmrc" ]]; then
      echo "::error::the project .npmrc was recreated during credentialed dependency acquisition."
      status=1
    elif ! mv -- "$acquisition_dir/project.npmrc" "$project_npmrc"; then
      echo "::error::could not restore the project .npmrc after credentialed dependency acquisition."
      status=1
    fi
  fi
  rm -rf -- "$acquisition_dir" || status=1
  exit "$status"
}
trap finish_acquisition EXIT
if [[ -e "$project_npmrc" || -L "$project_npmrc" ]]; then
  mv -- "$project_npmrc" "$acquisition_dir/project.npmrc"
  quarantined=true
fi
lockfile=package-lock.json
if [[ -e npm-shrinkwrap.json ]]; then
  lockfile=npm-shrinkwrap.json
fi
node - "$lockfile" "$PRIVATE_PACKAGE_REGISTRY" >"$acquisition_dir/downloads.tsv" <<'NODE'
const fs = require("node:fs");
const [lockfilePath, registry] = process.argv.slice(2);
const fail = (message) => {
  console.error(`::error::${message}`);
  process.exit(1);
};
const registryUrl = new URL(registry);
let lock;
try {
  lock = JSON.parse(fs.readFileSync(lockfilePath, "utf8"));
} catch {
  fail(`dependency installation requires a readable JSON ${lockfilePath}.`);
}
const lockfileStrings = function* (value) {
  if (typeof value === "string") {
    yield value;
  } else if (typeof value === "object" && value !== null) {
    for (const [key, member] of Object.entries(value)) {
      yield key;
      yield* lockfileStrings(member);
    }
  }
};
if (![2, 3].includes(lock?.lockfileVersion)) {
  // An older lockfile has no flat packages map to select downloads
  // from. It installs without credentials unless it names the
  // private registry anywhere.
  for (const text of lockfileStrings(lock)) {
    if (text.toLowerCase().includes(registryUrl.hostname)) {
      fail(`${lockfilePath} names ${registryUrl.hostname}; private packages require lockfileVersion 2 or 3.`);
    }
  }
  process.exit(0);
}
if (typeof lock.packages !== "object" || lock.packages === null) {
  fail(`${lockfilePath} lockfileVersion ${lock.lockfileVersion} has no packages map.`);
}
// GitHub Packages may record the owner's own spelling of the scope.
const downloadPath =
  /^\/download\/@[A-Za-z0-9][A-Za-z0-9._~-]*\/[A-Za-z0-9._~-]+\/[0-9A-Za-z.+-]+\/[0-9A-Za-z._-]+$/;
const downloads = new Map();
for (const [location, entry] of Object.entries(lock.packages)) {
  const resolved = entry?.resolved;
  if (typeof resolved !== "string") continue;
  let url;
  try {
    url = new URL(resolved);
  } catch {
    continue;
  }
  if (url.hostname !== registryUrl.hostname) continue;
  if (url.origin !== registryUrl.origin
    || resolved !== `${url.origin}${url.pathname}`
    || !downloadPath.test(url.pathname)) {
    fail(`${JSON.stringify(location)} is not an exact private registry download URL.`);
  }
  const integrity = /^sha512-([A-Za-z0-9+/]{86}==)$/.exec(entry.integrity ?? "");
  if (!integrity) {
    fail(`${JSON.stringify(location)} needs exactly one sha512 lockfile integrity.`);
  }
  const digest = Buffer.from(integrity[1], "base64").toString("hex");
  if ((downloads.get(resolved) ?? digest) !== digest) {
    fail(`${JSON.stringify(location)} repeats a download URL with different integrity.`);
  }
  downloads.set(resolved, digest);
}
for (const [resolved, digest] of downloads) {
  process.stdout.write(`${resolved}\t${digest}\n`);
}
NODE
if [[ -s "$acquisition_dir/downloads.tsv" ]]; then
  npm_version="$(npm --version)"
  if [[ ! "$npm_version" =~ ^(10|11)\.[0123456789]+\.[0123456789]+$ ]]; then
    echo "::error::npm $npm_version has no verified credential-isolation contract; use a node-version that bundles npm 10 or 11."
    exit 1
  fi
  # The cache path comes from the step environment, which the
  # tokenless install shares, rather than `npm config get cache`,
  # whose output npm redacts when a path segment looks secret.
  cache_dir="${npm_config_cache-}"
  if [[ "$cache_dir" != /* ]]; then
    echo "::error::npm_config_cache must be the absolute cache path shared with the tokenless install."
    exit 1
  fi
  while IFS=$'\t' read -r resolved digest; do
    npm cache add "$resolved"
    content="$cache_dir/_cacache/content-v2/sha512/${digest:0:2}/${digest:2:2}/${digest:4}"
    if [[ ! -f "$content" || -L "$content" ]] ||
      [[ "$(sha512sum -- "$content" | cut -d' ' -f1)" != "$digest" ]]; then
      echo "::error::npm did not cache the exact locked content of $resolved."
      exit 1
    fi
  done <"$acquisition_dir/downloads.tsv"
fi
