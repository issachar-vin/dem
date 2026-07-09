#!/usr/bin/env bash
# Agent container entrypoint. Validates auth, seeds Claude Code's config on a fresh
# state volume, then execs the passed `claude -p …` command from the dispatcher.
set -euo pipefail

# Exactly one Claude credential. Both set is an ambiguous config error, not something to
# guess at — fail loudly so the dispatcher/operator fixes it.
if [[ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" && -n "${ANTHROPIC_API_KEY:-}" ]]; then
    echo "FATAL: both CLAUDE_CODE_OAUTH_TOKEN and ANTHROPIC_API_KEY are set; provide exactly one." >&2
    exit 78  # EX_CONFIG
fi
if [[ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" && -z "${ANTHROPIC_API_KEY:-}" ]]; then
    echo "FATAL: no Claude credential set; provide CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY." >&2
    exit 78  # EX_CONFIG
fi

# Seed ~/.claude.json (onboarding-complete + /work trusted) so `claude -p` never blocks
# on an interactive prompt. Only when absent: on a resumed state volume the config Claude
# Code has already written wins.
CLAUDE_CONFIG="${HOME}/.claude.json"
if [[ ! -f "${CLAUDE_CONFIG}" ]]; then
    cp /opt/agent/seed-claude.json "${CLAUDE_CONFIG}"
fi

exec "$@"
