# ONE Data Commons — Developer Commands
# Run 'just' or 'just --list' to see all available targets.
#
# Override defaults from the command line:
#   just ENV_FILE=custom_dc/one/env.staging.list run
#   just IMAGE_TAG=v1.2.0 build
#   just IMAGE_TAG=v1.2.0 push

# Configuration (override any of these from the command line)
IMAGE_NAME   := "datacommons-website-compose"
IMAGE_TAG    := "latest"
IMAGE        := IMAGE_NAME + ":" + IMAGE_TAG
ENV_FILE     := "custom_dc/one/env.list"
ENV_SAMPLE   := "custom_dc/one/env.list.sample"
DOCKERFILE   := "build/cdc_services/Dockerfile"
UPSTREAM_BRANCH := "customdc_stable"

# Load registry config from env file if it exists
DOCKER_REGISTRY := env_var_or_default("DOCKER_REGISTRY", "us-east4-docker.pkg.dev/one-data-commons/datacommons/website-compose")
GOOGLE_CLOUD_REGION := env_var_or_default("GOOGLE_CLOUD_REGION", "us-east4")

# Env file paths for convenience targets
_ENV_STAGING := "custom_dc/one/env.staging.list"
_ENV_PROD    := "custom_dc/one/env.prod.list"

# Show available commands (default)
_default:
    @just --list --unsorted

# ── Setup & Configuration ────────────────────

# First-time setup: create env file, configure Docker auth, update submodules
setup: env
    #!/usr/bin/env bash
    set -euo pipefail
    echo "Configuring Docker authentication for Artifact Registry..."
    gcloud auth configure-docker {{GOOGLE_CLOUD_REGION}}-docker.pkg.dev
    echo ""
    echo "Updating git submodules..."
    ./scripts/update_git_submodules.sh
    echo ""
    echo "Setup complete. Edit {{ENV_FILE}} with your API keys and secrets, then run 'just run'."

# Create env file from template (won't overwrite existing)
env:
    #!/usr/bin/env bash
    if [ -f "{{ENV_FILE}}" ]; then
        echo "{{ENV_FILE}} already exists. Remove it first to regenerate."
    else
        cp {{ENV_SAMPLE}} {{ENV_FILE}}
        echo "Created {{ENV_FILE}} from template."
        echo "Edit it with your API keys and configuration."
    fi

# Create env files for all environments from their templates
env-all:
    #!/usr/bin/env bash
    for pair in \
        "custom_dc/one/env.list.sample:custom_dc/one/env.list" \
        "custom_dc/one/env.staging.sample:custom_dc/one/env.staging.list" \
        "custom_dc/one/env.prod.sample:custom_dc/one/env.prod.list"; do
        sample="${pair%%:*}"
        target="${pair##*:}"
        if [ -f "$target" ]; then
            echo "$target already exists, skipping."
        else
            cp "$sample" "$target"
            echo "Created $target from template."
        fi
    done
    echo ""
    echo "Edit each env file with the appropriate secrets."

# ── Upstream Sync ─────────────────────────────

# Fetch and merge upstream customdc_stable branch (guided, step-by-step)
sync:
    #!/usr/bin/env bash
    set -euo pipefail
    echo "Fetching from upstream..."
    git fetch upstream
    echo ""
    echo "To merge into your current branch, run:"
    echo "  git merge upstream/{{UPSTREAM_BRANCH}}"
    echo "  just submodules"

# Fetch and merge upstream customdc_stable into current branch automatically
sync-auto:
    #!/usr/bin/env bash
    set -euo pipefail
    CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
    echo "Fetching from upstream..."
    git fetch upstream
    echo ""
    echo "Merging upstream/{{UPSTREAM_BRANCH}} into $CURRENT_BRANCH..."
    git merge upstream/{{UPSTREAM_BRANCH}}
    echo ""
    echo "Updating submodules..."
    ./scripts/update_git_submodules.sh
    echo ""
    echo "Sync complete. Check for any merge conflicts."

# Update git submodules
submodules:
    ./scripts/update_git_submodules.sh

# ── Building ──────────────────────────────────

# Build Docker image locally (override tag: just IMAGE_TAG=v1.0 build)
build:
    @echo "Building {{IMAGE}} from {{DOCKERFILE}}..."
    docker build --tag {{IMAGE}} -f {{DOCKERFILE}} .

# ── Running Locally ───────────────────────────

# Run container locally (port 8080, debug mode)
run: _check-env
    docker run -it \
        --init \
        --env-file {{ENV_FILE}} \
        -p 8080:8080 \
        -e DEBUG=true \
        -v {{justfile_directory()}}/custom_dc/one:/userdata \
        -v {{justfile_directory()}}/server/config:/workspace/server/config \
        {{IMAGE}}

# Run container with bash shell (for debugging)
shell: _check-env
    docker run -it \
        --init \
        --env-file {{ENV_FILE}} \
        -p 8080:8080 \
        -e DEBUG=true \
        -v {{justfile_directory()}}/custom_dc/one:/userdata \
        -v {{justfile_directory()}}/server/config:/workspace/server/config \
        {{IMAGE}} \
        /bin/bash

# Run only the service container (no data reload)
run-service: _check-env
    ./run_cdc_dev_docker.sh \
        --env_file {{ENV_FILE}} \
        --actions run \
        --container service \
        --image {{IMAGE}}

# Run with staging environment config
run-staging: (_check-env-file _ENV_STAGING)
    docker run -it \
        --init \
        --env-file {{_ENV_STAGING}} \
        -p 8080:8080 \
        -e DEBUG=true \
        -v {{justfile_directory()}}/custom_dc/one:/userdata \
        -v {{justfile_directory()}}/server/config:/workspace/server/config \
        {{IMAGE}}

# Run with production environment config
run-prod: (_check-env-file _ENV_PROD)
    docker run -it \
        --init \
        --env-file {{_ENV_PROD}} \
        -p 8080:8080 \
        -e DEBUG=true \
        -v {{justfile_directory()}}/custom_dc/one:/userdata \
        -v {{justfile_directory()}}/server/config:/workspace/server/config \
        {{IMAGE}}

# ── Deploying ─────────────────────────────────

# Tag and push image to configured registry (override tag: just IMAGE_TAG=v1.0 push)
push: _check-env
    @echo "Tagging {{IMAGE}} -> {{DOCKER_REGISTRY}}:{{IMAGE_TAG}}..."
    docker tag {{IMAGE}} {{DOCKER_REGISTRY}}:{{IMAGE_TAG}}
    @echo "Pushing to {{DOCKER_REGISTRY}}:{{IMAGE_TAG}}..."
    docker push {{DOCKER_REGISTRY}}:{{IMAGE_TAG}}

# Build and push in one step
deploy: build push

# ── Utilities ─────────────────────────────────

# Show current branch, remotes, Docker images, and env files
status:
    #!/usr/bin/env bash
    echo "Git branch:"
    git branch --show-current
    echo ""
    echo "Remotes:"
    git remote -v
    echo ""
    echo "Docker images (datacommons):"
    docker images | grep -E "datacommons|REPOSITORY" || echo "  (none found)"
    echo ""
    echo "Env files:"
    for f in custom_dc/one/env.list custom_dc/one/env.staging.list custom_dc/one/env.prod.list; do
        if [ -f "$f" ]; then echo "  ✓ $f"; else echo "  ✗ $f (missing)"; fi
    done

# Stop all running datacommons containers
stop:
    #!/usr/bin/env bash
    docker ps --filter "ancestor={{IMAGE}}" -q | xargs -r docker stop
    echo "Stopped."

# Remove built Docker images
clean:
    #!/usr/bin/env bash
    docker rmi {{IMAGE}} 2>/dev/null || true
    docker rmi {{DOCKER_REGISTRY}}:{{IMAGE_TAG}} 2>/dev/null || true
    echo "Cleaned."

# ── Internal ──────────────────────────────────

_check-env:
    #!/usr/bin/env bash
    if [ ! -f "{{ENV_FILE}}" ]; then
        echo "Error: {{ENV_FILE}} not found."
        echo "Run 'just setup' or 'just env' first."
        exit 1
    fi

[no-exit-message]
_check-env-file file:
    #!/usr/bin/env bash
    if [ ! -f "{{file}}" ]; then
        echo "Error: {{file}} not found."
        echo "Run 'just env-all' to create env files for all environments."
        exit 1
    fi
