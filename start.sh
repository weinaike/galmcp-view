#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="galaxy-voting"
CONTAINER_NAME="galaxy-voting"
HOST_PORT=35091
CONTAINER_PORT=35091
DATA_DIR="/home/wnk/code/galfit_example"
CONTAINER_DATA_DIR="/data/galfit_example"
ANALYSIS_IMAGE_DIR="/home/wnk/code/s4g-p4-galfit/filter_comp_q5"
CONTAINER_ANALYSIS_DIR="/data/analysis_images"

# Build image
echo "Building Docker image: ${IMAGE_NAME}..."
docker build -t "${IMAGE_NAME}" .

# Remove existing container
if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "Removing existing container: ${CONTAINER_NAME}..."
    docker rm -f "${CONTAINER_NAME}"
fi

# Run container
echo "Starting container: ${CONTAINER_NAME}..."
docker run -d \
    --name "${CONTAINER_NAME}" \
    -p "${HOST_PORT}:${CONTAINER_PORT}" \
    -v "${DATA_DIR}:${CONTAINER_DATA_DIR}:ro" \
    -v "${ANALYSIS_IMAGE_DIR}:${CONTAINER_ANALYSIS_DIR}:ro" \
    -e "GALFIT_BASE_PATH=${CONTAINER_DATA_DIR}" \
    -e "ANALYSIS_IMAGE_DIR=${CONTAINER_ANALYSIS_DIR}" \
    "${IMAGE_NAME}"

# Wait and check
sleep 2
if docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "Container started successfully."
    echo "  Service: http://127.0.0.1:${HOST_PORT}"
    docker logs "${CONTAINER_NAME}" 2>&1 | tail -5
else
    echo "Container failed to start. Logs:"
    docker logs "${CONTAINER_NAME}" 2>&1
    exit 1
fi
