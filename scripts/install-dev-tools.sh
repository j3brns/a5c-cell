#!/usr/bin/env bash
# install-dev-tools.sh — Idempotent pre-agent environment setup
#
# Called automatically by `make validate-local` and worktree provisioning.
# Safe to run repeatedly — skips anything already present.
#
# Hard requirements for `make validate-local`:
#   uv     — ruff, detect-secrets (installed to ~/.local/bin, no sudo)
#   node   — pyright, tsc, cdk synth (any version; v20 preferred)
#
# Soft requirements (warn if missing, do not abort):
#   aws CLI, glab, cfn-guard, claude
#
# In ephemeral environments (containers, Codespaces) where sudo has no
# password, system packages are installed automatically.
# On developer workstations where sudo requires a password, system-level
# installs are skipped and a warning is printed instead.

set -uo pipefail   # -u (unset vars = error), -o pipefail; NOT -e (installs may fail)

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OS_ARCH="$(uname -m)"  # x86_64 or aarch64
FAILED_TOOLS=()

info()  { printf "[install] %s\n" "$*"; }
ok()    { printf "[ok]      %s\n" "$*"; }
skip()  { printf "[skip]    %s\n" "$*"; }
warn()  { printf "[warn]    %s\n" "$*" >&2; }
fail()  { printf "[fail]    %s\n" "$*" >&2; FAILED_TOOLS+=("$1"); }

# Check once whether passwordless sudo is available
if sudo -n true 2>/dev/null; then
    CAN_SUDO=true
else
    CAN_SUDO=false
    warn "No passwordless sudo — system-level installs skipped (OK on dev workstations)"
fi

# ---------------------------------------------------------------------------
# Helper: apt install a list of packages (no-op if sudo unavailable)
# ---------------------------------------------------------------------------
apt_install() {
    if ! $CAN_SUDO; then return 0; fi
    sudo apt-get update -qq 2>/dev/null
    sudo apt-get install -y -qq "$@" 2>/dev/null || warn "apt install $* failed"
}

# ---------------------------------------------------------------------------
# 1. uv — installs to ~/.local/bin, no sudo needed
# ---------------------------------------------------------------------------
export PATH="$HOME/.local/bin:$PATH"

if command -v uv &>/dev/null; then
    skip "uv $(uv --version)"
else
    info "Installing uv..."
    if curl -Ls https://astral.sh/uv/install.sh | sh; then
        ok "uv $(uv --version)"
    else
        fail "uv"
        warn "uv is required for validate-local — install manually: curl -Ls https://astral.sh/uv/install.sh | sh"
    fi
fi

# ---------------------------------------------------------------------------
# 2. Node.js (any version acceptable; v20 preferred)
# ---------------------------------------------------------------------------
if command -v node &>/dev/null; then
    NODE_VER="$(node --version)"
    if [[ "$NODE_VER" == v20* ]]; then
        skip "node $NODE_VER"
    else
        skip "node $NODE_VER (v20 preferred but not required)"
    fi
elif $CAN_SUDO; then
    info "Installing Node.js 20 LTS..."
    apt_install ca-certificates gnupg curl
    if curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - 2>/dev/null \
        && sudo apt-get install -y -qq nodejs; then
        ok "node $(node --version)"
    else
        fail "node"
    fi
else
    # nvm as a no-sudo fallback
    info "Installing Node.js via nvm (no-sudo fallback)..."
    NVM_DIR="$HOME/.nvm"
    if [[ ! -s "$NVM_DIR/nvm.sh" ]]; then
        curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash 2>/dev/null || true
    fi
    # shellcheck source=/dev/null
    [ -s "$NVM_DIR/nvm.sh" ] && source "$NVM_DIR/nvm.sh" || true
    if command -v nvm &>/dev/null; then
        nvm install 20 --silent && nvm use 20 --silent
        ok "node $(node --version) via nvm"
    else
        fail "node"
        warn "node is required for validate-local — install Node.js 20 manually"
    fi
fi

# ---------------------------------------------------------------------------
# 3. Declarative Tool Installation (aws, glab, cfn-guard, claude)
# ---------------------------------------------------------------------------
info "Installing tools from declarative manifest..."
if uv run python scripts/install_tools.py; then
    ok "Declarative tools ready"
else
    warn "Some declarative tools failed to install"
fi

# ---------------------------------------------------------------------------
# 4. Project deps — HARD REQUIREMENTS (validate-local fails without these)
# ---------------------------------------------------------------------------
cd "$REPO_ROOT"

info "Syncing Python project dependencies (uv sync)..."
if uv sync --quiet; then
    ok "Python deps ready"
else
    echo "[error]   uv sync failed — validate-local will not pass" >&2
    exit 1
fi

info "Syncing CDK Node dependencies (infra/cdk)..."
if npm install --prefix infra/cdk --quiet 2>/dev/null; then
    ok "infra/cdk deps ready"
else
    echo "[error]   npm install in infra/cdk failed — tsc/cdk synth will not pass" >&2
    exit 1
fi

info "Syncing SPA Node dependencies (spa)..."
npm install --prefix spa --quiet 2>/dev/null || warn "spa npm install failed (not needed for validate-local)"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
if [[ ${#FAILED_TOOLS[@]} -gt 0 ]]; then
    warn "Some tools failed to install: ${FAILED_TOOLS[*]}"
    warn "validate-local will still run — failed tools are only needed for deploy/ops targets"
fi
ok "Environment ready."
