# Copyright 2024 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# #### Stage 1: Build mixer. ####
FROM golang:1.24.0 AS mixer

WORKDIR /workspace

# Install the protobuf compiler and Go plugins.
RUN apt-get update \
    && apt-get install -y protobuf-compiler \
    && go install google.golang.org/protobuf/cmd/protoc-gen-go@v1.30.0 \
    && go install google.golang.org/grpc/cmd/protoc-gen-go-grpc@v1.3.0

COPY mixer/go.mod mixer/go.sum ./
RUN go mod download -x

# Copy files
COPY mixer/proto/ proto
COPY mixer/internal/ internal
COPY mixer/cmd/ cmd
COPY mixer/esp/ esp

# Build protobuf.
RUN protoc \
    --include_source_info \
    --include_imports \
    --proto_path=proto \
    --go_out=paths=source_relative:internal/proto \
    --go-grpc_out=paths=source_relative:internal/proto \
    --go-grpc_opt=require_unimplemented_servers=false \
    --experimental_allow_proto3_optional \
    --descriptor_set_out mixer-grpc.pb \
    proto/*.proto proto/**/*.proto

# Build mixer.
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o bin/mixer cmd/main.go


# #### Stage 2: Build python servers (website and NL). ####
FROM --platform=linux/amd64 python:3.11.14-slim as py-servers

ARG PIP_DISABLE_PIP_VERSION_CHECK=1
ARG PIP_NO_CACHE_DIR=1

WORKDIR /workspace

# Install git which is needed to install web server requirements
RUN apt-get update && apt-get -y upgrade && apt-get install -y git

# Copy requirements.
# Copy nl_requirements.txt since it is referenced by embeddings requirements.txt
COPY server/requirements.txt ./server/requirements.txt
COPY nl_server/requirements.txt ./nl_server/requirements.txt
COPY nl_requirements.txt ./nl_requirements.txt
COPY torch_requirements.txt ./torch_requirements.txt

# Create a virtual env and install requirements.
RUN python -m venv ./venv
ENV PATH="/workspace/venv/bin:$PATH"
RUN pip3 install --upgrade pip setuptools \
    && pip3 install --no-cache-dir -r /workspace/server/requirements.txt \
    && pip3 install --no-cache-dir -r /workspace/torch_requirements.txt --index-url https://download.pytorch.org/whl/cpu \
    && pip3 install --no-cache-dir -r /workspace/nl_server/requirements.txt \
    && pip3 install --no-cache-dir datacommons-mcp

# dc-search service — isolated venv on CPython 3.12 (resolvekit requires >=3.12).
# uv provides a self-contained standalone interpreter so the venv's python binary
# is embedded in /workspace/dc_search_venv and survives the COPY --from=py-servers
# into the runtime stage without requiring system python3.12 there.
#
# Fallback (if uv-standalone proves unusable in the runtime base):
#   RUN apt-get install -y python3.12 python3.12-venv \
#       && python3.12 -m venv /workspace/dc_search_venv
# Note: the slim base has no deadsnakes PPA; use direct apt packages as above.
#
# Deploy verification gate — run against the RUNTIME image (not just py-servers):
#   docker run --rm <runtime-img> \
#     /workspace/dc_search_venv/bin/python -c "import sys, resolvekit; print(sys.version)"
# Must print 3.12.x and import cleanly before treating this Dockerfile edit as final.
COPY --from=ghcr.io/astral-sh/uv:0.11.20 /uv /bin/uv
# Install the standalone CPython INSIDE /workspace so it is carried into the
# runtime stage by `COPY --from=py-servers /workspace/ .`. uv's default install
# dir (~/.local/share/uv/python) lives OUTSIDE /workspace and is NOT copied,
# which leaves the dc_search_venv interpreter — and thus gunicorn's shebang —
# dangling at runtime ("cannot execute: required file not found" / exit 127).
ENV UV_PYTHON_INSTALL_DIR=/workspace/uv-python
RUN uv python install 3.12.13 \
    && uv venv --python 3.12.13 /workspace/dc_search_venv \
    && test -x "$(readlink -f /workspace/dc_search_venv/bin/python)" \
    && case "$(readlink -f /workspace/dc_search_venv/bin/python)" in /workspace/*) : ;; *) echo "FATAL: venv interpreter not under /workspace — would not survive COPY into runtime"; exit 1 ;; esac
COPY dc_search_server/requirements.txt /workspace/dc_search_server/requirements.txt
RUN uv pip install --no-cache-dir \
    --python /workspace/dc_search_venv/bin/python \
    -r /workspace/dc_search_server/requirements.txt

# Pre-bake ALL remote resolvekit data packs (the full geo hierarchy: geo.cities
# + geo.admin1–admin5) into the image cache, so the service never downloads at
# boot: no cold-start stall, no runtime network egress, no ephemeral-disk churn.
# The country / region / continent / org packs already ship inside the wheel.
# ~810 MB on disk (~1.6 GB resident once the fuzzy indexes build — well under
# the Cloud Run memory limit). The runtime stage runs OFFLINE
# (RESOLVEKIT_OFFLINE=1) and loads exactly what's baked here.
ENV RESOLVEKIT_CACHE_DIR=/workspace/resolvekit_cache
RUN /workspace/dc_search_venv/bin/python -c "import resolvekit as rk; rk.download_all(); have=sorted(m.module_id for m in rk.modules() if getattr(m,'is_available',False)); assert 'geo.cities' in have and any(m.startswith('geo.admin') for m in have), f'packs not baked: {have}'; print('resolvekit packs baked into image cache:', have)" \
    && find /workspace/resolvekit_cache -name '*.gz' -delete \
    && du -sh /workspace/resolvekit_cache

COPY dc_search_server/ /workspace/dc_search_server/

# Copy website app script.
COPY web_app.py ./web_app.py
# Copy server module.
COPY server/. ./server/
# Copy NL app script.
COPY nl_app.py ./nl_app.py
# Copy nl_server module.
COPY nl_server/. ./nl_server/
# Copy the shared module.
COPY shared/. ./shared/
# Copy yaml files.
COPY deploy/nl/. /datacommons/nl/


# #### Stage 3: Build static website assets. ####
FROM --platform=linux/amd64 node:20 as static

WORKDIR /workspace

# Insall npm dependencies.
COPY packages/web-components/package.json ./packages/web-components/package.json
COPY packages/web-components/package-lock.json ./packages/web-components/package-lock.json

COPY packages/client/package.json ./packages/client/package.json
COPY packages/client/package-lock.json ./packages/client/package-lock.json

COPY static/package.json ./static/package.json
COPY static/package-lock.json ./static/package-lock.json

RUN npm --prefix /workspace/packages/web-components ci --omit=dev \
    && npm --prefix /workspace/packages/client ci \
    && npm --prefix /workspace/static ci --omit=dev

# Build static assets.
COPY packages/. ./packages/
COPY static/. ./static/
WORKDIR /workspace/static
RUN npm run-script build


# #### Stage 4: Download base dc model and embeddings from GCS. ####
FROM --platform=linux/amd64 google/cloud-sdk:slim as download

# Remove auth check since these are public buckets and we don't have credentials within the container.
# Copy model and embeddings.
RUN gcloud config set auth/disable_credentials True \
    && mkdir -p /tmp/datcom-nl-models \
    && gcloud storage cp --recursive \
        gs://datcom-nl-models/ft_final_v20230717230459.all-MiniLM-L6-v2/ \
        gs://datcom-nl-models/embeddings_medium_2024_05_09_18_01_32.ft_final_v20230717230459.all-MiniLM-L6-v2.csv \
        /tmp/datcom-nl-models/


# #### Final stage: Runtime env. ####
FROM gcr.io/datcom-ci/datacommons-services-runtime:2024-08-08 as runner

# Env defaults.
ENV WEBSITE_MIXER_API_ROOT=http://127.0.0.1:8081 \
    ENV_PREFIX=Compose \
    FLASK_ENV=custom \
    ENABLE_ADMIN=false \
    DEBUG=false \
    USE_SQLITE=false \
    USE_CLOUDSQL=false \
    DC_API_ROOT=https://api.datacommons.org \
    ENABLE_MODEL=true \
    ENABLE_MCP=true \
    DC_TYPE=custom \
    DC_API_BASE_URL=http://127.0.0.1:8081/v2 \
    CUSTOM_DC_URL=http://127.0.0.1:7070 \
    RESOLVEKIT_CACHE_DIR=/workspace/resolvekit_cache \
    RESOLVEKIT_OFFLINE=1 \
    RESOLVE_DEMO_DOWNLOAD=0

WORKDIR /workspace

# Copy scripts, dependencies and files from build stages and from local file system.
# Mixer.
COPY --from=mixer /workspace/bin/ bin
COPY --from=mixer /workspace/esp/ esp
COPY --from=mixer /workspace/internal/ internal
COPY --from=mixer /workspace/mixer-grpc.pb .
# nginx.
COPY build/cdc_services/nginx.conf .
# Website and NL servers.
COPY --from=py-servers /workspace/ .
COPY --from=py-servers /datacommons/ /datacommons
# Static website assets.
COPY --from=static /workspace/server ./server
# Downloaded model and embeddings.
COPY --from=download /tmp/datcom-nl-models /tmp/datcom-nl-models

# Copy executable script.
COPY build/cdc_services/run.sh .

# Make script executable.
RUN chmod +x run.sh

# Add virtual env to the path.
ENV PATH="/workspace/venv/bin:$PATH"

# Set the default command to run the script.
CMD ./run.sh
