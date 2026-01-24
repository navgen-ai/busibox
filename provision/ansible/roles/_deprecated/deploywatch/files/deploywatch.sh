#!/usr/bin/env bash
set -euo pipefail

APPS=("agent")
declare -A REPO DIR SERVICE BUILD
REPO["agent"]="jazzmind/agent-server"
DIR["agent"]="/srv/apps/agent"
SERVICE["agent"]="agent"
BUILD["agent"]="npm ci || true && npm run build || true"

STATE_DIR="/var/lib/deploywatch"
LOCK_FILE="/run/deploywatch/deploywatch.lock"
mkdir -p "$STATE_DIR"

exec 9>"$LOCK_FILE"
flock -n 9 || { echo "Another run active"; exit 0; }

auth_hdr=()
[[ -n "${GITHUB_TOKEN:-}" ]] && auth_hdr=(-H "Authorization: Bearer ${GITHUB_TOKEN}")

for app in "${APPS[@]}"; do
  repo="${REPO[$app]}"; dir="${DIR[$app]}"; svc="${SERVICE[$app]}"; build="${BUILD[$app]}"
  latest_json=$(curl -fsSL -H 'Accept: application/vnd.github+json' -H 'X-GitHub-Api-Version: 2022-11-28' "${auth_hdr[@]}" "https://api.github.com/repos/${repo}/releases/latest" || true)
  tag=$(jq -r .tag_name <<<"$latest_json")
  rel_id=$(jq -r .id <<<"$latest_json")

  [[ -z "$tag" || "$tag" == "null" ]] && { echo "No release for $app"; continue; }

  state_file="${STATE_DIR}/${app}.state"
  last=$(cat "$state_file" 2>/dev/null || true)
  [[ "$rel_id" == "$last" ]] && { echo "$app up-to-date"; continue; }

  if [[ ! -d "$dir/.git" ]]; then
    git clone "git@github.com:${repo}.git" "$dir"
  fi
  pushd "$dir"
  git fetch --tags --force
  git reset --hard
  git clean -fdx
  git checkout -f "refs/tags/${tag}" || git checkout -f "${tag}"
  bash -lc "$build"
  popd

  /usr/bin/systemctl restart "${svc}.service"
  echo "$rel_id" > "$state_file"
  echo "Deployed $app $tag"
done
