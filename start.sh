#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="galaxy-voting"
CONTAINER_NAME="galaxy-voting"
HOST_PORT=35091
CONTAINER_PORT=35091
ANALYSIS_IMAGE_DIR="/home/wnk/code/s4g-p4-galfit/gadotti-0513/"
CONTAINER_ANALYSIS_DIR="/data/analysis_images"
DB_DIR="/home/wnk/code/view/data"
GALFIT_PARENT1="/home/wnk/code/s4g-p4-galfit"
GALFIT_PARENT2="/media/data/galfits"

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
    --name "${CONTAINER_NAME}" \
    -p "${HOST_PORT}:${CONTAINER_PORT}" \
    ${VOLUME_FLAGS} \
    -v "${ANALYSIS_IMAGE_DIR}:${CONTAINER_ANALYSIS_DIR}:ro" \
    -v "${DB_DIR}:/app/db_data" \
    -e "GALFIT_PARENT_DIRS=${PARENT_DIRS}" \
    -e "ANALYSIS_IMAGE_DIR=${CONTAINER_ANALYSIS_DIR}" \
    -e "DATABASE=/app/db_data/galfit_viewer.db" \
    "${IMAGE_NAME}"

# Wait and check
sleep 2
if docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "Container started successfully."
    echo "  Service: http://127.0.0.1:${HOST_PORT}"
    echo "  Parent volumes:"
    [ -d "${GALFIT_PARENT1}" ] && echo "    ${GALFIT_PARENT1} -> /data/galfit"
    [ -d "${GALFIT_PARENT2}" ] && echo "    ${GALFIT_PARENT2} -> /data/galfits"
    docker logs "${CONTAINER_NAME}" 2>&1 | tail -5
else
    echo "Container failed to start. Logs:"
    docker logs "${CONTAINER_NAME}" 2>&1
    exit 1
fi
