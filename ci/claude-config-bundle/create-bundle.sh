#!/usr/bin/env bash
set -euo pipefail

source_home="${1:-/home/boxd}"
output="${2:-claude-config-bundle.tar.gz}"
stage_dir="$(mktemp -d)"
scan_file="$(mktemp)"
cleanup() {
  rm -rf "$stage_dir"
  rm -f "$scan_file"
}
trap cleanup EXIT

copy_optional_path() {
  local src="$1"
  local dest="$2"
  if [[ -e "$src" ]]; then
    mkdir -p "$(dirname "$dest")"
    cp -a "$src" "$dest"
  fi
}

mkdir -p "$stage_dir/.claude"

copy_optional_path "$source_home/.claude/settings.json" "$stage_dir/.claude/settings.json"
copy_optional_path "$source_home/.claude/9router-env.sh" "$stage_dir/.claude/9router-env.sh"
copy_optional_path "$source_home/.claude/plugins" "$stage_dir/.claude/plugins"
copy_optional_path "$source_home/.claude/commands" "$stage_dir/.claude/commands"
copy_optional_path "$source_home/.claude/agents" "$stage_dir/.claude/agents"
copy_optional_path "$source_home/.claude/skills" "$stage_dir/.claude/skills"
copy_optional_path "$source_home/.claude/cache/ecc" "$stage_dir/.claude/cache/ecc"
copy_optional_path "$source_home/.claude/cache/caveman" "$stage_dir/.claude/cache/caveman"
copy_optional_path "$source_home/.local/bin/claude-review" "$stage_dir/.local/bin/claude-review"

for denied in \
  "$stage_dir/.claude/projects" \
  "$stage_dir/.claude/sessions" \
  "$stage_dir/.claude/statsig" \
  "$stage_dir/.claude/todos" \
  "$stage_dir/.bash_history" \
  "$stage_dir/.zsh_history" \
  "$stage_dir/.git-credentials"; do
  if [[ -e "$denied" ]]; then
    echo "Denied path copied into bundle: $denied" >&2
    exit 1
  fi
done

if grep -RIlE --binary-files=without-match \
  '((anthropic|openai|gemini|google|azure|openrouter|deepseek|groq|xai|cohere)?[_-]?(api[_-]?key|access[_-]?token|secret[_-]?key|bearer[_-]?token|auth[_-]?token)[[:space:]]*[:=][[:space:]]*["'"'"']?[A-Za-z0-9_./+=-]{16,}|ghp_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]+|sk-ant-[A-Za-z0-9_-]+|BEGIN OPENSSH PRIVATE KEY|BEGIN RSA PRIVATE KEY)' \
  "$stage_dir" >"$scan_file"; then
  echo "Token-like content found in bundle files:" >&2
  cat "$scan_file" >&2
  exit 1
fi

chmod -R a+rX "$stage_dir"
if [[ -e "$stage_dir/.local/bin/claude-review" ]]; then
  chmod +x "$stage_dir/.local/bin/claude-review"
fi

tar -C "$stage_dir" -czf "$output" .
echo "Created $output"