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

# Fetch upstream and show what's changed since last sync
sync:
    #!/usr/bin/env bash
    set -euo pipefail
    CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
    echo "Fetching from upstream..."
    git fetch upstream
    echo ""
    echo "Current branch: $CURRENT_BRANCH"
    BEHIND=$(git rev-list --count HEAD..upstream/{{UPSTREAM_BRANCH}})
    echo "Commits behind upstream/{{UPSTREAM_BRANCH}}: $BEHIND"
    echo ""
    if [ "$BEHIND" -eq 0 ]; then
        echo "Already up to date."
    else
        echo "To merge, run one of:"
        echo "  just sync-auto       # merge, keep both sides (may need manual conflict resolution)"
        echo "  just sync-theirs     # merge, prefer upstream for conflicts (safe for most files)"
        echo "  just sync-abort      # abort a failed merge and start over"
    fi

# Merge upstream, stop on conflicts for manual resolution
sync-auto:
    #!/usr/bin/env bash
    set -euo pipefail
    CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
    echo "Fetching from upstream..."
    git fetch upstream
    echo ""
    echo "Merging upstream/{{UPSTREAM_BRANCH}} into $CURRENT_BRANCH..."
    if git merge upstream/{{UPSTREAM_BRANCH}}; then
        echo ""
        echo "Updating submodules..."
        ./scripts/update_git_submodules.sh
        echo ""
        echo "Sync complete. No conflicts."
    else
        echo ""
        CONFLICTS=$(git diff --name-only --diff-filter=U)
        COUNT=$(echo "$CONFLICTS" | wc -l | tr -d ' ')
        echo "═══════════════════════════════════════════"
        echo " $COUNT files with conflicts"
        echo "═══════════════════════════════════════════"
        echo ""
        echo "$CONFLICTS"
        echo ""
        echo "Options:"
        echo "  just sync-theirs     # accept upstream version for ALL conflicts"
        echo "  just sync-resolve    # accept upstream for most, list ONE-modified files to review"
        echo "  just sync-abort      # abort merge and go back to previous state"
    fi

# Merge upstream, accept their version for all conflicts
sync-theirs:
    #!/usr/bin/env bash
    set -euo pipefail
    CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
    # Start merge if not already mid-merge
    if git rev-parse --verify MERGE_HEAD > /dev/null 2>&1; then
        echo "Continuing in-progress merge..."
    else
        echo "Fetching from upstream..."
        git fetch upstream
        echo ""
        echo "Merging upstream/{{UPSTREAM_BRANCH}} into $CURRENT_BRANCH..."
        git merge upstream/{{UPSTREAM_BRANCH}} || true
    fi
    echo ""
    # Resolve each conflict type
    # UU = both modified, AA = both added — accept theirs
    UU_AA=$(git status --porcelain | grep "^UU\|^AA" | awk '{print $2}' | grep -v "^import$" || true)
    if [ -n "$UU_AA" ]; then
        COUNT=$(echo "$UU_AA" | wc -l | tr -d ' ')
        echo "Accepting upstream version for $COUNT conflicted files..."
        echo "$UU_AA" | while read -r file; do
            git checkout --theirs -- "$file" 2>/dev/null && git add "$file"
        done
        echo ""
    fi
    # UD = deleted by upstream, modified by us — remove
    UD=$(git status --porcelain | grep "^UD" | awk '{print $2}' || true)
    if [ -n "$UD" ]; then
        echo "Removing files deleted by upstream..."
        echo "$UD" | while read -r file; do
            git rm --force "$file" 2>/dev/null || true
        done
        echo ""
    fi
    # DU = deleted by us, modified by upstream — accept theirs
    DU=$(git status --porcelain | grep "^DU" | awk '{print $2}' || true)
    if [ -n "$DU" ]; then
        echo "Restoring files upstream modified that we had deleted..."
        echo "$DU" | while read -r file; do
            git checkout --theirs -- "$file" 2>/dev/null && git add "$file" 2>/dev/null || true
        done
        echo ""
    fi
    # DD = both deleted (rename conflicts) — just remove
    DD=$(git status --porcelain | grep "^DD" | awk '{print $2}' || true)
    if [ -n "$DD" ]; then
        echo "Removing files deleted by both sides..."
        echo "$DD" | while read -r file; do
            git rm --force "$file" 2>/dev/null || git add "$file" 2>/dev/null || true
        done
        echo ""
    fi
    # Submodule conflicts
    if git status --porcelain | grep -q "^UU import"; then
        echo "Resolving import submodule conflict..."
        cd import && git fetch origin && git checkout origin/master && cd ..
        git add import
    fi
    # Complete the merge
    if git rev-parse --verify MERGE_HEAD > /dev/null 2>&1; then
        git commit --no-edit
    fi
    echo ""
    echo "Updating submodules..."
    ./scripts/update_git_submodules.sh
    echo ""
    echo "Sync complete. All conflicts resolved using upstream versions."

# Merge upstream, accept theirs for most files but protect ONE-customized paths
sync-resolve:
    #!/usr/bin/env bash
    set -euo pipefail
    PROTECTED_FILE=".one-protected-paths"
    # Check if we're mid-merge
    if ! git rev-parse --verify MERGE_HEAD > /dev/null 2>&1; then
        echo "No merge in progress. Run 'just sync-auto' first."
        exit 1
    fi
    # Load protected paths (strip comments and blanks)
    if [ ! -f "$PROTECTED_FILE" ]; then
        echo "Warning: $PROTECTED_FILE not found. All conflicts will use upstream version."
        PROTECTED=""
    else
        PROTECTED=$(grep -v '^#' "$PROTECTED_FILE" | grep -v '^$')
    fi
    is_protected() {
        local file="$1"
        while IFS= read -r pattern; do
            [ -z "$pattern" ] && continue
            # Directory prefix match (pattern ends with /)
            if [[ "$pattern" == */ ]] && [[ "$file" == "$pattern"* ]]; then
                return 0
            fi
            # Exact match
            if [[ "$file" == "$pattern" ]]; then
                return 0
            fi
        done <<< "$PROTECTED"
        return 1
    }
    # Categorize all conflicts
    ALL_CONFLICTS=$(git status --porcelain | grep "^UU\|^AA\|^UD\|^DU\|^DD\|^AU\|^UA" | grep -v "^UU import$" || true)
    AUTO_COUNT=0
    REVIEW_LIST=""
    while IFS= read -r line; do
        [ -z "$line" ] && continue
        status="${line:0:2}"
        file="${line:3}"
        if is_protected "$file"; then
            REVIEW_LIST="$REVIEW_LIST"$'\n'"  [$status] $file"
            continue
        fi
        case "$status" in
            "UU"|"AA")
                git checkout --theirs -- "$file" 2>/dev/null && git add "$file"
                ((AUTO_COUNT++)) || true
                ;;
            "UD")
                git rm --force "$file" 2>/dev/null || true
                ((AUTO_COUNT++)) || true
                ;;
            "DU")
                git checkout --theirs -- "$file" 2>/dev/null && git add "$file" 2>/dev/null || true
                ((AUTO_COUNT++)) || true
                ;;
            "DD")
                git rm --force "$file" 2>/dev/null || git add "$file" 2>/dev/null || true
                ((AUTO_COUNT++)) || true
                ;;
            "UA"|"AU")
                git checkout --theirs -- "$file" 2>/dev/null && git add "$file" 2>/dev/null || true
                ((AUTO_COUNT++)) || true
                ;;
        esac
    done <<< "$ALL_CONFLICTS"
    echo "Auto-resolved $AUTO_COUNT files using upstream version."
    echo ""
    # Submodule conflicts
    if git status --porcelain | grep -q "^UU import"; then
        echo "Resolving import submodule conflict..."
        cd import && git fetch origin && git checkout origin/master && cd ..
        git add import
    fi
    # Report protected files that still need review
    REMAINING=$(git diff --name-only --diff-filter=U 2>/dev/null || true)
    REVIEW_LIST=$(echo "$REVIEW_LIST" | sed '/^$/d')
    if [ -n "$REMAINING" ] || [ -n "$REVIEW_LIST" ]; then
        echo "═══════════════════════════════════════════════════════"
        echo " Protected files needing manual review:"
        echo "═══════════════════════════════════════════════════════"
        if [ -n "$REVIEW_LIST" ]; then
            echo "$REVIEW_LIST"
        fi
        echo ""
        echo "Protected paths loaded from: $PROTECTED_FILE"
        echo ""
        echo "Resolve these manually, then run:"
        echo "  git add <files>"
        echo "  git commit --no-edit"
        echo "  just submodules"
    else
        git commit --no-edit
        echo ""
        echo "Updating submodules..."
        ./scripts/update_git_submodules.sh
        echo ""
        echo "Sync complete."
    fi

# Abort a failed merge and return to previous state
sync-abort:
    #!/usr/bin/env bash
    if git rev-parse --verify MERGE_HEAD > /dev/null 2>&1; then
        git merge --abort
        echo "Merge aborted. Back to clean state."
    else
        echo "No merge in progress."
    fi

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
