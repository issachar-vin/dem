#!/usr/bin/env bash
# Phase 4 acceptance for the agent image: prove a containerized `claude -p` works in the
# configured auth mode, that a second run resumes the first run's session off the state
# volume, and that a hung container can be force-killed (the dispatcher's hard timeout).
set -euo pipefail

IMAGE="dem-agent:smoke"
CLAUDE_VOL="psa-claude-smoke"
CONTAINER_TIMEOUT="psa-agent-smoke-timeout"
MODEL="${CLAUDE_MODEL_SMOKE:-claude-haiku-4-5}"

cd "$(dirname "$0")/.."

# Pull the auth var from the environment, falling back to .env, so the smoke matches
# whatever single credential the operator has configured.
if [[ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" && -z "${ANTHROPIC_API_KEY:-}" && -f .env ]]; then
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
fi
if [[ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" && -z "${ANTHROPIC_API_KEY:-}" ]]; then
    echo "FATAL: set CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY (env or .env) before running the smoke." >&2
    exit 1
fi

auth_args=()
if [[ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]]; then
    auth_args=(-e "CLAUDE_CODE_OAUTH_TOKEN=${CLAUDE_CODE_OAUTH_TOKEN}")
    echo "▶ auth mode: subscription (CLAUDE_CODE_OAUTH_TOKEN)"
else
    auth_args=(-e "ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}")
    echo "▶ auth mode: api key (ANTHROPIC_API_KEY)"
fi

cleanup() {
    docker rm -f "${CONTAINER_TIMEOUT}" >/dev/null 2>&1 || true
    docker volume rm -f "${CLAUDE_VOL}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "▶ building ${IMAGE}"
docker build -t "${IMAGE}" agent

docker volume create "${CLAUDE_VOL}" >/dev/null

run_agent() {
    docker run --rm \
        "${auth_args[@]}" \
        -v "${CLAUDE_VOL}:/home/agent/.claude" \
        "${IMAGE}" "$@"
}

echo "▶ first run: claude -p 'say hi' --output-format json"
first_out="$(run_agent claude -p "say hi" --output-format json --model "${MODEL}")"
echo "${first_out}"
session_id="$(printf '%s' "${first_out}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["session_id"])')"
if [[ -z "${session_id}" ]]; then
    echo "FATAL: no session_id in first run output." >&2
    exit 1
fi
echo "▶ session_id=${session_id}"

echo "▶ second run: resume the prior session off the state volume"
resume_out="$(run_agent claude -p --resume "${session_id}" "say hi again" --output-format json --model "${MODEL}")"
echo "${resume_out}"
resumed_id="$(printf '%s' "${resume_out}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["session_id"])')"
if [[ "${resumed_id}" != "${session_id}" ]]; then
    echo "FATAL: resume did not continue the prior session (${resumed_id} != ${session_id})." >&2
    exit 1
fi
echo "▶ resume confirmed: same session_id"

echo "▶ kill-on-timeout: start a hung container and force-kill it"
docker run -d --name "${CONTAINER_TIMEOUT}" "${auth_args[@]}" "${IMAGE}" sleep 600 >/dev/null
docker kill "${CONTAINER_TIMEOUT}" >/dev/null
if docker ps --filter "name=${CONTAINER_TIMEOUT}" --filter "status=running" --format '{{.Names}}' | grep -q "${CONTAINER_TIMEOUT}"; then
    echo "FATAL: container still running after docker kill." >&2
    exit 1
fi
echo "▶ kill-on-timeout confirmed"

echo "✅ agent-smoke passed"
