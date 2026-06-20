#!/usr/bin/env bash
# Co-start: visualRAG KB service (host GPU) + galaxy-voting labeling container.
#
#  1. launch the visualRAG FastAPI server on the host (GPU-resident DINOv2 + FAISS)
#  2. poll its /health until green
#  3. launch the galaxy-voting container via start.sh, pointing it at the KB
#     service via host.docker.internal (Linux container -> host service)
#
# The KB server stays in the foreground-equivalent (nohup background process);
# for production durability, run both as systemd units (follow-up).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- knobs (override via env) ---
VISUALRAG_REPO="${VISUALRAG_REPO:-${SCRIPT_DIR}/../visiualRAG}"
KB_PREFIX="${VISUALRAG_KB_PREFIX:-.kb_build_0601/index/jwst_0601}"
KB_PORT="${VISUALRAG_PORT:-8765}"
KB_DEVICE="${VISUALRAG_DEVICE:-cuda}"
KB_LOG="${SCRIPT_DIR}/visualrag_server.log"

# Bind 0.0.0.0 so BOTH host consumers (galaxy_morphology_mcp Few-shot at
# 127.0.0.1:${KB_PORT}) AND the bridge-mode galaxy-voting container
# (host.docker.internal) can reach it. On a LAN-exposed/shared host, override
# VISUALRAG_BIND=172.17.0.1 (docker bridge gateway) to avoid exposing the
# (unauthenticated) service network-wide.
KB_BIND="${VISUALRAG_BIND:-0.0.0.0}"
POLL_HOST="$([ "$KB_BIND" = "0.0.0.0" ] && echo 127.0.0.1 || echo "$KB_BIND")"
KB_URL_HOST="http://${POLL_HOST}:${KB_PORT}"               # what THIS script polls
KB_URL_CONTAINER="http://host.docker.internal:${KB_PORT}"  # what the container uses

echo "== visualRAG KB service =="
if [ ! -d "${VISUALRAG_REPO}/src/visualRAG" ]; then
    echo "ERROR: visualRAG repo not found at ${VISUALRAG_REPO} (set VISUALRAG_REPO)"
    exit 1
fi

# Already up? skip launching.
if curl -sf "${KB_URL_HOST}/health" >/dev/null 2>&1; then
    echo "  KB server already running at ${KB_URL_HOST}, reusing it."
else
    echo "  starting KB server (repo=${VISUALRAG_REPO}, prefix=${KB_PREFIX}, device=${KB_DEVICE})..."
    pushd "${VISUALRAG_REPO}" >/dev/null
    # Load VLM creds for /distill (VISUALRAG_VLM_* / OPENAI_*) if a .env is present.
    if [ -f .env ]; then set -a; . ./.env; set +a; fi
    # VISUALRAG_VLM_* (distillation) are forwarded if set in the caller's env.
    PYTHONPATH=src VISUALRAG_KB_PREFIX="${KB_PREFIX}" VISUALRAG_DEVICE="${KB_DEVICE}" \
        VISUALRAG_HOST="${KB_BIND}" VISUALRAG_PORT="${KB_PORT}" \
        nohup python -m visualRAG.server --host "${KB_BIND}" --port "${KB_PORT}" \
        > "${KB_LOG}" 2>&1 &
    KB_PID=$!
    popd >/dev/null
    echo "  KB server PID ${KB_PID}; logs: ${KB_LOG}"

    echo -n "  waiting for /health"
    ok=""
    for _ in $(seq 1 60); do   # up to ~2 min (model load ~5-30s)
        if curl -sf "${KB_URL_HOST}/health" >/dev/null 2>&1; then ok=1; break; fi
        echo -n "."; sleep 2
    done
    echo
    if [ -z "${ok}" ]; then
        echo "ERROR: KB server did not become healthy; tail of log:"
        tail -20 "${KB_LOG}" || true
        exit 1
    fi
fi
echo "  KB healthy: $(curl -sf "${KB_URL_HOST}/health")"

echo
echo "== galaxy-voting labeling container =="
# Hand the container the KB URL via host.docker.internal; start.sh forwards it.
export VISUALRAG_SERVICE_URL="${KB_URL_CONTAINER}"
export VISUALRAG_ENABLED="${VISUALRAG_ENABLED:-1}"
exec bash "${SCRIPT_DIR}/start.sh"
