# Contributing to ONE Data Commons

This is ONE Campaign's fork of the [Data Commons website](https://github.com/datacommonsorg/website). We track the upstream `customdc_stable` branch and add our own customizations.

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/)
- [Google Cloud SDK](https://cloud.google.com/sdk/docs/install) (`gcloud`)
- [just](https://github.com/casey/just) — command runner (`brew install just`)
- Git with access to this repository
- Access to the `one-data-commons` GCP project (for deployment)

## Quick Start

```bash
# 1. Clone and set up remotes
git clone https://github.com/ONEcampaign/one-datacommons-website.git
cd one-datacommons-website
git remote add upstream https://github.com/datacommonsorg/website.git

# 2. Run first-time setup (creates env file, configures Docker auth, updates submodules)
just setup

# 3. Edit your env file with API keys and secrets
#    (ask a team member for the values)
nano custom_dc/one/env.list

# 4. Build the Docker image
just build

# 5. Run locally on http://localhost:8080
just run
```

## Available Commands

Run `just` or `just --list` for the full list. Here are the most common ones:

| Command | What it does |
|---------|-------------|
| `just setup` | First-time setup (env file, Docker auth, submodules) |
| `just build` | Build the Docker image locally |
| `just run` | Run locally on port 8080 |
| `just shell` | Start container with bash (for debugging) |
| `just sync` | Guided upstream sync from `customdc_stable` |
| `just sync-auto` | Automatic upstream sync (merge + submodule update) |
| `just deploy` | Build and push to Artifact Registry |
| `just status` | Show current branch, remotes, Docker images |
| `just stop` | Stop running containers |
| `just clean` | Remove built Docker images |

## Syncing Upstream Changes

The upstream Data Commons project tags stable releases as `customdc_stable`. To pull in their latest changes:

```bash
# Guided (shows you each step):
just sync

# Automatic (does everything in one go):
just sync-auto
```

If there are merge conflicts, resolve them, then run `just submodules` to update git submodules.

## Project Structure (ONE-specific)

```
custom_dc/one/              # ONE-specific configuration
  env.list                  # Your local env file (gitignored)
  env.list.sample           # Template for env file (committed)
  local_env.list            # Alternative env file (gitignored)

server/templates/custom_dc/ # Custom HTML templates
server/config/              # Server configuration

static/custom_dc/           # Custom static assets (CSS, JS, images)
```

## Configuration

All environment variables are in `custom_dc/one/env.list`. Key settings:

- `DC_API_KEY` / `MAPS_API_KEY` — API keys (ask a team member)
- `INPUT_DIR` / `OUTPUT_DIR` — Data directories (GCS or local paths)
- `FLASK_ENV=one` — Tells the app to use `custom_dc/one/` customizations
- `DOCKER_REGISTRY` — Where `just push` sends images
- `ENABLE_MODEL=true` — Enables NL search functionality

## Deployment

Staging deployment happens automatically when you push to the `staging` branch.

For manual deployment:

```bash
# Build and push to Artifact Registry
just deploy

# Or step by step:
just build   # Build image
just push    # Tag and push to registry
```

The registry path is configured via `DOCKER_REGISTRY` in your env file. Default: `us-east4-docker.pkg.dev/one-data-commons/datacommons/website-compose`.

## Branching Strategy

- `master` — Main branch for the ONE fork
- `staging` — Auto-deploys to staging environment
- `customdc_stable` — Tracks upstream stable releases
- Feature branches — Branch off `master`, merge back via PR
