#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="galaxy-voting"
CONTAINER_NAME="galaxy-voting"
HOST_PORT=35091
CONTAINER_PORT=35091
ANALYSIS_IMAGE_DIR="/home/wnk/code/s4g-p4-galfit/gadotti-0513/"
CONTAINER_ANALYSIS_DIR="/data/analysis_images"
DB_DIR="/home/wnk/code/view/data"

# Define data sources: "LABEL:HOST_PATH:CONTAINER_PATH"
SOURCES=(
  "gadotti-0513:/home/wnk/code/s4g-p4-galfit/gadotti-0513:/data/gadotti-0513"
  "s4g-cc5:/home/wnk/code/s4g-p4-galfit/filter_mag_lt9_cc5/:/data/s4g-cc5"
  "zhongyi-0512:/home/wnk/code/s4g-p4-galfit/galfit_data_0512:/data/zhongyi-0512"
  "gadotti-0519:/home/wnk/code/s4g-p4-galfit/gadotti-0519:/data/gadotti-0519"
  "gadotti-0520:/home/wnk/code/s4g-p4-galfit/gadotti-0520:/data/gadotti-0520"
  "zhongyi_0520:/home/wnk/code/s4g-p4-galfit/zhongyi_0520:/data/zhongyi_0520"
  "gadotti-0521:/home/wnk/code/s4g-p4-galfit/gadotti-0521:/data/gadotti-0521"
  "gadotti-0522:/home/wnk/code/s4g-p4-galfit/gadotti-0522:/data/gadotti-0522"
  "gadotti-0527:/home/wnk/code/s4g-p4-galfit/gadotti-0527:/data/gadotti-0527"
  "gadotti-0528:/home/wnk/code/s4g-p4-galfit/gadotti-0528:/data/gadotti-0528"
)

# Build GALFIT_SOURCES JSON and volume mount flags
GALFIT_SOURCES_JSON="{"
VOLUME_FLAGS=""
FIRST=true
for src in "${SOURCES[@]}"; do
  IFS=: read -r label host_path container_path <<< "$src"
  [ "$FIRST" = true ] && FIRST=false || GALFIT_SOURCES_JSON+=","
  GALFIT_SOURCES_JSON+="\"${label}\":\"${container_path}\""
  VOLUME_FLAGS+=" -v ${host_path}:${container_path}:ro"
done
GALFIT_SOURCES_JSON+="}"

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
    ${VOLUME_FLAGS} \
    -v "${ANALYSIS_IMAGE_DIR}:${CONTAINER_ANALYSIS_DIR}:ro" \
    -v "${DB_DIR}:/app/db_data" \
    -e "GALFIT_SOURCES=${GALFIT_SOURCES_JSON}" \
    -e "ANALYSIS_IMAGE_DIR=${CONTAINER_ANALYSIS_DIR}" \
    -e "DATABASE=/app/db_data/galfit_viewer.db" \
    "${IMAGE_NAME}"

# Wait and check
sleep 2
if docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "Container started successfully."
    echo "  Service: http://127.0.0.1:${HOST_PORT}"
    echo "  Sources: ${GALFIT_SOURCES_JSON}"
    docker logs "${CONTAINER_NAME}" 2>&1 | tail -5
else
    echo "Container failed to start. Logs:"
    docker logs "${CONTAINER_NAME}" 2>&1
    exit 1
fi
