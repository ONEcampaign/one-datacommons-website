# Contributing to ONE Data Commons

This is the ONE Data fork of the [Data Commons website](https://github.com/datacommonsorg/website). We track the upstream `customdc_stable` branch and add our own customizations.

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
| `just dev` | Run with live frontend assets (pair with `just watch`) |
| `just watch` | Start webpack watch mode for incremental frontend rebuilds |
| `just shell` | Start container with bash (for debugging) |
| `just sync` | Guided upstream sync from `customdc_stable` |
| `just sync-auto` | Automatic upstream sync (merge + submodule update) |
| `just deploy` | Build and push to Artifact Registry |
| `just status` | Show current branch, remotes, Docker images |
| `just stop` | Stop running containers |
| `just clean` | Remove built Docker images |

## Frontend Development Without Full Rebuilds

A full Docker build takes ~15 minutes because it bundles the entire frontend via webpack. For frontend-only changes (TypeScript, React components, CSS), you can skip that by running webpack locally in watch mode:

**Terminal 1** — start the file watcher:
```bash
just watch
```

This runs webpack in development mode with `--watch`. It does a full build on first run, then rebuilds incrementally (a few seconds) whenever you save a file under `static/`.

**Terminal 2** — run the container with live assets:
```bash
just dev
```

This is the same as `just run` but mounts your local `server/dist/` into the container, so the Flask server serves the locally-built frontend. After webpack rebuilds, reload the page in your browser to see changes.

You still need a full `just build` for:
- Python/Flask server changes
- Changes to Docker-level configuration
- Changes to dependencies (`package.json`)
- Final verification before deploying

## Syncing Upstream Changes

The upstream Data Commons project releases stable updates on the `customdc_stable` branch. To pull in their latest changes:

```bash
# 1. Check how far behind you are:
just sync

# 2. Merge (pick one):
just sync-auto       # Merge, stop on conflicts for manual resolution
just sync-theirs     # Merge, accept upstream for ALL conflicts
just sync-resolve    # Merge, accept upstream for most, flag protected files for review
```

### Recommended sync workflow

1. Run `just sync-auto` first.
2. If there are conflicts, run `just sync-resolve`. This auto-resolves most files using upstream's version, but flags files listed in `.one-protected-paths` for manual review.
3. Resolve the flagged files manually (usually just `.gitignore` and `static/package.json`), then `git add` and `git commit --no-edit`.
4. Run `just submodules` to update git submodules.

If something goes wrong: `just sync-abort` returns to the pre-merge state.

## Project Structure (ONE-specific)

```
custom_dc/one/                          # Environment configuration
  env.list                              # Local dev env file (gitignored, from env.list.sample)
  env.staging.list                      # Staging env file (gitignored, from env.staging.sample)
  env.prod.list                         # Production env file (gitignored, from env.prod.sample)
  env.list.sample                       # Local dev template (committed)
  env.staging.sample                    # Staging template (committed)
  env.prod.sample                       # Production template (committed)

server/app_env/one.py                   # Flask app config (FLASK_ENV=one)
server/templates/custom_dc/one/         # Custom Jinja templates (header, footer, pages)

static/js/apps/custom_dc/one/          # Custom React apps
  base/                                 # Header, footer, base template entry point
    main.ts                             # Replaces upstream base entry point
    header_app.tsx                      # Custom header component
    components/                         # Custom components (footer, header bar, search)
  homepage/                             # Custom homepage React app
    main.ts                             # Replaces upstream homepage entry point
    app.tsx                             # Homepage component

static/js/theme/                        # Theme system
  theme.ts, types.ts, emotion.d.ts      # Upstream root-level theme (for relative imports)
  base_theme/                           # Upstream default theme (copy)
  dc_custom_theme/                      # ONE custom Emotion theme (override)

static/css/custom_dc/one/              # Custom SCSS
static/custom_dc/one/                  # Static assets (favicon, fonts, images, overrides.css)

static/webpack.one.js                  # Webpack wrapper (loads base config + ONE overrides)
static/webpack.custom_dc.js            # ONE overrides (entry points, aliases)
```

## Architecture: How Customization Works

ONE's customization goes beyond the [official Custom DC model](https://docs.datacommons.org/custom_dc/custom_ui.html) (which only supports Jinja templates and CSS overrides). We also replace React entry points and use a custom Emotion theme. Here's how it all fits together.

### Webpack Build Chain

The upstream `webpack.config.js` is **never modified**. Instead, we use a wrapper:

```
package.json build command
  → webpack --config webpack.one.js
      → loads webpack.config.js        (upstream, untouched)
      → loads webpack.custom_dc.js     (ONE overrides)
      → merges entry points and resolve aliases
      → outputs to server/dist/
```

`static/webpack.one.js` imports the base config and applies overrides from `static/webpack.custom_dc.js`:

- **Entry point replacements** — `base` and `homepage` are replaced with ONE's custom React apps. The default `homepage_custom_dc` entry is removed.
- **Webpack aliases** — `import from 'theme'` resolves to `dc_custom_theme/` instead of the root-level theme. The `auto_complete_input` component is replaced with ONE's custom version.

### Theme System

There are two ways upstream code imports themes:

| Import style | Resolves to | Used by |
|---|---|---|
| `import from '../../theme/theme'` (relative) | `static/js/theme/theme.ts` | ~24 upstream files |
| `import from 'theme'` (bare module) | `static/js/theme/dc_custom_theme/` via webpack alias | ~10 files (homepages, landing pages) |

The root-level `theme.ts` must always match upstream. The `dc_custom_theme/` directory is where ONE can diverge colors, typography, spacing, etc. without touching any upstream files.

### Flask Configuration

`FLASK_ENV=one` tells the server to load `server/app_env/one.py`, which sets:
- `CUSTOM = True` — enables custom DC mode
- `NAME = "ONE Data Commons"` — site branding
- `OVERRIDE_CSS_PATH` — points to ONE's CSS overrides
- Template directory — `server/templates/custom_dc/one/`

### What's Safe to Change Without Merge Conflicts

| Location | Risk | Notes |
|---|---|---|
| `custom_dc/one/` | None | ONE-only directory, not in upstream |
| `static/js/apps/custom_dc/one/` | None | ONE-only React apps |
| `static/js/theme/dc_custom_theme/` | None | ONE-only theme |
| `static/css/custom_dc/one/` | None | ONE-only styles |
| `server/templates/custom_dc/one/` | None | ONE-only templates |
| `static/webpack.one.js` | None | ONE-only wrapper |
| `static/webpack.custom_dc.js` | None | ONE-only overrides |
| `justfile` | None | ONE-only, not in upstream |
| `static/js/theme/base_theme/` | Low | ONE copy; keep in sync with upstream `theme/` |
| `static/package.json` | Low | 3 config filename changes in wireit section |
| `static/tsconfig.json` | Low | `baseUrl` and `paths` added for theme/component aliases |
| `.gitignore` | Low | Additive entries for ONE env files |

### Override Drift Detection

ONE replaces several upstream components via webpack aliases (e.g., the search bar, homepage, base app). When upstream changes the original files, ONE's replacements may need updating to stay compatible.

`.one-overridden-files` maps each upstream file to its ONE replacement. After every sync, `just check-overrides` runs automatically and warns if any originals have changed:

```
═══════════════════════════════════════════════════════════
 ⚠  1 overridden file(s) changed upstream
═══════════════════════════════════════════════════════════

  ⚠  static/js/components/nl_search_bar/auto_complete_input.tsx
     ONE replacement: static/js/apps/custom_dc/one/base/components/nl_search_bar/auto_complete_input.tsx
     Changes: 1 file changed, 5 insertions(+), 2 deletions(-)
```

If you see warnings, compare the upstream change with ONE's version and update as needed. You can also run `just check-overrides` at any time.

If you add a new component substitution, add the mapping to `.one-overridden-files`.

### Protected Paths

`.one-protected-paths` lists files that `just sync-resolve` will flag for manual review instead of auto-accepting upstream's version. If you add new ONE-specific modifications to upstream files, add them here.

## Configuration

There is one env file per environment, all under `custom_dc/one/`:

| File | Purpose |
|------|---------|
| `env.list` | Local development (created by `just env`) |
| `env.staging.list` | Staging environment |
| `env.prod.list` | Production environment |

Each is gitignored. Create them from the matching `.sample` template (`just env` for local, `just env-all` for all three).

**Local development** (`env.list`) — you only need to fill in API keys:

- `DC_API_KEY` / `MAPS_API_KEY` — API keys (ask a team member)
- `FLASK_ENV=one` — Tells the app to use `custom_dc/one/` customizations
- `ENABLE_MODEL=true` — Enables NL search functionality

Data directories (`INPUT_DIR`, `OUTPUT_DIR`) are injected automatically by the justfile based on your repo location — you don't need to configure them.

**Staging / Production** (`env.staging.list`, `env.prod.list`) — these also include GCS paths for `INPUT_DIR`/`OUTPUT_DIR`, Cloud SQL credentials, and Redis/admin settings.

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
