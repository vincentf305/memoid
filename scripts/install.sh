#!/usr/bin/env bash
set -euo pipefail

# Memoid Ultimate Installer
# Handles: cloning (specific ref), uv setup, memory initialization, and MCP setup guidance.
# Also handles: in-place engine updates that preserve the memory/ folder.

REPO_URL="https://github.com/latentarts/memoid.git"
GIT_REF="main"

# ------------------------------------------------------------------------------
# Utility: Print in color
# ------------------------------------------------------------------------------
printf_color() {
    local color_code=$1
    shift
    printf "\033[${color_code}m%s\033[0m\n" "$*"
}

info()    { printf_color "34" "INFO: $*"; }
success() { printf_color "32" "SUCCESS: $*"; }
warn()    { printf_color "33" "WARN: $*"; }
error()   { printf_color "31" "ERROR: $*"; }

# ------------------------------------------------------------------------------
# CLI argument parsing
# ------------------------------------------------------------------------------
print_usage() {
    cat <<EOF
Usage: install.sh [OPTIONS]

Options:
  --ref <ref>   Clone or update to a specific branch, tag, or commit.
                Default: main (latest commit on main).
  --help        Show this message.

Examples:
  install.sh
  install.sh --ref v1.2.3
  install.sh --ref develop
  install.sh --ref a1b2c3d
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --ref)
            if [[ $# -lt 2 ]]; then
                error "--ref requires a value (branch, tag, or commit)."
                exit 1
            fi
            GIT_REF="$2"
            shift 2
            ;;
        --help|-h)
            print_usage
            exit 0
            ;;
        *)
            error "Unknown option: $1"
            print_usage
            exit 1
            ;;
    esac
done

# ------------------------------------------------------------------------------
# Version display
# ------------------------------------------------------------------------------
show_version_details() {
    local desc tag commit branch
    desc=$(git describe --tags --always 2>/dev/null || echo "untagged")
    tag=$(git tag --sort=-v:refname 2>/dev/null | head -1 || echo "none")
    commit=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")

    printf "\n"
    info "Version details:"
    printf "  Version:      %s\n" "$desc"
    printf "  Latest tag:   %s\n" "$tag"
    printf "  Commit:       %s\n" "$commit"
    if git symbolic-ref -q HEAD >/dev/null 2>&1; then
        branch=$(git symbolic-ref --short HEAD)
        printf "  Branch:       %s\n" "$branch"
    else
        printf "  HEAD:         detached (at %s)\n" "$GIT_REF"
    fi
}

# ------------------------------------------------------------------------------
# Ensure uv is available
# ------------------------------------------------------------------------------
ensure_uv() {
    if command -v uv &>/dev/null; then
        return 0
    fi
    warn "uv (Python manager) not found."
    printf "Would you like to install uv now? [Y/n]: "
    local choice
    read -r choice < /dev/tty 2>/dev/null || true
    if [[ "$choice" =~ ^[Nn]$ ]]; then
        error "uv is required for Memoid. Please install it and run this script again."
        exit 1
    fi
    info "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"
    if [[ -f "$HOME/.local/bin/env" ]]; then
        source "$HOME/.local/bin/env"
    fi
    if ! command -v uv &>/dev/null; then
        error "uv installation succeeded but uv is still not on PATH."
        error "Try: export PATH=\"\$HOME/.local/bin:\$PATH\" and re-run."
        exit 1
    fi
}

# ------------------------------------------------------------------------------
# Restore memory/ from backup (used in error recovery)
# ------------------------------------------------------------------------------
restore_memory_from_backup() {
    local backup_dir="$1"
    if [[ -d "$backup_dir/memory" ]]; then
        warn "Restoring memory/ from backup ($backup_dir)..."
        rm -rf memory 2>/dev/null || true
        cp -a "$backup_dir/memory" ./
    fi
}

# ------------------------------------------------------------------------------
# Resolve and checkout a git ref (branch, tag, or commit)
# Call after git fetch --all --tags.
# ------------------------------------------------------------------------------
checkout_ref() {
    local ref="$1"
    info "Resolving ref: $ref..."

    # Try direct checkout first (works for local branches, commits, and
    # fetched tags when the tag name alone resolves).
    if git checkout "$ref" 2>/dev/null; then
        return 0
    fi

    # Try as a remote branch
    if git rev-parse --verify "origin/$ref" >/dev/null 2>&1; then
        info "Found remote branch origin/$ref, checking out..."
        git checkout -b "$ref" "origin/$ref" 2>/dev/null || git checkout "origin/$ref"
        return 0
    fi

    # Try as a tag (fully qualified)
    if git rev-parse --verify "refs/tags/$ref" >/dev/null 2>&1; then
        info "Found tag $ref, checking out..."
        git checkout "refs/tags/$ref"
        return 0
    fi

    error "Could not resolve ref: $ref"
    return 1
}

# ------------------------------------------------------------------------------
# Perform git operations for update:
#  - fetch everything
#  - checkout the target ref
#  - if on a tracking branch, hard-reset to remote
# ------------------------------------------------------------------------------
git_update_to_ref() {
    local ref="$1"

    info "Fetching all refs from remote..."
    if ! git fetch --all --tags --prune --force; then
        error "Failed to fetch from remote."
        return 1
    fi

    if ! checkout_ref "$ref"; then
        return 1
    fi

    # If we landed on a local branch that tracks a remote, hard-reset to
    # remote state so we get a clean engine without local drift.
    if git symbolic-ref -q HEAD >/dev/null 2>&1; then
        local current_branch
        current_branch=$(git symbolic-ref --short HEAD)
        if git rev-parse --verify "origin/$current_branch" >/dev/null 2>&1; then
            info "Hard-resetting to origin/$current_branch..."
            git reset --hard "origin/$current_branch"
        fi
    fi
}

# ------------------------------------------------------------------------------
# In-place update of an existing Memoid installation.
# Preserves memory/ folder, updates engine to GIT_REF.
# ------------------------------------------------------------------------------
do_update() {
    local install_path="$1"
    cd "$install_path"

    # ---------- validate ----------
    if [[ ! -f "scripts/memoid" ]]; then
        error "$install_path exists but does not appear to be a Memoid installation (missing scripts/memoid)."
        exit 1
    fi

    if ! git rev-parse --git-dir >/dev/null 2>&1; then
        error "$install_path exists but is not a git repository. Cannot update."
        exit 1
    fi

    # ---------- confirm ----------
    printf "\n"
    info "Directory $install_path already exists and appears to be a Memoid installation."
    info "Update will preserve your memory/ folder and update the engine to ref '$GIT_REF'."
    printf "Proceed with update? [y/N]: "
    local choice
    read -r choice < /dev/tty 2>/dev/null || true
    if [[ ! "$choice" =~ ^[Yy]$ ]]; then
        info "Update cancelled."
        exit 0
    fi

    # ---------- backup memory/ ----------
    local backup_dir
    backup_dir="$(mktemp -d /tmp/memoid-memory-backup.XXXXXX)"
    if [[ -d "memory" ]]; then
        info "Backing up memory/ to $backup_dir..."
        cp -a memory "$backup_dir/"
        success "Memory folder backed up ($(find "$backup_dir/memory" -type f | wc -l) files)."
    else
        info "No memory/ folder found; nothing to back up."
    fi

    # ---------- remove memory/ from working tree ----------
    if [[ -d "memory" ]]; then
        info "Temporarily removing memory/ from working tree to protect it during update..."
        rm -rf memory
    fi

    # ---------- git update ----------
    if ! git_update_to_ref "$GIT_REF"; then
        restore_memory_from_backup "$backup_dir"
        error "Git update failed. Original memory/ has been restored."
        rm -rf "$backup_dir"
        exit 1
    fi

    # ---------- restore memory/ ----------
    info "Restoring memory/ folder..."
    if [[ -d "$backup_dir/memory" ]]; then
        rm -rf memory 2>/dev/null || true
        cp -a "$backup_dir/memory" ./

        # ---------- verify integrity ----------
        info "Verifying memory/ integrity..."
        if diff -rq "$backup_dir/memory" "memory" >/dev/null 2>&1; then
            success "Memory folder intact and verified — no differences from backup."
            rm -rf "$backup_dir"
        else
            diff -rq "$backup_dir/memory" "memory" || true
            error "Memory folder verification FAILED!"
            error "Original backup preserved at: $backup_dir"
            error "Please inspect differences above and restore manually if needed."
            exit 1
        fi
    fi

    # ---------- dependencies & post-init ----------
    ensure_uv
    info "Syncing dependencies..."
    uv sync

    info "Running post-init checks..."
    uv run python scripts/post_init_check.py

    # ---------- CLI symlink ----------
    mkdir -p "$HOME/.local/bin"
    ln -sf "$install_path/scripts/memoid" "$HOME/.local/bin/memoid"
    info "CLI 'memoid' symlinked to ~/.local/bin/memoid"

    # ---------- version ----------
    show_version_details
    success "Memoid engine updated successfully to ref '$GIT_REF'."
}

# ------------------------------------------------------------------------------
# Fresh install: clone and set up.
# ------------------------------------------------------------------------------
do_fresh_install() {
    local install_path="$1"

    ensure_uv

    # ---------- clone ----------
    info "Cloning Memoid into $install_path..."
    git clone "$REPO_URL" "$install_path"
    cd "$install_path"

    # ---------- checkout specific ref (if not default main) ----------
    if [[ "$GIT_REF" != "main" ]]; then
        info "Checking out specified ref: $GIT_REF..."
        if ! git checkout "$GIT_REF" 2>/dev/null; then
            # For fresh clones, fetch tags explicitly and retry
            git fetch --tags --force
            if ! checkout_ref "$GIT_REF"; then
                error "Failed to check out ref '$GIT_REF' after fresh clone."
                exit 1
            fi
        fi
    fi

    # ---------- global CLI ----------
    mkdir -p "$HOME/.local/bin"
    ln -sf "$install_path/scripts/memoid" "$HOME/.local/bin/memoid"
    success "CLI 'memoid' installed to ~/.local/bin/memoid"

    info "Running CLI smoke test..."
    "$HOME/.local/bin/memoid" version >/dev/null
    success "CLI smoke test passed"

    info "Initializing Memoid memory..."
    "$HOME/.local/bin/memoid" init
    success "Memoid memory initialized"

    show_version_details
}

# ------------------------------------------------------------------------------
# MCP configuration helpers
# ------------------------------------------------------------------------------
MCP_CONFIGURED_ANY=0

run_py() {
    UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/memoid-uv-cache}" uv run python - "$@"
}

json_config_has_memoid() {
    local config_path="$1"
    run_py "$config_path" <<'PY'
import json, sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.exists():
    print("missing")
    raise SystemExit(0)
try:
    data = json.loads(path.read_text(encoding="utf-8") or "{}")
except Exception:
    print("invalid")
    raise SystemExit(0)

memoid = None
if isinstance(data.get("mcpServers"), dict):
    memoid = data["mcpServers"].get("memoid")
elif isinstance(data.get("mcp"), dict):
    memoid = data["mcp"].get("memoid")

print("present" if memoid else "absent")
PY
}

ensure_json_memoid() {
    local config_path="$1"
    local kind="$2"
    mkdir -p "$(dirname "$config_path")"
    if [[ ! -f "$config_path" ]]; then
        printf '{}\n' > "$config_path"
    fi

    run_py "$config_path" "$kind" <<'PY'
import json, sys
from pathlib import Path

path = Path(sys.argv[1])
kind = sys.argv[2]
data = json.loads(path.read_text(encoding="utf-8") or "{}")

if kind in {"claude", "gemini"}:
    data.setdefault("mcpServers", {})
    data["mcpServers"]["memoid"] = {
        "command": "memoid",
        "args": ["mcp"],
    }
elif kind == "opencode":
    data.setdefault("mcp", {})
    data["mcp"]["memoid"] = {
        "type": "local",
        "command": ["memoid", "mcp"],
        "enabled": True,
    }
else:
    raise SystemExit(f"Unsupported JSON config type: {kind}")

path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY
}

validate_json_config() {
    local config_path="$1"
    local kind="$2"
    run_py "$config_path" "$kind" <<'PY'
import json, sys
from pathlib import Path

path = Path(sys.argv[1])
kind = sys.argv[2]
data = json.loads(path.read_text(encoding="utf-8"))
if kind in {"claude", "gemini"}:
    ok = isinstance(data.get("mcpServers"), dict) and "memoid" in data["mcpServers"]
elif kind == "opencode":
    ok = isinstance(data.get("mcp"), dict) and "memoid" in data["mcp"]
else:
    ok = False
raise SystemExit(0 if ok else 1)
PY
}

toml_config_has_memoid() {
    local config_path="$1"
    run_py "$config_path" <<'PY'
import re, sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.exists():
    print("missing")
    raise SystemExit(0)
text = path.read_text(encoding="utf-8")
print("present" if re.search(r'^\[mcp_servers\.memoid\]\s*$', text, re.MULTILINE) else "absent")
PY
}

ensure_codex_memoid() {
    local config_path="$1"
    mkdir -p "$(dirname "$config_path")"
    touch "$config_path"
    if toml_config_has_memoid "$config_path" | grep -q '^present$'; then
        return
    fi
    if [[ -s "$config_path" ]]; then
        printf '\n' >> "$config_path"
    fi
    cat >> "$config_path" <<'EOF'
[mcp_servers.memoid]
command = "memoid"
args = ["mcp"]
EOF
}

validate_codex_config() {
    local config_path="$1"
    run_py "$config_path" <<'PY'
import re, sys, tomllib
from pathlib import Path

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
data = tomllib.loads(text)
ok = isinstance(data.get("mcp_servers"), dict) and "memoid" in data["mcp_servers"]
raise SystemExit(0 if ok else 1)
PY
}

detect_agent_binary() {
    local binary="$1"
    if command -v "$binary" >/dev/null 2>&1; then
        return 0
    fi
    return 1
}

config_path_for() {
    local agent="$1"
    case "$agent" in
        claude)
            if [[ "$(uname -s)" == "Darwin" ]]; then
                printf '%s\n' "$HOME/Library/Application Support/Claude/claude_desktop_config.json"
            else
                printf '%s\n' "${XDG_CONFIG_HOME:-$HOME/.config}/Claude/claude_desktop_config.json"
            fi
            ;;
        gemini)
            printf '%s\n' "$HOME/.gemini/settings.json"
            ;;
        opencode)
            if [[ "$(uname -s)" == "Darwin" && -e "$HOME/Library/Application Support/opencode/opencode.json" ]]; then
                printf '%s\n' "$HOME/Library/Application Support/opencode/opencode.json"
            else
                printf '%s\n' "${XDG_CONFIG_HOME:-$HOME/.config}/opencode/opencode.json"
            fi
            ;;
        codex)
            printf '%s\n' "$HOME/.codex/config.toml"
            ;;
    esac
}

status_for_agent() {
    local agent="$1"
    local config_path="$2"
    case "$agent" in
        claude|gemini|opencode) json_config_has_memoid "$config_path" ;;
        codex) toml_config_has_memoid "$config_path" ;;
    esac
}

backup_file() {
    local path="$1"
    local timestamp
    timestamp="$(date +%Y%m%d%H%M%S)"
    if [[ -f "$path" ]]; then
        cp "$path" "${path}.bak.${timestamp}"
        printf '%s\n' "${path}.bak.${timestamp}"
    else
        printf '%s\n' ""
    fi
}

configure_detected_mcp_clients() {
    local agents=("claude" "codex" "gemini" "opencode")
    local selectable=()

    printf '\n'
    printf "Would you like Memoid to check your installed AI agents and offer to configure MCP automatically? [Y/n]: "
    local configure_choice
    read -r configure_choice < /dev/tty 2>/dev/null || true
    if [[ "$configure_choice" =~ ^[Nn]$ ]]; then
        info "Skipping automatic MCP client configuration."
        return
    fi

    printf '\n'
    info "Checking installed AI agents and their MCP configs..."

    for agent in "${agents[@]}"; do
        local config_path status
        config_path="$(config_path_for "$agent")"
        if detect_agent_binary "$agent"; then
            status="$(status_for_agent "$agent" "$config_path")"
            case "$status" in
                present)
                    info " - $agent: installed, config ready, Memoid MCP already configured ($config_path)"
                    ;;
                absent)
                    info " - $agent: installed, config found or will be created, Memoid MCP missing ($config_path)"
                    selectable+=("$agent")
                    ;;
                missing)
                    info " - $agent: installed, config not found yet, will create if selected ($config_path)"
                    selectable+=("$agent")
                    ;;
                invalid)
                    warn " - $agent: installed, but config is invalid JSON and was skipped ($config_path)"
                    ;;
            esac
        else
            info " - $agent: not installed"
        fi
    done

    if [[ ${#selectable[@]} -eq 0 ]]; then
        info "No installed agent configs require Memoid MCP setup."
        return
    fi

    printf '\n'
    info "Select which agent configs to update with the Memoid MCP entry."
    info "Enter one or more names separated by spaces, or 'all' to update every detected config."
    printf "Selection [%s]: " "${selectable[*]}"
    local selection
    read -r selection < /dev/tty 2>/dev/null || true
    selection="${selection:-all}"

    local chosen=()
    if [[ "$selection" == "all" ]]; then
        chosen=("${selectable[@]}")
    else
        for item in $selection; do
            for candidate in "${selectable[@]}"; do
                if [[ "$item" == "$candidate" ]]; then
                    chosen+=("$candidate")
                fi
            done
        done
    fi

    if [[ ${#chosen[@]} -eq 0 ]]; then
        warn "No valid agent selections were provided. Skipping MCP config updates."
        return
    fi

    for agent in "${chosen[@]}"; do
        local config_path backup_path
        config_path="$(config_path_for "$agent")"
        backup_path="$(backup_file "$config_path")"
        [[ -n "$backup_path" ]] && info "Backed up $agent config to $backup_path"

        case "$agent" in
            claude|gemini|opencode)
                ensure_json_memoid "$config_path" "$agent"
                validate_json_config "$config_path" "$agent"
                ;;
            codex)
                ensure_codex_memoid "$config_path"
                validate_codex_config "$config_path"
                ;;
        esac
        success "Configured Memoid MCP for $agent at $config_path"
        MCP_CONFIGURED_ANY=1
    done
}

# ==============================================================================
# MAIN
# ==============================================================================

IS_UPDATE=0

# 1. Path Selection
printf "Where would you like to install Memoid? [default: $HOME/memoid]: "
read -r INSTALL_PATH < /dev/tty 2>/dev/null || true
INSTALL_PATH="${INSTALL_PATH:-$HOME/memoid}"

# 2. Branch: update existing or fresh install
if [[ -d "$INSTALL_PATH" ]]; then
    IS_UPDATE=1
    do_update "$INSTALL_PATH"
else
    do_fresh_install "$INSTALL_PATH"
fi

# 3. MCP Setup (fresh installs only — updates already have MCP configured)
if [[ "$IS_UPDATE" -eq 0 ]]; then
    configure_detected_mcp_clients
    if [[ "$MCP_CONFIGURED_ANY" -eq 0 ]]; then
        printf "\n"
        info "To set up Memoid as an MCP server for your AI agent, please refer to the instructions in the README.md"
    fi
fi

success "\nMemoid installation complete!"
info "Path: $INSTALL_PATH"
info "You can now run 'memoid gemini' or use it via MCP in your configured agents."
