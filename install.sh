#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# d2l-brightspace installer: everything from nothing to "ask your AI what's due".
#
#   curl -fsSL https://raw.githubusercontent.com/boss2236/d2l-brightspace/main/install.sh | bash
#
# Steps, each safe to re-run (a second run updates instead of reinstalling):
#   prerequisites  git, curl, tar (offers to install missing ones with your package manager)
#   uv             a pinned, sha256-checked uv just for this app; never touches one you already have
#   download       clone the repo; on re-runs fast-forward, parking any local changes first
#   python         the exact locked dependencies (uv also fetches Python 3.11+ if the system has none)
#   browser        Chromium for signing in and syncing
#   school         your Brightspace address, checked before it's saved
#   ports          two random free ports for the app and the AI connection, saved in .env and shown to you
#   command        `d2l` on your PATH
#   sign-in        a browser window opens once; you log in as usual (SSO + MFA); no password is ever seen
#   first sync     downloads this term's courses
#   AI apps        pick from Claude Code, Codex, Hermes, Gemini CLI, VS Code, Claude Desktop
#   auto-update    sync 3× a day + the Brightspace app running in the background
#   address        http://d2l.localhost opens the app (a tiny forwarder on port 80; asks for sudo once on Linux)
#
# Everything it changes is recorded, and `--uninstall` undoes it:
#   bash ~/.local/share/d2l-brightspace/app/install.sh --uninstall
#
# Options: --yes (accept every default) --school URL --apps a,b|all|none --no-login --no-sync --no-schedule
#          --dir PATH --branch NAME --verbose --uninstall --purge --help
set -u

# uv must not pick up a uv.toml / pyproject.toml from wherever the installer happens to be run.
export UV_NO_CONFIG=1

REPO_URL="${D2L_REPO_URL:-https://github.com/boss2236/d2l-brightspace.git}"
RAW_URL="https://raw.githubusercontent.com/boss2236/d2l-brightspace/main/install.sh"
BRANCH="main"
D2L_HOME="${D2L_HOME:-$HOME/.local/share/d2l-brightspace}"
INSTALL_DIR=""
SCHOOL=""
APPS=""
ASSUME_YES=false
DO_LOGIN=true
DO_SYNC=true
DO_SCHEDULE=true
VERBOSE=false
UNINSTALL=false
PURGE=false

usage() {
    cat <<EOF
Usage: install.sh [options]

  --yes            accept every default (sign in, sync, connect every installed AI app, auto-update)
  --school URL     your Brightspace address, e.g. https://school.brightspace.com
  --apps LIST      AI apps to connect: comma-separated keys, 'all' or 'none'
                   (claude-code, codex, hermes, gemini, vscode, claude-desktop)
  --no-login       skip signing in (run 'd2l login' later)
  --no-sync        skip the first sync
  --no-schedule    don't set up automatic syncs and the background app
  --dir PATH       install somewhere else (default: $D2L_HOME/app)
  --branch NAME    install another branch (default: main)
  --verbose        show every command's full output
  --uninstall      undo everything a previous install did (asks before deleting your data)
  --purge          uninstall and also delete your synced data and saved sign-in

Environment: D2L_HOME (default ~/.local/share/d2l-brightspace), D2L_REPO_URL, NO_COLOR
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --school|--apps|--dir|--branch)
            if [ $# -lt 2 ] || [ -z "$2" ] || [ "${2#-}" != "$2" ]; then
                printf '%s needs a value\n' "$1" >&2; exit 2
            fi
            case "$1" in
                --school) SCHOOL="$2" ;;
                --apps) APPS="$2" ;;
                --dir) INSTALL_DIR="$2" ;;
                --branch) BRANCH="$2" ;;
            esac
            shift 2 ;;
        --yes|-y) ASSUME_YES=true; shift ;;
        --no-login) DO_LOGIN=false; shift ;;
        --no-sync) DO_SYNC=false; shift ;;
        --no-schedule) DO_SCHEDULE=false; shift ;;
        --verbose) VERBOSE=true; shift ;;
        --uninstall) UNINSTALL=true; shift ;;
        --purge) UNINSTALL=true; PURGE=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'unknown option: %s (try --help)\n' "$1" >&2; exit 2 ;;
    esac
done

INSTALL_DIR="${INSTALL_DIR:-$D2L_HOME/app}"
STATE="$D2L_HOME/install-state"
LOG="$D2L_HOME/logs/install.log"
BIN_DIR="$HOME/.local/bin"
PY="$INSTALL_DIR/.venv/bin/python"
D2L=("$PY" -m d2l)

# --- look ---------------------------------------------------------------------------------------------------------

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    C_RED=$'\033[0;31m' C_GREEN=$'\033[0;32m' C_YELLOW=$'\033[0;33m' C_BLUE=$'\033[0;34m'
    C_CYAN=$'\033[0;36m' C_BOLD=$'\033[1m' C_DIM=$'\033[2m' C_NC=$'\033[0m'
else
    C_RED="" C_GREEN="" C_YELLOW="" C_BLUE="" C_CYAN="" C_BOLD="" C_DIM="" C_NC=""
fi

log()      { printf '%s→%s %s\n' "$C_CYAN" "$C_NC" "$1"; }
ok()       { printf '%s✓%s %s\n' "$C_GREEN" "$C_NC" "$1"; }
warn()     { printf '%s⚠%s %s\n' "$C_YELLOW" "$C_NC" "$1"; }
err()      { printf '%s✗%s %s\n' "$C_RED" "$C_NC" "$1" >&2; }
fail()     { err "$1"; [ -f "$LOG" ] && printf '  full log: %s\n' "$LOG" >&2; exit 1; }
hint()     { printf '  %s%s%s\n' "$C_DIM" "$1" "$C_NC"; }
tld()      { printf '%s' "${1/#$HOME/\~}"; }      # paths as people read them: ~/…

STEP=0
STEPS=13
step() {
    STEP=$((STEP + 1))
    printf '\n%s%s[%d/%d]%s %s%s%s\n' "$C_BLUE" "$C_BOLD" "$STEP" "$STEPS" "$C_NC" "$C_BOLD" "$1" "$C_NC"
}

banner() {
    printf '\n%s%s' "$C_BLUE" "$C_BOLD"
    printf '%s\n' "  ┌──────────────────────────────────────────────────────────┐"
    printf '%s\n' "  │                 📚  Brightspace for your AI               │"
    printf '%s\n' "  ├──────────────────────────────────────────────────────────┤"
    printf '%s\n' "  │  Your courses, grades, deadlines and lecture files,      │"
    printf '%s\n' "  │  synced locally and readable by Claude, Codex, Hermes…   │"
    printf '%s\n' "  └──────────────────────────────────────────────────────────┘"
    printf '%s' "$C_NC"
}

# --- terminal input -----------------------------------------------------------------------------------------------
# Under `curl | bash`, stdin IS this script, so questions are read from /dev/tty. Opening it is the real test: a
# container can have the device node and still fail to open it.

has_tty() { (: </dev/tty) 2>/dev/null; }
interactive() { [ "$ASSUME_YES" = false ] && has_tty; }

# ask_yn QUESTION DEFAULT(y|n) -> status 0 for yes
ask_yn() {
    local q="$1" def="$2" ans prompt
    if ! interactive; then [ "$def" = y ]; return; fi
    if [ "$def" = y ]; then prompt="[Y/n]"; else prompt="[y/N]"; fi
    while true; do
        printf '%s?%s %s %s%s%s ' "$C_YELLOW" "$C_NC" "$q" "$C_DIM" "$prompt" "$C_NC" >/dev/tty
        IFS= read -r ans </dev/tty || ans=""
        case "$ans" in
            "") [ "$def" = y ]; return ;;
            y|Y|yes|YES|Yes) return 0 ;;
            n|N|no|NO|No) return 1 ;;
        esac
    done
}

# ask_text QUESTION DEFAULT -> answer on stdout
ask_text() {
    local q="$1" def="$2" ans
    if ! interactive; then printf '%s' "$def"; return; fi
    if [ -n "$def" ]; then
        printf '%s?%s %s %s(%s)%s ' "$C_YELLOW" "$C_NC" "$q" "$C_DIM" "$def" "$C_NC" >/dev/tty
    else
        printf '%s?%s %s ' "$C_YELLOW" "$C_NC" "$q" >/dev/tty
    fi
    IFS= read -r ans </dev/tty || ans=""
    printf '%s' "${ans:-$def}"
}

# Checkbox list on the terminal: ↑/↓ (or j/k) move, space toggles, a = all/none, enter confirms.
# In: MENU_LABELS[] and MENU_ON[] (1/0). Out: MENU_ON[] updated.
MENU_LABELS=()
MENU_ON=()
menu_cleanup() { printf '\033[?25h' >/dev/tty 2>/dev/null || :; stty echo icanon </dev/tty 2>/dev/null || :; }
menu_draw() {
    local i box mark
    for ((i = 0; i < ${#MENU_LABELS[@]}; i++)); do
        if [ "${MENU_ON[$i]}" = 1 ]; then box="${C_GREEN}[✓]${C_NC}"; else box="[ ]"; fi
        if [ "$i" = "$MENU_CUR" ]; then mark="${C_CYAN}❯${C_NC}"; else mark=" "; fi
        printf '\r\033[K  %s %s %s\n' "$mark" "$box" "${MENU_LABELS[$i]}" >/dev/tty
    done
}
menu_pick() {
    local n=${#MENU_LABELS[@]} key rest i all
    MENU_CUR=0
    printf '  %s↑/↓ move · space select · a all/none · enter confirm%s\n' "$C_DIM" "$C_NC" >/dev/tty
    trap 'menu_cleanup; exit 130' INT TERM
    printf '\033[?25l' >/dev/tty
    menu_draw
    while true; do
        IFS= read -rsn1 key </dev/tty || break
        if [ "$key" = $'\033' ]; then
            IFS= read -rsn2 -t 1 rest </dev/tty || rest=""
            case "$rest" in
                "[A") key=k ;;
                "[B") key=j ;;
                *) key="" ;;
            esac
            [ -n "$key" ] || continue
        fi
        case "$key" in
            k) MENU_CUR=$(( (MENU_CUR - 1 + n) % n )) ;;
            j) MENU_CUR=$(( (MENU_CUR + 1) % n )) ;;
            " ") if [ "${MENU_ON[$MENU_CUR]}" = 1 ]; then MENU_ON[$MENU_CUR]=0; else MENU_ON[$MENU_CUR]=1; fi ;;
            a|A)
                all=1
                for ((i = 0; i < n; i++)); do [ "${MENU_ON[$i]}" = 1 ] || all=0; done
                for ((i = 0; i < n; i++)); do MENU_ON[$i]=$(( 1 - all )); done ;;
            "") break ;;
        esac
        printf '\033[%dA' "$n" >/dev/tty
        menu_draw
    done
    menu_cleanup
    trap - INT TERM
}

# --- running commands quietly -------------------------------------------------------------------------------------
# On a terminal, a command's output is folded into one live status line and saved in the log; on failure the last
# lines are printed. With --verbose, in CI, or with no terminal, output streams as-is.

quiet() {
    [ "$VERBOSE" = true ] && return 1
    [ -z "${CI:-}" ] || return 1
    [ -t 1 ]
}

status_line() {
    local text="  $1" width=$(( $2 - 1 ))
    [ "${#text}" -le "$width" ] || text="${text:0:$width}"
    printf '\r\033[K%s%s%s' "$C_DIM" "$text" "$C_NC"
}

# run LABEL CMD... -> CMD's exit status. `run --may-fail` skips the failure report (the caller handles it).
run() {
    local may_fail=false
    if [ "$1" = --may-fail ]; then may_fail=true; shift; fi
    local label="$1"; shift
    mkdir -p "${LOG%/*}" 2>/dev/null
    printf '==> %s (%s)\n' "$label" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$LOG" 2>/dev/null
    if ! quiet; then
        log "$label"
        "$@" </dev/null 2>&1 | tee -a "$LOG"
        return "${PIPESTATUS[0]}"
    fi
    local start cols rc line shown
    start=$(( $(wc -l < "$LOG") + 1 ))
    cols="$(tput cols 2>/dev/null)" || cols=80
    [ "${cols:-0}" -gt 20 ] 2>/dev/null || cols=80
    status_line "$label" "$cols"
    "$@" </dev/null 2>&1 | {
        while IFS= read -r line || [ -n "$line" ]; do
            line="${line%$'\r'}"
            printf '%s\n' "$line" >&3
            shown="${line##*$'\r'}"
            [ -z "$shown" ] || status_line "$label: $shown" "$cols"
        done
    } 3>>"$LOG"
    rc=${PIPESTATUS[0]}
    printf '\r\033[K'
    if [ "$rc" -ne 0 ] && [ "$may_fail" = false ]; then
        err "$label failed (exit $rc). Last output:"
        tail -n +"$start" "$LOG" | tail -n 20 | sed 's/^/    /' >&2
    fi
    return "$rc"
}

# --- install record -----------------------------------------------------------------------------------------------
# One "key value" per line; --uninstall reads it back.

# Keys may hold paths, so they're matched as plain strings, never as patterns.
record() {
    mkdir -p "$D2L_HOME"
    { [ -f "$STATE" ] && awk -v k="$1 " 'index($0, k) != 1' "$STATE"; printf '%s %s\n' "$1" "$2"; } > "$STATE.tmp"
    mv -f "$STATE.tmp" "$STATE"
}
recorded() { [ -f "$STATE" ] && awk -v k="$1 " 'index($0, k) == 1 { v = substr($0, length(k) + 1) } END { print v }' "$STATE"; }

# --- 1. prerequisites ---------------------------------------------------------------------------------------------

OS="$(uname -s 2>/dev/null)"
pkg_install_cmd() {
    # The command that installs packages "$@" on this system, or nothing when there's no known manager.
    if [ "$OS" = Darwin ]; then
        command -v brew >/dev/null 2>&1 && printf 'brew install %s' "$*"
        return
    fi
    local sudo=""
    [ "$(id -u)" -eq 0 ] || sudo="sudo "
    if command -v apt-get >/dev/null 2>&1; then printf '%sapt-get install -y %s' "$sudo" "$*"
    elif command -v dnf >/dev/null 2>&1; then printf '%sdnf install -y %s' "$sudo" "$*"
    elif command -v pacman >/dev/null 2>&1; then printf '%spacman -S --needed --noconfirm %s' "$sudo" "$*"
    elif command -v zypper >/dev/null 2>&1; then printf '%szypper install -y %s' "$sudo" "$*"
    elif command -v apk >/dev/null 2>&1; then printf '%sapk add %s' "$sudo" "$*"
    fi
}

stage_prerequisites() {
    step "Checking this computer"
    case "$OS" in
        Linux|Darwin) : ;;
        MINGW*|MSYS*|CYGWIN*) fail "Windows: run this inside WSL (wsl --install), or follow the manual setup in the README." ;;
        *) fail "unsupported system: $OS" ;;
    esac
    case "$(uname -m)" in
        x86_64|amd64|arm64|aarch64) : ;;
        *) fail "unsupported processor: $(uname -m) (x86_64 and arm64 are supported)" ;;
    esac
    local missing=() c
    for c in git curl tar; do command -v "$c" >/dev/null 2>&1 || missing+=("$c"); done
    if [ ${#missing[@]} -gt 0 ]; then
        local cmd
        cmd="$(pkg_install_cmd "${missing[@]}")"
        if [ "$OS" = Darwin ] && [[ " ${missing[*]} " == *" git "* ]] && [ -z "$cmd" ]; then
            fail "git is missing. Run: xcode-select --install  (then run this installer again)"
        fi
        [ -n "$cmd" ] || fail "missing: ${missing[*]}. Install them with your package manager and run this again."
        warn "missing: ${missing[*]}"
        if ask_yn "Install them now with: $cmd" y && has_tty; then
            # shellcheck disable=SC2086
            $cmd </dev/tty || fail "could not install ${missing[*]}"
        else
            fail "missing: ${missing[*]}. Install with: $cmd"
        fi
    fi
    ok "$OS $(uname -m) · git, curl, tar"
}

# --- 2. uv --------------------------------------------------------------------------------------------------------
# Same idea as the Hermes installer: a pinned uv build, checked against Astral's published sha256, kept in this
# app's own folder. A uv you already have is left alone, and a newer or broken one can't change this install.

UV_VERSION="0.12.3"
uv_target() {
    local arch libc=""
    case "$(uname -m)" in
        arm64|aarch64) arch="aarch64" ;;
        *) arch="x86_64" ;;
    esac
    if [ "$OS" = Darwin ]; then echo "$arch-apple-darwin"; return; fi
    # musl (Alpine, Void) vs glibc: ask the system's own shell binary which loader it uses.
    case "$(head -c 4096 /bin/sh 2>/dev/null | LC_ALL=C tr -d '\000')" in
        *ld-musl-*) libc="musl" ;;
    esac
    [ -n "$libc" ] || { ldd --version 2>&1 | grep -qi musl && libc="musl"; }
    echo "$arch-unknown-linux-${libc:-gnu}"
}
uv_sha256() {
    case "$1" in
        x86_64-unknown-linux-gnu)   echo 600cf9a742aca00d292673b16b5acffaa7b8c269a364ad0c2e79498dcb1fe101 ;;
        aarch64-unknown-linux-gnu)  echo bb66cb52e7b1823aed1183630d8d8e5c958840d584a4c55ec10a4cfc168dcca2 ;;
        x86_64-unknown-linux-musl)  echo 0643b9fb8c9fb27458e709ce6ff939695013c41975ff7b02d3f3b138d8d4bdb3 ;;
        aarch64-unknown-linux-musl) echo fa513fca1eb2913334c944fe9adbdd410274a1cbe8dd05d03699a9eb85311d4e ;;
        x86_64-apple-darwin)        echo 4c9f52262a14da336e4a42ed24992d12d0c956acde87619e4611d321dffa602b ;;
        aarch64-apple-darwin)       echo 546f7f8a6c70ff13a3a9d2bc958db3427298cebf3e0cb756f9177133b7068843 ;;
    esac
}
sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
    else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

UV=""
stage_uv() {
    step "Getting uv (Python installer)"
    local target want dir tmp attempt got=false
    target="$(uv_target)"
    want="$(uv_sha256 "$target")"
    [ -n "$want" ] || fail "no uv build for $target"
    dir="$D2L_HOME/tools/uv-$UV_VERSION-$target"
    UV="$dir/uv"
    if [ -x "$UV" ] && "$UV" --version >/dev/null 2>&1; then
        ok "uv $UV_VERSION (already here)"
        return
    fi
    tmp="$(mktemp -d)" || fail "cannot create a temporary folder"
    local url="https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-$target.tar.gz"
    for attempt in 1 2 3; do
        if run --may-fail "Downloading uv $UV_VERSION ($target)" curl -fL --retry 2 --connect-timeout 20 -o "$tmp/uv.tar.gz" "$url"; then
            got=true; break
        fi
        [ "$attempt" = 3 ] || sleep $((attempt * 3))
    done
    [ "$got" = true ] || { rm -rf "$tmp"; fail "could not download uv from $url (check your internet connection)"; }
    local digest
    digest="$(sha256_of "$tmp/uv.tar.gz")"
    if [ "$digest" != "$want" ]; then
        rm -rf "$tmp"
        fail "uv download didn't match its checksum (expected $want, got $digest); not using it"
    fi
    tar -xzf "$tmp/uv.tar.gz" -C "$tmp" || { rm -rf "$tmp"; fail "could not unpack uv"; }
    mkdir -p "$dir"
    mv -f "$(find "$tmp" -name uv -type f | head -n 1)" "$UV" || { rm -rf "$tmp"; fail "uv binary missing from the download"; }
    chmod +x "$UV"
    rm -rf "$tmp"
    "$UV" --version >/dev/null 2>&1 || fail "uv was downloaded but doesn't run on this computer"
    ok "uv $UV_VERSION (checksum verified)"
}

# --- 3. download --------------------------------------------------------------------------------------------------

stage_download() {
    step "Downloading d2l-brightspace"
    if [ -d "$INSTALL_DIR/.git" ] && ! git -C "$INSTALL_DIR" rev-parse --verify HEAD >/dev/null 2>&1; then
        # An interrupted clone: move it aside rather than delete it.
        local broken
        broken="$INSTALL_DIR.broken-$(date +%Y%m%d-%H%M%S)"
        warn "$INSTALL_DIR is an unfinished download; moving it to $broken"
        mv "$INSTALL_DIR" "$broken" || fail "cannot move $INSTALL_DIR aside"
    fi
    if [ -d "$INSTALL_DIR/.git" ]; then
        run "Fetching updates" git -C "$INSTALL_DIR" fetch origin "+refs/heads/$BRANCH:refs/remotes/origin/$BRANCH" \
            || fail "could not fetch updates"
        local stamp
        stamp="$(date +%Y%m%d-%H%M%S)"
        if [ -n "$(git -C "$INSTALL_DIR" status --porcelain --untracked-files=no)" ]; then
            # Your own edits are parked, never overwritten. Data, session and .env are gitignored and never touched.
            run "Saving your local changes" git -C "$INSTALL_DIR" stash push -m "d2l-install-autostash-$stamp" \
                || fail "could not save local changes in $INSTALL_DIR; commit or move them, then run this again"
            warn "your local code changes were saved: git -C \"$INSTALL_DIR\" stash list"
        fi
        if git -C "$INSTALL_DIR" show-ref --verify --quiet "refs/heads/$BRANCH"; then
            run "Switching to $BRANCH" git -C "$INSTALL_DIR" checkout "$BRANCH" || fail "git checkout failed"
        else
            run "Switching to $BRANCH" git -C "$INSTALL_DIR" checkout -b "$BRANCH" "origin/$BRANCH" || fail "git checkout failed"
        fi
        if ! run --may-fail "Updating to the latest version" git -C "$INSTALL_DIR" merge --ff-only "origin/$BRANCH"; then
            local ahead
            ahead="$(git -C "$INSTALL_DIR" rev-list --count "origin/$BRANCH..HEAD" 2>/dev/null || echo 0)"
            if [ "$ahead" -gt 0 ]; then
                local ref="refs/d2l-backups/$BRANCH-$stamp"
                git -C "$INSTALL_DIR" update-ref "$ref" HEAD || fail "cannot back up your commits; not updating"
                warn "$ahead commit(s) of yours saved as $ref"
            fi
            run "Resetting to origin/$BRANCH" git -C "$INSTALL_DIR" reset --hard "origin/$BRANCH" || fail "git reset failed"
        fi
        ok "updated to $(git -C "$INSTALL_DIR" rev-parse --short HEAD)"
        return
    fi
    if [ -e "$INSTALL_DIR" ]; then
        if [ -d "$INSTALL_DIR" ] && [ -z "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ]; then
            rmdir "$INSTALL_DIR"
        else
            fail "$INSTALL_DIR already exists and isn't a d2l-brightspace download. Move it, or use --dir another/path."
        fi
    fi
    mkdir -p "$(dirname "$INSTALL_DIR")"
    # Clone into a temporary folder next to the target and move it in only when complete, so an interrupted
    # download never leaves a half-finished install behind.
    local staged attempt cloned=false
    staged="$(mktemp -d "$(dirname "$INSTALL_DIR")/.d2l-clone-XXXXXX")" || fail "cannot create a temporary folder"
    for attempt in 1 2 3; do
        if run --may-fail "Cloning $REPO_URL" git clone --depth 50 --branch "$BRANCH" "$REPO_URL" "$staged/tree"; then
            cloned=true; break
        fi
        rm -rf "$staged/tree"
        [ "$attempt" = 3 ] || sleep $((attempt * 5))
    done
    if [ "$cloned" = false ]; then
        rm -rf "$staged"
        fail "could not download from $REPO_URL (check your internet connection)"
    fi
    mv "$staged/tree" "$INSTALL_DIR" || { rm -rf "$staged"; fail "cannot move the download into $INSTALL_DIR"; }
    rmdir "$staged" 2>/dev/null
    record dir "$INSTALL_DIR"
    ok "downloaded to $(tld "$INSTALL_DIR")"
}

# --- 4. python ----------------------------------------------------------------------------------------------------

stage_python() {
    step "Installing Python and dependencies"
    (cd "$INSTALL_DIR" && run "Installing locked dependencies" "$UV" sync --frozen --no-dev --python-preference managed) \
        || fail "could not install the Python dependencies"
    "$PY" -c "import d2l" 2>/dev/null || fail "the install finished but Python can't load it"
    ok "$("$PY" --version 2>&1) with every dependency at its locked version"
}

# --- 5. browser ---------------------------------------------------------------------------------------------------

chromium_works() {
    "$PY" - <<'EOF' >/dev/null 2>&1
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    b.new_page().set_content("<p>ok</p>")
    b.close()
EOF
}

stage_browser() {
    step "Installing the browser used for sign-in and sync"
    run "Downloading Chromium" "$PY" -m playwright install chromium || fail "could not download Chromium"
    if chromium_works; then
        ok "Chromium ready"
        return
    fi
    warn "Chromium is downloaded but won't start; some system libraries are missing"
    if [ "$OS" = Linux ] && command -v apt-get >/dev/null 2>&1; then
        if ask_yn "Install them now? (runs: sudo playwright install-deps chromium)" y && has_tty; then
            sudo "$PY" -m playwright install-deps chromium </dev/tty || warn "installing the libraries failed"
        fi
    else
        hint "Install your distribution's Chromium/Chrome package; it brings the libraries Chromium needs."
    fi
    if chromium_works; then ok "Chromium ready"; else
        warn "Chromium still won't start. Sync and sign-in need it; see $LOG"
        hint "after fixing: $PY -m playwright install chromium"
    fi
}

# --- 6. school ----------------------------------------------------------------------------------------------------

normalize_url() {
    local u="$1"
    u="${u#"${u%%[![:space:]]*}"}"; u="${u%"${u##*[![:space:]]}"}"
    case "$u" in http://*|https://*) : ;; "") return ;; *) u="https://$u" ;; esac
    # keep scheme://host[:port] only
    u="$(printf '%s' "$u" | sed -E 's#^(https?://[^/?#]+).*#\1#')"
    printf '%s' "$u"
}

looks_like_brightspace() {
    local page
    page="$(curl -fsSL --max-time 20 "$1/d2l/login" 2>/dev/null)" || return 1
    printf '%s' "$page" | grep -qiE 'd2l|brightspace'
}

env_get() { [ -f "$INSTALL_DIR/.env" ] && sed -n "s/^$1=//p" "$INSTALL_DIR/.env" | tail -n 1; }
env_set() {
    local f="$INSTALL_DIR/.env" tmp="$INSTALL_DIR/.env.tmp"
    if grep -q "^$1=" "$f" 2>/dev/null; then
        awk -v k="$1" -v v="$2" 'BEGIN{FS=OFS="="} $1==k{print k "=" v; next} {print}' "$f" > "$tmp" && mv -f "$tmp" "$f"
    else
        printf '%s=%s\n' "$1" "$2" >> "$f"
    fi
    chmod 600 "$f"
}

stage_school() {
    step "Your school"
    if [ ! -f "$INSTALL_DIR/.env" ]; then
        cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env" 2>/dev/null || : > "$INSTALL_DIR/.env"
    fi
    chmod 600 "$INSTALL_DIR/.env"
    local current url
    current="$(env_get D2L_BASE_URL)"
    case "$current" in *your-school*) current="" ;; esac
    url="$(normalize_url "${SCHOOL:-}")"
    if [ -z "$url" ] && [ -n "$current" ]; then
        if ask_yn "Keep $current?" y; then url="$current"; fi
    fi
    while [ -z "$url" ]; do
        if ! interactive; then
            fail "no school address. Run again with --school https://your-school.brightspace.com"
        fi
        hint "The address you see in the browser when you're on Brightspace, e.g. d2l.myschool.edu"
        url="$(normalize_url "$(ask_text "Brightspace address:" "")")"
    done
    if run --may-fail "Checking $url" looks_like_brightspace "$url"; then
        ok "$url answers like a Brightspace site"
    else
        warn "$url didn't answer like a Brightspace login page"
        if interactive && ! ask_yn "Use it anyway?" n; then
            SCHOOL=""
            stage_school_retry
            return
        fi
    fi
    env_set D2L_BASE_URL "$url"
    ok "saved to $(tld "$INSTALL_DIR")/.env"
}
stage_school_retry() { STEP=$((STEP - 1)); stage_school; }

# --- ports --------------------------------------------------------------------------------------------------------

APP_PORT="" MCP_PORT=""
stage_ports() {
    step "Choosing ports"
    local out
    out="$(cd "$INSTALL_DIR" && "${D2L[@]}" ports --assign 2>&1)" || { printf '%s\n' "$out" >> "$LOG"; fail "could not choose ports: $out"; }
    printf '%s\n' "$out" >> "$LOG"
    APP_PORT="$(env_get D2L_UI_PORT)"; MCP_PORT="$(env_get D2L_MCP_PORT)"
    ok "app on port ${C_BOLD}$APP_PORT${C_NC} · AI connection (MCP + REST) on port ${C_BOLD}$MCP_PORT${C_NC}"
    hint "both were free; saved in $(tld "$INSTALL_DIR")/.env. If another program takes one later, the app moves to a new"
    hint "free port by itself and everything follows. 'd2l ports' shows them any time."
}

# --- 7. command ---------------------------------------------------------------------------------------------------

RC_BEGIN="# >>> d2l-brightspace >>>"
RC_END="# <<< d2l-brightspace <<<"

add_rc_block() {
    local rc="$1" line="$2"
    if [ -f "$rc" ] && grep -qF "$RC_BEGIN" "$rc"; then return; fi
    local how=edited
    [ -e "$rc" ] || how=created                     # uninstall deletes a file only if this installer made it
    mkdir -p "$(dirname "$rc")"
    printf '\n%s\n%s\n%s\n' "$RC_BEGIN" "$line" "$RC_END" >> "$rc" || return
    record "rc:$rc" "$how"
    ok "added ~/.local/bin to PATH in $(tld "$rc")"
}

stage_command() {
    step "Adding the d2l command"
    mkdir -p "$BIN_DIR"
    local target="$INSTALL_DIR/.venv/bin/d2l" link="$BIN_DIR/d2l"
    if [ -L "$link" ] && [ "$(readlink "$link")" = "$target" ]; then
        :
    elif [ -e "$link" ] || [ -L "$link" ]; then
        warn "$link already exists and isn't from this installer; leaving it alone"
        hint "use $target directly"
        return
    else
        ln -s "$target" "$link" || fail "cannot create $link"
        record launcher "$link"
    fi
    case ":$PATH:" in
        *":$BIN_DIR:"*) : ;;
        *)
            local path_line='case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) export PATH="$HOME/.local/bin:$PATH" ;; esac'
            case "${SHELL##*/}" in
                zsh) add_rc_block "$HOME/.zshrc" "$path_line" ;;
                fish) add_rc_block "$HOME/.config/fish/config.fish" 'fish_add_path "$HOME/.local/bin"' ;;
                *) add_rc_block "$HOME/.bashrc" "$path_line"; add_rc_block "$HOME/.profile" "$path_line" ;;
            esac
            NEEDS_RELOAD=true ;;
    esac
    ok "d2l → $(tld "$target")"
}
NEEDS_RELOAD=false

# --- 8. sign in ---------------------------------------------------------------------------------------------------

has_display() {
    [ "$OS" = Darwin ] && return 0
    [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]
}
signed_in() { [ -s "$INSTALL_DIR/session/state.json" ]; }

stage_login() {
    step "Signing in to Brightspace"
    if [ "$DO_LOGIN" = false ]; then hint "skipped (--no-login); later: d2l login"; return; fi
    if signed_in && ! { interactive && ask_yn "You're already signed in. Sign in again?" n; }; then
        ok "already signed in"
        return
    fi
    if ! has_tty; then warn "no terminal to sign in from; later run: d2l login"; return; fi
    if ! has_display; then
        warn "no screen here (SSH?). Sign in from the computer's own desktop with: d2l login"
        return
    fi
    if ! ask_yn "Open a browser window to sign in now? (your normal school login, incl. MFA)" y; then
        hint "later: d2l login"
        return
    fi
    hint "A Chromium window opens. Sign in as usual; it closes by itself once you reach your Brightspace home page."
    if (cd "$INSTALL_DIR" && "${D2L[@]}" login </dev/tty); then
        ok "signed in; the session is saved on this computer only"
    else
        warn "sign-in didn't finish; try again any time with: d2l login"
    fi
}

# --- 9. first sync ------------------------------------------------------------------------------------------------

stage_sync() {
    step "Downloading your courses"
    if [ "$DO_SYNC" = false ]; then hint "skipped (--no-sync); later: d2l sync"; return; fi
    if ! signed_in; then hint "skipped: not signed in yet. After 'd2l login', run: d2l sync"; return; fi
    if ! ask_yn "Download this term's courses now? (a few minutes the first time)" y; then
        hint "later: d2l sync"; return
    fi
    if (cd "$INSTALL_DIR" && run "Syncing (courses, grades, announcements, files)" "${D2L[@]}" sync); then
        ok "courses downloaded"
    else
        warn "the first sync didn't finish; run 'd2l sync' to see why"
    fi
}

# --- 10. AI apps --------------------------------------------------------------------------------------------------

stage_apps() {
    step "Connecting your AI apps"
    local keys=() names=() inst=() conn=() mine=() k n i c m
    while IFS=$'\t' read -r k n i c m; do
        [ -n "$k" ] || continue
        keys+=("$k"); names+=("$n"); inst+=("$i"); conn+=("$c"); mine+=("${m:-0}")
    done < <(cd "$INSTALL_DIR" && "${D2L[@]}" connect --list 2>/dev/null)
    [ ${#keys[@]} -gt 0 ] || { warn "couldn't list AI apps; later: d2l connect"; return; }

    local chosen=() idx
    if [ -n "$APPS" ]; then
        case "$APPS" in
            none) : ;;
            all) for ((idx = 0; idx < ${#keys[@]}; idx++)); do [ "${inst[$idx]}" = 1 ] && chosen+=("${keys[$idx]}"); done ;;
            *) IFS=',' read -r -a chosen <<< "$APPS" ;;
        esac
    else
        local avail=()
        for ((idx = 0; idx < ${#keys[@]}; idx++)); do [ "${inst[$idx]}" = 1 ] && avail+=("$idx"); done
        if [ ${#avail[@]} -eq 0 ]; then
            warn "none of the supported AI apps are installed"
            hint "supported: Claude Code, Codex, Hermes, Gemini CLI, VS Code, Claude Desktop"
            hint "install one, then run: d2l connect all"
            return
        fi
        if interactive; then
            printf '  Which AI apps should be able to read your courses?\n' >/dev/tty
            MENU_LABELS=(); MENU_ON=()
            for idx in "${avail[@]}"; do
                if [ "${mine[$idx]}" = 1 ]; then
                    MENU_LABELS+=("${names[$idx]} ${C_DIM}(already connected)${C_NC}")
                elif [ "${conn[$idx]}" = 1 ]; then
                    MENU_LABELS+=("${names[$idx]} ${C_DIM}(connected to another copy; will switch to this one)${C_NC}")
                else
                    MENU_LABELS+=("${names[$idx]}")
                fi
                MENU_ON+=(1)
            done
            menu_pick
            for ((i = 0; i < ${#avail[@]}; i++)); do
                [ "${MENU_ON[$i]}" = 1 ] && chosen+=("${keys[${avail[$i]}]}")
            done
        else
            for idx in "${avail[@]}"; do chosen+=("${keys[$idx]}"); done
        fi
    fi
    if [ ${#chosen[@]} -eq 0 ]; then hint "none chosen; later: d2l connect <app>"; return; fi
    local out rc
    out="$(cd "$INSTALL_DIR" && "${D2L[@]}" connect "${chosen[@]}" 2>&1)"; rc=$?
    printf '%s\n' "$out" | sed 's/^/  /'
    printf '%s\n' "$out" >> "$LOG"
    record apps "$(IFS=,; printf '%s' "${chosen[*]}")"
    [ "$rc" -eq 0 ] || warn "some apps weren't connected; see above"
}

# --- 11. auto-update ----------------------------------------------------------------------------------------------

can_schedule() {
    case "$OS" in
        Darwin) command -v launchctl >/dev/null 2>&1 ;;
        Linux) command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1 ;;
        *) return 1 ;;
    esac
}

stage_schedule() {
    step "Keeping it up to date"
    if [ "$DO_SCHEDULE" = false ]; then hint "skipped (--no-schedule); later: d2l schedule install --serve"; return; fi
    if ! can_schedule; then
        warn "no user service manager here (systemd --user / launchd); run 'd2l sync' yourself, or from cron"
        return
    fi
    if ! ask_yn "Sync 3× a day (08:00, 14:00, 20:00) and keep the Brightspace app running at http://127.0.0.1:8766?" y; then
        hint "later: d2l schedule install --serve"; return
    fi
    if (cd "$INSTALL_DIR" && run "Setting up the background sync and app" "${D2L[@]}" schedule install --serve); then
        record schedule yes
        ok "automatic sync and the Brightspace app are on"
    else
        warn "couldn't set up the background service; later: d2l schedule install --serve"
    fi
}

# --- address: http://d2l.localhost ----------------------------------------------------------------------------------

APP_URL=""
stage_address() {
    step "Your app's address"
    APP_URL="http://d2l.localhost:$APP_PORT"
    local state
    state="$(cd "$INSTALL_DIR" && "${D2L[@]}" web status 2>/dev/null)"
    if [[ "$state" == *": on"* ]]; then
        APP_URL="http://d2l.localhost"
        ok "type ${C_BOLD}d2l.localhost${C_NC} in your browser"
        return
    fi
    local reason=""
    if (exec 3<>/dev/tcp/127.0.0.1/80) 2>/dev/null; then
        reason="port 80 is used by another program"
    elif [ "$OS" = Linux ] && ! { command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; }; then
        reason="no systemd here"
    elif [ "$OS" = Linux ] && ! command -v sudo >/dev/null 2>&1 && [ "$(id -u)" -ne 0 ]; then
        reason="it needs sudo"
    elif ! interactive && [ "$OS" = Linux ]; then
        reason="it needs your password once; run 'd2l web install' to add it"
    fi
    if [ -z "$reason" ]; then
        local q="Open the app by typing just d2l.localhost in your browser?"
        [ "$OS" = Linux ] && q="$q (asks for your password once: a tiny service that may only use port 80)"
        if ask_yn "$q" y; then
            if (cd "$INSTALL_DIR" && "${D2L[@]}" web install </dev/tty); then
                record web yes
                APP_URL="http://d2l.localhost"
                ok "type ${C_BOLD}d2l.localhost${C_NC} in your browser"
                return
            fi
            reason="setting it up didn't work"
        else
            reason="skipped; add it later with 'd2l web install'"
        fi
    fi
    ok "your app: ${C_BOLD}$APP_URL${C_NC}"
    hint "short address d2l.localhost not set up: $reason"
}

# --- done ---------------------------------------------------------------------------------------------------------

stage_done() {
    record version "$(git -C "$INSTALL_DIR" rev-parse --short HEAD 2>/dev/null)"
    record installed "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf '\n%s%s  ✓ All set.%s\n\n' "$C_GREEN" "$C_BOLD" "$C_NC"
    printf '  %sYour app%s      %s\n' "$C_BOLD" "$C_NC" "${APP_URL:-http://d2l.localhost:$APP_PORT}"
    printf '  %sPorts%s         app %s · AI connection (MCP + REST) %s   (d2l ports)\n\n' "$C_BOLD" "$C_NC" "$APP_PORT" "$MCP_PORT"
    printf '  %sTry it%s\n' "$C_BOLD" "$C_NC"
    printf '    d2l due                 what is coming up\n'
    printf '    d2l new                 what changed recently\n'
    printf '    d2l open                the dashboard + Connect AI (cloud AIs, Semestra)\n'
    printf '    or ask your AI app:     "what is due this week?"\n\n'
    printf '  %sLater%s\n' "$C_BOLD" "$C_NC"
    printf '    update      curl -fsSL %s | bash\n' "$RAW_URL"
    printf '    uninstall   bash %s/install.sh --uninstall\n' "$(tld "$INSTALL_DIR")"
    printf '    install log %s\n' "$(tld "$LOG")"
    if [ "$NEEDS_RELOAD" = true ]; then
        printf '\n  %sOpen a new terminal to use the d2l command%s (or: source ~/.%src)\n' "$C_YELLOW" "$C_NC" \
            "$( [ "${SHELL##*/}" = zsh ] && echo zsh || echo bash )"
    fi
    printf '\n'
}

# --- uninstall ----------------------------------------------------------------------------------------------------

remove_rc_block() {
    local rc="$1" tmp
    [ -f "$rc" ] && grep -qF "$RC_BEGIN" "$rc" || return 0
    tmp="$(mktemp)" || return 0
    # drop the block, and the one blank line put before it
    awk -v b="$RC_BEGIN" -v e="$RC_END" '
        skip { if ($0 == e) skip = 0; next }
        $0 == b { skip = 1; pend = 0; next }
        pend { print ""; pend = 0 }
        $0 == "" { pend = 1; next }
        { print }
        END { if (pend) print "" }' "$rc" > "$tmp" && cat "$tmp" > "$rc"
    rm -f "$tmp"
    ok "removed the PATH line from $(tld "$rc")"
}

uninstall() {
    banner
    printf '\n  %sUninstall%s\n' "$C_BOLD" "$C_NC"
    local dir
    dir="$(recorded dir)"; dir="${dir:-$INSTALL_DIR}"
    PY="$dir/.venv/bin/python"; D2L=("$PY" -m d2l)
    if [ ! -f "$STATE" ] && [ ! -d "$dir" ]; then
        warn "nothing to uninstall (no install at $dir)"
        exit 0
    fi
    if [ -x "$PY" ]; then
        # Only what points at this install: another copy of the app keeps its own connections and services.
        if ask_yn "Remove Brightspace from your AI apps?" y; then
            (cd "$dir" && "${D2L[@]}" disconnect --all) 2>&1 | sed 's/^/  /'
        fi
        if [ "$(recorded schedule)" = yes ]; then
            (cd "$dir" && "${D2L[@]}" schedule remove) >/dev/null 2>&1 && ok "stopped the background sync and app"
        fi
        if [ "$(recorded web)" = yes ]; then
            (cd "$dir" && "${D2L[@]}" web remove </dev/tty) 2>&1 | sed 's/^/  /'
        fi
    fi
    local link
    link="$(recorded launcher)"; link="${link:-$BIN_DIR/d2l}"
    if [ -L "$link" ] && [ "$(readlink "$link")" = "$dir/.venv/bin/d2l" ]; then
        rm -f "$link" && ok "removed $link"
    fi
    local rc
    for rc in "$HOME/.bashrc" "$HOME/.profile" "$HOME/.zshrc" "$HOME/.config/fish/config.fish"; do
        remove_rc_block "$rc"
        if [ "$(recorded "rc:$rc")" = created ] && [ -f "$rc" ] && ! grep -q '[^[:space:]]' "$rc"; then
            rm -f "$rc"
        fi
    done
    if [ -d "$dir" ]; then
        if [ "$PURGE" = true ] || ask_yn "Also delete your downloaded courses, files and saved sign-in in $dir?" n; then
            if [ -f "$dir/pyproject.toml" ] && grep -q 'name = "d2l-brightspace"' "$dir/pyproject.toml"; then
                rm -rf "$dir" && ok "deleted $dir"
            else
                warn "$dir doesn't look like this app; not deleting it"
            fi
        else
            rm -rf "$dir/.venv"
            ok "kept your data in $dir (code environment removed; delete the folder to remove the rest)"
        fi
    fi
    rm -rf "$D2L_HOME/tools" "$D2L_HOME/logs" "$STATE"
    rmdir "$D2L_HOME" 2>/dev/null || :
    printf '\n%s  ✓ Uninstalled.%s Your AI apps and shell are back to how they were.\n\n' "$C_GREEN" "$C_NC"
}

# --- main ---------------------------------------------------------------------------------------------------------
# All of the work happens in this function, called on the script's last line, so a download that is cut off halfway
# through `curl | bash` runs nothing at all.

main() {
    if [ "$UNINSTALL" = true ]; then uninstall; exit 0; fi
    banner
    mkdir -p "${LOG%/*}" || fail "cannot create $D2L_HOME"
    printf '\n==== install %s ====\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$LOG"
    hint "installing into $(tld "$INSTALL_DIR") · log: $(tld "$LOG")"
    stage_prerequisites
    stage_uv
    stage_download
    stage_python
    stage_browser
    stage_school
    stage_ports
    stage_command
    stage_login
    stage_sync
    stage_apps
    stage_schedule
    stage_address
    stage_done
}

main
