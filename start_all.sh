#!/usr/bin/env bash
# Co-start / manage: visualRAG KB service (host GPU) + galaxy-voting container.
#
#  Subcommands:
#    start_all.sh [start]   Launch the KB service if down (reuse if healthy),
#                           then start the galaxy-voting container via start.sh.
#                           (Default; original behavior.)
#    start_all.sh restart   Restart ONLY the KB service (stop + relaunch + wait
#                           for health). The container is untouched — it reaches
#                           the KB via host.docker.internal:${KB_PORT} and
#                           reconnects on its own. Use this to reload the KB
#                           (e.g. after clearing it) or pick up new server code.
#    start_all.sh stop      Stop the KB service.
#    start_all.sh status    Show KB health + the PID we launched it as.
#
#  The KB server runs as a fully-detached background daemon (setsid + nohup,
#  survives this shell) and its PID is recorded in ${SCRIPT_DIR}/visualrag_server.pid.
#  For production durability, run both as systemd units (follow-up).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- knobs (override via env) ---
VISUALRAG_REPO="${VISUALRAG_REPO:-${SCRIPT_DIR}/../visiualRAG}"
KB_PREFIX="${VISUALRAG_KB_PREFIX:-.kb_build_0601/index/jwst_0601}"
KB_PORT="${VISUALRAG_PORT:-8765}"
KB_DEVICE="${VISUALRAG_DEVICE:-cuda}"
KB_LOG="${SCRIPT_DIR}/visualrag_server.log"
KB_PIDFILE="${SCRIPT_DIR}/visualrag_server.pid"

# Bind 0.0.0.0 so BOTH host consumers (galaxy_morphology_mcp Few-shot at
# 127.0.0.1:${KB_PORT}) AND the bridge-mode galaxy-voting container
# (host.docker.internal) can reach it. On a LAN-exposed/shared host, override
# VISUALRAG_BIND=172.17.0.1 (docker bridge gateway) to avoid exposing the
# (unauthenticated) service network-wide.
KB_BIND="${VISUALRAG_BIND:-0.0.0.0}"
POLL_HOST="$([ "$KB_BIND" = "0.0.0.0" ] && echo 127.0.0.1 || echo "$KB_BIND")"
KB_URL_HOST="http://${POLL_HOST}:${KB_PORT}"               # what THIS script polls
KB_URL_CONTAINER="http://host.docker.internal:${KB_PORT}"  # what the container uses

# ── shared helpers ──────────────────────────────────────────────────────────

_kb_health() { curl -sf "${KB_URL_HOST}/health" >/dev/null 2>&1; }

_kb_health_print() {
    if _kb_health; then echo "  KB healthy: $(curl -sf "${KB_URL_HOST}/health")"
    else echo "  KB NOT healthy at ${KB_URL_HOST}"; fi
}

# PID of the server WE launched, if its pidfile exists and that process is alive.
_kb_pid() {
    [ -f "${KB_PIDFILE}" ] || return 1
    local p; p="$(cat "${KB_PIDFILE}" 2>/dev/null || true)"
    [ -n "${p}" ] && kill -0 "${p}" 2>/dev/null || return 1
    echo "${p}"
}

_kb_launch() {
    echo "  starting KB server (repo=${VISUALRAG_REPO}, prefix=${KB_PREFIX}, device=${KB_DEVICE})..."
    pushd "${VISUALRAG_REPO}" >/dev/null
    # Load VLM creds for /distill (VISUALRAG_VLM_* / OPENAI_*) if a .env is present.
    if [ -f .env ]; then set -a; . ./.env; set +a; fi
    # Fully detached: setsid -> own session (survives this shell), nohup -> ignore SIGHUP.
    # VISUALRAG_VLM_* (distillation) are forwarded if set in the caller's env.
    PYTHONPATH=src VISUALRAG_KB_PREFIX="${KB_PREFIX}" VISUALRAG_DEVICE="${KB_DEVICE}" \
        VISUALRAG_HOST="${KB_BIND}" VISUALRAG_PORT="${KB_PORT}" \
        setsid nohup python -m visualRAG.server --host "${KB_BIND}" --port "${KB_PORT}" \
        > "${KB_LOG}" 2>&1 &
    echo "$!" > "${KB_PIDFILE}"
    popd >/dev/null
    echo "  KB server PID $(cat "${KB_PIDFILE}"); logs: ${KB_LOG}"
}

_kb_wait_health() {
    echo -n "  waiting for /health"
    local ok=""
    for _ in $(seq 1 60); do   # up to ~2 min (model load ~5-30s)
        if _kb_health; then ok=1; break; fi
        echo -n "."; sleep 2
    done
    echo
    if [ -z "${ok}" ]; then
        echo "ERROR: KB server did not become healthy; tail of log:"
        tail -20 "${KB_LOG}" || true
        return 1
    fi
}

# Launch only if not already healthy (reuse). Idempotent for `start`.
_kb_launch_if_needed() {
    if _kb_health; then
        echo "  KB server already running at ${KB_URL_HOST}, reusing it."
        return 0
    fi
    _kb_launch
    _kb_wait_health
}

_kb_stop() {
    echo "== stopping KB service =="
    local p
    if p="$(_kb_pid 2>/dev/null || true)" && [ -n "${p}" ]; then
        echo "  sending SIGTERM to PID ${p} (uvicorn drains then exits)..."
        kill -TERM "${p}" 2>/dev/null || true
        for _ in $(seq 1 30); do          # up to ~60s for model unload + drain
            kill -0 "${p}" 2>/dev/null || break
            sleep 2
        done
        if kill -0 "${p}" 2>/dev/null; then
            echo "  still alive after 60s, sending SIGKILL..."
            kill -KILL "${p}" 2>/dev/null || true
            sleep 1
        fi
    fi
    # Fallback: clear any orphan / hand-launched server still holding the port
    # (e.g. a server started by an older start_all.sh that wrote no pidfile).
    if command -v fuser >/dev/null 2>&1 && fuser "${KB_PORT}/tcp" >/dev/null 2>&1; then
        echo "  port ${KB_PORT} still bound; killing holder via fuser..."
        fuser -k "${KB_PORT}/tcp" >/dev/null 2>&1 || true
        sleep 1
    fi
    rm -f "${KB_PIDFILE}"
    if _kb_health; then echo "  WARNING: KB still responds at ${KB_URL_HOST} (stop may be incomplete)"
    else echo "  KB stopped."; fi
}

_kb_status() {
    local p
    if p="$(_kb_pid 2>/dev/null || true)" && [ -n "${p}" ]; then
        echo "  pidfile ${KB_PIDFILE} -> PID ${p} (alive)"
    else
        echo "  pidfile ${KB_PIDFILE} (absent or stale)"
    fi
    _kb_health_print
}

_require_repo() {
    if [ ! -d "${VISUALRAG_REPO}/src/visualRAG" ]; then
        echo "ERROR: visualRAG repo not found at ${VISUALRAG_REPO} (set VISUALRAG_REPO)"
        exit 1
    fi
}

# ── dispatch ────────────────────────────────────────────────────────────────

CMD="${1:-start}"
case "${CMD}" in
    start)
        echo "== visualRAG KB service =="
        _require_repo
        _kb_launch_if_needed
        echo
        echo "== galaxy-voting labeling container =="
        # Hand the container the KB URL via host.docker.internal; start.sh forwards it.
        export VISUALRAG_SERVICE_URL="${KB_URL_CONTAINER}"
        export VISUALRAG_ENABLED="${VISUALRAG_ENABLED:-1}"
        exec bash "${SCRIPT_DIR}/start.sh"
        ;;
    restart)
        echo "== visualRAG KB service: RESTART =="
        _require_repo
        _kb_stop
        echo
        _kb_launch
        _kb_wait_health
        _kb_health_print
        echo "  (container untouched — it reconnects to host.docker.internal:${KB_PORT})"
        ;;
    stop)
        _kb_stop
        ;;
    status)
        _kb_status
        ;;
    *)
        echo "usage: $0 {start|restart|stop|status}" >&2
        exit 1
        ;;
esac
