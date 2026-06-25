#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="galaxy-voting"
CONTAINER_NAME="galaxy-voting"
HOST_PORT=35091
CONTAINER_PORT=35091
ANALYSIS_IMAGE_DIR="/home/wnk/code/s4g-p4-galfit/gadotti-0513/"
CONTAINER_ANALYSIS_DIR="/data/analysis_images"
VIEW_DIR="/home/wnk/code/view"
DB_DIR="${VIEW_DIR}/data"
GALFIT_PARENT1="/home/wnk/code/s4g-p4-galfit"
GALFIT_PARENT2="/media/data/galfits"

# visualRAG KB service linkage. Default to the host KB service over the docker
# gateway (the container already has --add-host=host.docker.internal:host-gateway)
# so a bare `start.sh` links the KB too — start_all.sh exports this same URL and
# thus overrides the default. Set VISUALRAG_SERVICE_URL= (empty) to disable
# linkage (badge red, ingest buttons no-op).
VISUALRAG_PORT="${VISUALRAG_PORT:-8765}"
# `-` (not `:-`): only substitute when UNSET, so a bare `start.sh` (unset) gets
# the host KB URL, while `VISUALRAG_SERVICE_URL= bash start.sh` (set-but-empty)
# stays empty → linkage off.
VISUALRAG_SERVICE_URL="${VISUALRAG_SERVICE_URL-http://host.docker.internal:${VISUALRAG_PORT}}"
VISUALRAG_ENABLED="${VISUALRAG_ENABLED:-1}"

# Aux mask/sigma/PSF files are symlinks into a shared examples tree (absolute
# host paths). Bind-mount that tree read-only at the SAME host path so the
# symlinks resolve inside the container. Override / add more with
# VISUALRAG_AUX_RO (colon-separated extra read-only roots).
AUX_RO_ROOT="${VISUALRAG_AUX_RO_ROOT:-/home/wnk/code/GALFITS_examples}"
VISUALRAG_AUX_RO="${VISUALRAG_AUX_RO:-}"
AUX_MOUNT_FLAGS=""
for root in "${AUX_RO_ROOT}" ${VISUALRAG_AUX_RO//:/ }; do
    [ -n "${root}" ] && [ -d "${root}" ] && AUX_MOUNT_FLAGS+=" -v ${root}:${root}:ro"
done

# Build image
echo "Building Docker image: ${IMAGE_NAME}..."
docker build -t "${IMAGE_NAME}" .

# Remove existing container
if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "Removing existing container: ${CONTAINER_NAME}..."
    docker rm -f "${CONTAINER_NAME}"
fi

# Build volume mount flags for parent dirs
VOLUME_FLAGS=""
if [ -d "${GALFIT_PARENT1}" ]; then
    VOLUME_FLAGS+=" -v ${GALFIT_PARENT1}:/data/galfit:ro"
fi
if [ -d "${GALFIT_PARENT2}" ]; then
    VOLUME_FLAGS+=" -v ${GALFIT_PARENT2}:/data/galfits:ro"
fi

# Build GALFIT_PARENT_DIRS value
PARENT_DIRS="galfit:/data/galfit,galfits:/data/galfits"

# Run container
echo "Starting container: ${CONTAINER_NAME}..."
docker run -d \
    --restart=unless-stopped \
    --name "${CONTAINER_NAME}" \
    -p "${HOST_PORT}:${CONTAINER_PORT}" \
    --add-host=host.docker.internal:host-gateway \
    ${VOLUME_FLAGS} \
    ${AUX_MOUNT_FLAGS} \
    -v "${ANALYSIS_IMAGE_DIR}:${CONTAINER_ANALYSIS_DIR}:ro" \
    -v "${DB_DIR}:/app/db_data" \
    -v "${VIEW_DIR}/templates:/app/templates:ro" \
    -v "${VIEW_DIR}/static:/app/static:ro" \
    -e "GALFIT_PARENT_DIRS=${PARENT_DIRS}" \
    -e "ANALYSIS_IMAGE_DIR=${CONTAINER_ANALYSIS_DIR}" \
    -e "DATABASE=/app/db_data/galfit_viewer.db" \
    -e "VISUALRAG_SERVICE_URL=${VISUALRAG_SERVICE_URL}" \
    -e "VISUALRAG_ENABLED=${VISUALRAG_ENABLED}" \
    "${IMAGE_NAME}"

# Wait and check
sleep 2
if docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "Container started successfully."
    echo "  Service: http://127.0.0.1:${HOST_PORT}"
    if [ -n "${VISUALRAG_SERVICE_URL}" ]; then
        echo "  visualRAG KB: ${VISUALRAG_SERVICE_URL} (badge should turn green)"
    else
        echo "  visualRAG KB: not linked (set VISUALRAG_SERVICE_URL or run start_all.sh)"
    fi
    echo "  Parent volumes:"
    [ -d "${GALFIT_PARENT1}" ] && echo "    ${GALFIT_PARENT1} -> /data/galfit"
    [ -d "${GALFIT_PARENT2}" ] && echo "    ${GALFIT_PARENT2} -> /data/galfits"
    docker logs "${CONTAINER_NAME}" 2>&1 | tail -5
else
    echo "Container failed to start. Logs:"
    docker logs "${CONTAINER_NAME}" 2>&1
    exit 1
fi
