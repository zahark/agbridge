#!/bin/sh
# agbridge installer -- run once per machine, and it has TWO sides.
#
#   sh install.sh mac  --instance <name> --statedir <farm path>
#                      --feed-host <target> --agb-remote-path <farm path>
#   sh install.sh farm --mac-id <id>
#
# The mac line used to be spelled without `--instance`, and that command now
# exits 1 and installs nothing: every Mac-side instance is named, so `agb
# instances` can say what exists and no command has to guess which one was
# meant. `--statedir` comes with it, because a first install has no config to
# read it back out of; exactly when a RE-install may drop it is stated once, in
# `usage()`'s `--statedir` entry, carve-out included.
#
# ---------------------------------------------------------------------------
# Why there are two roles
# ---------------------------------------------------------------------------
#
# The farm side is NOT a no-op. /shared is NFS-shared, so every farm host sees
# the same three files -- but NFS carries the *binary*, not the configuration:
#
#   * the hooks have to be merged into ~/.claude/settings.json, with the
#     statedir and an absolute interpreter baked into the command (constraints
#     #1 and #14); and
#   * `agb status-line` runs under tmux's status-interval, where neither
#     $AGB_STATEDIR nor ssh's `env` exists, so the only thing that can tell it
#     which statedir and which bridge beat to watch is
#     ~/.config/agbridge/config.
#
# The two sides meet at exactly one value, `mac_id`. The Mac mints it (nothing
# else in the tool produces one) and the farm must be told the *same* one: the
# bridge writes bridge/<mac-id>.beat and the segment reads that exact name, so
# a second, separately generated id would leave both halves healthy and the
# segment reading bridge:DOWN for ever. `install.sh mac` therefore prints -- or
# with --farm <target>, runs -- the exact farm command, id included.
#
# ---------------------------------------------------------------------------
# The distribution is THREE files
# ---------------------------------------------------------------------------
#
# `agb`, `agb_mac` and `agb_ops`, and they must land in the SAME directory:
# `agb` resolves its siblings from realpath(__file__). A copy that misses one
# does not fail at install time -- it fails at the first `agb bridge`
# (agb_mac) or the first `agb doctor` (agb_ops), which is the worst possible
# moment for a missing file. So this script copies all three and then *runs*
# the installed tree through all three of them before it configures anything.
#
# POSIX sh: macOS ships bash 3.2 and the farm is tcsh-by-default, so this
# assumes neither.

set -eu

SELF=$(cd "$(dirname "$0")" && pwd -P)
FILES="agb agb_mac agb_ops"
TEMPLATE="dist/com.agbridge.plist"

DEFAULT_LABEL="com.agbridge"
DEFAULT_DEST="$HOME/.local/lib/agbridge"
DEFAULT_AGENTS="$HOME/Library/LaunchAgents"
DEFAULT_LOGDIR="$HOME/Library/Logs/agbridge"
# One directory, two spellings of what lives in it: the default instance's
# config is the file, a named instance's is a subdirectory of the same place.
# Derived from one constant so the two cannot drift apart.
DEFAULT_CONFIG_DIR="$HOME/.config/agbridge"
DEFAULT_CONFIG="$DEFAULT_CONFIG_DIR/config"
# A LaunchAgent inherits almost no PATH, and the bridge shells out to
# `agtermctl` and `ssh`. Homebrew first (both architectures), then the system.
DEFAULT_BINDIR="$HOME/.local/bin"
DEFAULT_LAUNCH_PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
# `agb install-config --print-statedir`'s answer for "this file carries no
# statedir of its own", as opposed to 1 for "I could not read it at all". A
# CROSS-FILE AGREEMENT with `agb_ops.PRINT_STATEDIR_NONE` (invariant 14): this
# script cannot import that file, so it spells the number itself. A silent
# disagreement would make the adoption below report every unreadable config as
# "carries none" -- which is the failure the two statuses exist to separate --
# so `tests/test_install_pkg.py` compares the two values directly.
PRINT_STATEDIR_NONE=4

die() { printf 'install.sh: %s\n' "$*" >&2; exit 1; }
say() { printf '%s\n' "$*"; }

usage() {
    cat >&2 <<'EOF'
usage: install.sh mac  --instance <name> --statedir <farm path>
                       --feed-host <ssh-target> --agb-remote-path <farm path>
                       [options]
       install.sh farm --mac-id <id> [options]

       --statedir comes with --instance; when a re-install may drop it is
       stated once, under --statedir below.

mac -- copy agb, agb_mac and agb_ops, write ~/.config/agbridge/<name>/config with
       a freshly minted mac_id, render the launchd plist and load it.

  --instance <name>          REQUIRED. Every Mac-side instance is named, so
                             `agb instances` can say what exists and no command
                             has to guess which one you meant. Its own config,
                             label and logs live under <name>. Comes with
                             --statedir -- see that entry for when. mac only.
                             `auto` reads the name off --feed-host instead
  --feed-host <target>       ssh target of the farm box running `agb feed`  (required)
  --agb-remote-path <path>   absolute path of `agb` on the farm             (required)
  --statedir <path>          farm-side statedir, and THE statement of this rule
                             (the two mentions above point here, because three
                             copies of it had already drifted apart): required
                             for a NEW instance; adopted from that instance's
                             own config on a re-install -- but never when
                             --config is also given, since that file may belong
                             to another instance and its statedir is another
                             disk
  --remote-python <path>     absolute farm-side interpreter        (default /bin/python3)
  --jump-host <target>       ssh jump host for machine #3
  --host <name>=<target>     ssh target for a record's host; repeatable
  --mac-id <id>              adopt an existing mac-id instead of minting one
  --dest <dir>               where the three files go       (default ~/.local/lib/agbridge)
  --python <path>            absolute interpreter to run the bridge with
  --config <path>            config file      (default ~/.config/agbridge/<name>/config)
  --launch-agents <dir>      (default ~/Library/LaunchAgents)
  --log-dir <dir>            (default ~/Library/Logs/agbridge/<name>)
  --launch-path <PATH>       PATH given to the launchd job
  --label <name>             launchd label               (default com.agbridge.<name>)
  --farm <ssh-target>        run the farm side over ssh with the minted mac-id
  --no-load                  write the plist but do not load it
  --no-probe                 do not ssh the feed host to learn its hostname
  --bin-dir <dir>            where `agb` and `agb-refresh` go (default ~/.local/bin)
  --no-wrapper               do not write the `agb` wrapper or link agb-refresh
  --dry-run                  say what would happen; write nothing

farm -- write ~/.config/agbridge/config and merge the hooks into
        ~/.claude/settings.json. Run it on every farm host that runs agents.

  --mac-id <id>              the id `install.sh mac` printed                (required)
  --statedir <path>          statedir            (default: $AGB_STATEDIR, config, default)
  --config <path>            config file                (default ~/.config/agbridge/config)
  --settings <path>          settings file                 (default ~/.claude/settings.json)
  --python <path>            absolute interpreter to bake into the hook command
  --jump-host <target>       ssh jump host for machine #3
  --host <name>=<target>     ssh target for a record's host; repeatable
  --agb <path>               the agb to install hooks for       (default: beside this file)
  --bin-dir <dir>            where `agb` and `agb-refresh` go (default ~/.local/bin)
  --no-wrapper               do not write the `agb` wrapper or link agb-refresh
  --no-hooks                 write the config only
  --dry-run                  say what would happen; write nothing
EOF
}

# Values that end up on a remote command line (ssh re-splits it) or in a config
# file. Refused at install time rather than at the first connection.
# A leading `-` is refused separately, because the allowed set has to keep `-`
# for the inside of host names and paths. `ssh "$farm" "$@"` hands its first
# word to ssh's own option parser: `--farm -oProxyCommand=/tmp/x` runs /tmp/x
# instead of connecting anywhere, and every character of it is in the allowed
# set below. --feed-host and --jump-host are caught again on the Python side
# (`agb_ops._ssh_word_ok`, `agb_mac.bridge_ssh_argv`), which spell the same
# refusal for the same reason; --farm is consumed only here, so this is its
# only gate.
shell_safe() {
    case "$2" in
        "") die "$1 is empty" ;;
        -*) die "$1 must not start with '-': ssh reads a leading dash as an option, not a value, so $2 would be an ssh option (e.g. -oProxyCommand=...) rather than a host" ;;
        *[!A-Za-z0-9./_@:%+=-]*)
            die "$1 contains characters that would not survive a remote shell: $2" ;;
    esac
}

absolute() {
    case "$2" in
        /*) ;;
        *) die "$1 must be an absolute path, not: $2" ;;
    esac
}

# `--instance <name>` is validated separately from shell_safe, and more
# narrowly, because it is not a value on a command line -- it becomes FOUR
# structural things at once: a launchd label component, a plist FILENAME, a log
# DIRECTORY and a config DIRECTORY. shell_safe keeps `.` and `/` for the inside
# of paths and host names, so `--instance ../../evil` passes it and then writes
# a config, a log directory and a launchd plist three levels above where any of
# them were meant to go -- with an install that reports success. Alphanumerics,
# `-` and `_`: `.` is excluded too, so neither a leading dot nor a `..` segment
# can be spelled at all, and `com.agbridge.<name>` stays one label component.
#
# `$2`, when given, says where the name came from. `--instance auto` reads it
# back off the machine, so a hostname that is not a usable name has to complain
# about the HOST rather than read as a complaint about something the operator
# typed -- they typed `auto`. `agb-refresh`'s copy has no such argument and
# needs none (it has no `auto`); the cross-script agreement is over the `case`
# PATTERNS, not the messages, which is what lets these two differ.
instance_ok() {
    io_from=${2:-}
    case "$1" in
        "") die "--instance needs a name$io_from" ;;
        -*) die "--instance must not start with '-': $1$io_from" ;;
        *[!A-Za-z0-9_-]*)
            die "--instance must be letters, digits, '-' or '_': $1$io_from. It becomes a launchd label component, a plist filename, a log directory and a config directory, so a '/' or a '.' in it would place all four somewhere other than where they were meant to go" ;;
    esac
}

# A record's `host` is the farm's HOSTNAME; `--feed-host` is an ssh ALIAS. This
# is the one mapping the installer can derive rather than ask for: ssh once and
# read the hostname back.
#
# Extracted because there are now TWO readers and they run at different times.
# `--instance auto` needs the answer BEFORE the config path is decided (the
# sugar block below), while the `host_<name>` mapping wants it inside
# `role_mac`, after the files are copied. One ssh either way -- `$farmhost` is
# set once and the second caller finds it already answered, so the two cannot
# disagree about a machine that renamed itself in between.
#
# ⚠️ `|| farmhost=""` is load-bearing under `set -e`: an assignment from a
# command substitution takes that command's status, so an unreachable host, a
# `BatchMode` refusal or a box that is simply down would kill the script rather
# than answer "could not tell". Answering nothing is the point -- for the
# mapping it is a note, for `auto` it is a refusal, and that is the callers'
# decision, not this function's.
probe_farmhost() {
    [ -n "$farmhost" ] && return 0
    farmhost=$(ssh -o BatchMode=yes -o ConnectTimeout=10 "$feedhost" \
                   'hostname -s' 2>/dev/null | tr -d '\r' | head -1) || farmhost=""
    # Anything that is not a plain hostname is no answer at all: a shell profile
    # that greets you, an ssh banner, a `hostname` that printed an error. Wider
    # than `instance_ok`'s rule on purpose -- a `.` is fine in a `host_<name>`
    # key and is not fine in a label -- so `auto` re-asks with the narrow one.
    case "$farmhost" in
        *[!A-Za-z0-9._-]*) farmhost="" ;;
    esac
}

find_python() {
    candidate=$(command -v python3 2>/dev/null || :)
    if [ -z "$candidate" ]; then
        for candidate in /usr/bin/python3 /usr/local/bin/python3 \
                         /opt/homebrew/bin/python3 /bin/python3; do
            [ -x "$candidate" ] && break
            candidate=""
        done
    fi
    [ -n "$candidate" ] || die "no python3 found; pass --python <absolute path>"
    printf '%s' "$candidate"
}

# `agb` is not executable and has no shebang on purpose (constraint #1), so it
# is always run as an argument to the interpreter, never directly.
run_agb() {
    runner=$1
    target=$2
    shift 2
    "$runner" -S -E "$target" "$@"
}

# `agb` cannot be put on $PATH and run: it has no shebang and is not executable
# on purpose (constraint #1), because a hook must pass `-S -E` and neither a
# shebang nor `env` can. Every doc writes `agb doctor`, so something has to make
# that true -- this does, and it is the only place that knows both the
# interpreter and the install path.
write_wrapper() {
    wpython=$1
    wagb=$2
    wdir=$3
    wpath="$wdir/agb"
    if [ "$dry" = yes ]; then
        say "dry run:  would write the wrapper $wpath"
        return 0
    fi
    mkdir -p "$wdir" || { say "note:     could not create $wdir; skipping the wrapper"; return 0; }
    tmp="$wpath.tmp.$$"
    {
        printf '#!/bin/sh\n'
        printf '# Generated by agbridge install.sh. `agb` itself has no shebang and\n'
        printf '# is not executable on purpose: a hook must pass -S -E, and neither a\n'
        printf '# shebang nor `env` can. This wrapper is what makes `agb <cmd>` work.\n'
        printf 'exec %s -S -E %s "$@"\n' "$wpython" "$wagb"
    } > "$tmp" || { say "note:     could not write $wpath"; rm -f "$tmp"; return 0; }
    chmod +x "$tmp" && mv "$tmp" "$wpath" \
        || { say "note:     could not install $wpath"; rm -f "$tmp"; return 0; }
    say "wrapper:  $wpath -> $wpython -S -E $wagb"
    case ":$PATH:" in
        *":$wdir:"*) ;;
        *) say "          ⚠ $wdir is not on your \$PATH, so \`agb\` will not resolve yet" ;;
    esac
}

link_refresh() {
    ldir=$1
    ltarget=$2
    [ "$wrapper" = yes ] || return 0        # --no-wrapper means neither
    lpath="$ldir/agb-refresh"
    if [ "$dry" = yes ]; then
        # Asked about the SOURCE, not the target: a dry run copies nothing, so
        # testing the target would silently report nothing and make the dry run
        # a worse description of the real one than it needs to be.
        [ -f "$SELF/agb-refresh" ] \
            && say "dry run:  would link $lpath -> $ltarget"
        return 0
    fi
    [ -f "$ltarget" ] || return 0           # not installed; nothing to link
    mkdir -p "$ldir" || return 0
    # Never fatal, here or anywhere in this function: a missing convenience
    # link must not fail an install that otherwise worked. The full path in
    # the message is what makes the failure recoverable by hand.
    ln -sfn "$ltarget" "$lpath" 2>/dev/null \
        && say "link:     $lpath -> $ltarget" \
        || say "note:     could not link $lpath; run $ltarget directly"
}

# Prove the tree at $2 works, through all three files, before anything is
# configured against it. `version` alone would pass with agb_mac and agb_ops
# missing entirely -- which is precisely the failure this checks for.
#
# The memo. Initialised here rather than with the options below, because it
# belongs to this function and not to any flag -- and under `set -u` an unset
# name is a dead script rather than a cache miss.
verified_tree=""
verified_answer=""
# ⚠️ ASKED ONCE PER TREE, and the memo is not a micro-optimisation. Three
# `agb` runs is three interpreter starts, and `role_mac` now has two callers
# for the SAME tree: the statedir adoption proves `$SELF/agb` before it trusts
# an answer out of it, and the `--dry-run` branch proves the same file again
# because that is the tree a dry run would install. Six starts to say one
# thing. A second call therefore re-reports the answer the first one got --
# which also keeps the `verified:` line where the reader expects it, since the
# adoption's own report is suppressed (see role_mac). The two are the same
# claim only because nothing can change the file mid-run; `$installed` after
# the copy is a DIFFERENT tree and is genuinely proved again.
#
# ⚠️ ONE `say`, OUTSIDE the memo branch, because the hit path and the miss path
# make the SAME claim -- and a second copy of the sentence is a second thing to
# keep in step. The memo test can only count `verified:` lines (the adoption's
# report is suppressed either way, so the count cannot see the memo at all), so
# a divergence between two spellings would be invisible to it.
verify_tree() {
    vpython=$1
    vagb=$2
    if [ "$verified_tree" != "$vpython $vagb" ]; then
        answer=$(run_agb "$vpython" "$vagb" version) \
            || die "cannot run $vagb with $vpython"
        case "$answer" in
            "agb "*) ;;
            *) die "$vagb version answered '$answer', not 'agb <version>'" ;;
        esac
        run_agb "$vpython" "$vagb" status-line \
                --statedir "$(dirname "$vagb")/.install-probe" --mac-id probe \
                >/dev/null \
            || die "the tree at $(dirname "$vagb") cannot run 'agb status-line': agb_ops is missing or broken beside agb"
        run_agb "$vpython" "$vagb" bridge --from-stdin --no-agterm \
                --feed-host probe --mac-id probe </dev/null >/dev/null \
            || die "the tree at $(dirname "$vagb") cannot run 'agb bridge': agb_mac is missing or broken beside agb"
        verified_tree="$vpython $vagb"
        verified_answer=$answer
    fi
    say "verified: $verified_answer at $vagb, with agb_mac and agb_ops beside it"
}

xml_escape() {
    printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'
}

# XML-escape for the file, then escape what sed treats as special in a
# replacement (`&`, the `|` delimiter, backslash).
rep() {
    xml_escape "$1" | sed -e 's/[\\&|]/\\&/g'
}

# ---------------------------------------------------------------------------
# options
# ---------------------------------------------------------------------------

role=${1:-}
case "$role" in
    mac|farm) shift ;;
    -h|--help|help|"") usage; exit 2 ;;
    *) usage; die "unknown role: $role (expected 'mac' or 'farm')" ;;
esac

dest=""; python=""; config=""; statedir=""; macid=""; feedhost=""
remotepath=""; remotepython=""; jumphost=""; hosts=""; agentsdir=""
logdir=""; launchpath=""; label=""; farm=""; settings=""; agbpath=""
load=yes; hooks=yes; dry=no; probe=yes; bindir=""; wrapper=yes; instance=""
# `probe_farmhost` reads it before it writes it (the once-only guard), and
# `set -u` would kill the script on the unset name rather than ask the question.
farmhost=""
# Whether `--config` was TYPED, as opposed to derived from `--instance`. Set
# where the derivation happens, read once in `role_mac` (the statedir
# adoption). Initialised here for the same reason as `$farmhost`: the farm role
# never reaches the line that sets it, and under `set -u` an unset name is a
# dead script rather than a `no`.
config_given=no

# A value must be PRESENT and NON-EMPTY. ⚠️ The second half is not pedantry:
# `--config "$cfg"` with `$cfg` unset expands to one empty argument, so the
# COUNT is right and the value is not -- and every one of these flags has a
# default waiting below (`[ -n "$config" ] || config="$DEFAULT_CONFIG"`, the
# `--instance` conventions, `find_python`), so the empty value is silently
# replaced and the install SUCCEEDS against something other than what was
# named. `--statedir ""` is the worst of them: it reads as "not given", so the
# instance inherits the default config's farm path -- ssh to the right machine
# and read the wrong directory, which is the one failure `--instance` refuses
# to install without.
#
# ⚠️ It is also the rule `agb`'s own nine parsers already enforce -- `if not
# inline: raise ... needs a value`, for the `--opt=` and the `--opt ""` spelling
# alike -- so this is the shell agreeing with the Python rather than a new idea.
# `agb-refresh` spells the same function, because neither script can import the
# other (invariant 14); `tests/test_install_pkg.py` compares the two bodies.
need() { [ "$1" -gt 1 ] && [ -n "$3" ] || die "$2 needs a value"; }

while [ $# -gt 0 ]; do
    case "$1" in
        --dest) need $# "$1" "${2:-}"; dest=$2; shift 2 ;;
        --python) need $# "$1" "${2:-}"; python=$2; shift 2 ;;
        --config) need $# "$1" "${2:-}"; config=$2; shift 2 ;;
        --statedir) need $# "$1" "${2:-}"; statedir=$2; shift 2 ;;
        # Validated here rather than after the loop, like --host: the name is a
        # launchd label component, a plist filename and a config directory.
        # `need` above already refuses the EMPTY name (it used to only count
        # arguments, so `--instance ""` read as "not given" and installed the
        # DEFAULT instance while reporting the name back); `instance_ok` keeps
        # its own empty case anyway, since agb-refresh spells the same rule.
        --instance) need $# "$1" "${2:-}"; instance_ok "$2"; instance=$2; shift 2 ;;
        --mac-id) need $# "$1" "${2:-}"; macid=$2; shift 2 ;;
        --feed-host) need $# "$1" "${2:-}"; feedhost=$2; shift 2 ;;
        --agb-remote-path) need $# "$1" "${2:-}"; remotepath=$2; shift 2 ;;
        --remote-python) need $# "$1" "${2:-}"; remotepython=$2; shift 2 ;;
        --jump-host) need $# "$1" "${2:-}"; jumphost=$2; shift 2 ;;
        --host) need $# "$1" "${2:-}"
                shell_safe "--host" "$2"
                case "$2" in *=*) ;; *) die "--host wants <name>=<ssh-target>" ;; esac
                hosts="$hosts $2"; shift 2 ;;
        --launch-agents) need $# "$1" "${2:-}"; agentsdir=$2; shift 2 ;;
        --log-dir) need $# "$1" "${2:-}"; logdir=$2; shift 2 ;;
        --launch-path) need $# "$1" "${2:-}"; launchpath=$2; shift 2 ;;
        --label) need $# "$1" "${2:-}"; label=$2; shift 2 ;;
        --farm) need $# "$1" "${2:-}"; farm=$2; shift 2 ;;
        --settings) need $# "$1" "${2:-}"; settings=$2; shift 2 ;;
        --agb) need $# "$1" "${2:-}"; agbpath=$2; shift 2 ;;
        --no-load) load=no; shift ;;
        --no-hooks) hooks=no; shift ;;
        --no-probe) probe=no; shift ;;
        --bin-dir) need $# "$1" "${2:-}"; bindir=$2; shift 2 ;;
        --no-wrapper) wrapper=no; shift ;;
        --dry-run) dry=yes; shift ;;
        -h|--help) usage; exit 2 ;;
        *) usage; die "unknown option: $1" ;;
    esac
done

for f in $FILES; do
    [ -f "$SELF/$f" ] || die "missing $f in $SELF -- the distribution is three files ($FILES) and a tree with only some of them fails at the first bridge, doctor or pane call, not at install time"
done

[ -n "$python" ] || python=$(find_python)
absolute "the interpreter" "$python"
[ -x "$python" ] || die "$python is not executable"
# ⚠️ HERE, above the `--instance auto` block, and NOT with the other value
# checks below it. `auto` calls `probe_farmhost`, which runs
# `ssh "$feedhost" 'hostname -s'` -- so validating `--feed-host` after that
# block would hand an unvalidated word to ssh's own option parser, which is the
# one thing `shell_safe`'s leading-dash rule exists to prevent. It was ordered
# the other way when `--instance` was optional and `auto` was a rarity; making
# `--instance` mandatory promotes `auto` to a primary path, so the ordering
# stops being academic. (`role_mac` requires the flag to be non-empty; this
# only says what it may contain, hence the `[ -n ]` guard -- the farm role
# takes no `--feed-host` and must not be refused for lacking one.)
if [ -n "$feedhost" ]; then shell_safe "--feed-host" "$feedhost"; fi
# --instance is SUGAR over three flags that already exist, applied here because
# the option loop above is role-agnostic and `$config` is used by both roles.
# Everything an instance is, is these three paths -- there is no fourth thing to
# forget, and an explicit --config/--label/--log-dir still wins, because the
# defaults below are all `[ -n ... ] ||`.
# `--instance auto` names the instance after the machine, from the hostname this
# installer already ssh's for. Resolved HERE, before the paths below, because
# the name is what decides all three of them.
#
# ⚠️ OPT-IN, and it can never become the default. If `--instance` meant "name it
# after whatever the feed host calls itself", every upgrade of an existing
# install would mint a NEW instance beside it -- new config, new launchd job,
# new rows map, and every row duplicated in the sidebar. Typing the word is the
# whole difference between those two, and that still holds: a bare
# `install.sh mac` is REFUSED (see role_mac), never auto-named.
#
# ⚠️ WITHDRAWN, and kept because a withdrawn reason that is deleted gets
# re-proposed. Two clauses of the paragraph above used to read: "Re-running
# `install.sh mac` with the original flags is the documented upgrade path, so an
# ABSENT `--instance` has to keep meaning the default instance." Both are now
# false. `--instance` is REQUIRED for the mac role, so an absent one means
# nothing at all -- it is an error -- and a legacy NAMELESS install therefore has
# no in-place upgrade path: re-running it with the original flags is refused, and
# adopting the old file with `--config <the default path>` still demands
# `--statedir`. The rule they were protecting (never DERIVE a name nobody typed)
# survives above; what died is the fall-back they derived it from.
#
# ⚠️ And a failure here is a REFUSAL, never a fall-back to the default instance.
# That fall-back is the accident this feature exists to avoid: the run would
# rewrite the first machine's `feed_host` and `statedir`, boot out its launchd
# job and point its bridge at the new box -- reporting success in the same words
# a correct run uses. The probe is best-effort for the host MAPPING (a note, and
# you pass `--host` yourself) and cannot be for the NAME, which decides where
# four things are written.
#
# The literal name `auto` is therefore unavailable. A machine really called
# `auto` needs its instance spelled some other way -- or the three flags the
# sugar stands for.
if [ "$instance" = auto ]; then
    [ "$role" = mac ] || die "--instance auto is for the mac role only (and so is --instance at all): nothing on the farm reads a per-instance config"
    [ -n "$feedhost" ] || die "--instance auto needs --feed-host: the name is read back off that machine, and there is nothing else to ask"
    [ "$probe" = yes ] || die "--instance auto and --no-probe contradict each other: the probe IS the name. Drop one, or pass --instance <name> explicitly"
    probe_farmhost
    [ -n "$farmhost" ] || die "--instance auto: could not read a hostname back from $feedhost. Refusing rather than falling back to the DEFAULT instance, which would repoint the machine you already have -- rewriting its feed_host and statedir and booting out its launchd job. Pass --instance <name> explicitly, or fix the ssh and re-run"
    instance_ok "$farmhost" " -- read back from $feedhost, which is what --instance auto asked it"
    instance=$farmhost
    say "instance: auto -> $instance (read back from $feedhost)"
fi

if [ -n "$instance" ]; then
    # Mac only. `--label` and `--log-dir` are mac-only already, so the sugar is
    # mac-only by construction -- but `$config` is not, and nothing on the FARM
    # reads a per-instance config: `agb hook` and `agb status-line` resolve it
    # through `agb.config_path()`, the default path, and nothing else. So
    # `install.sh farm --instance x` would write a real config to a path no
    # farm-side reader ever opens and report success, which is the silent
    # no-op class this tool exists to remove.
    [ "$role" = mac ] || die "--instance is for the mac role only: nothing on the farm reads a per-instance config (agb hook and agb status-line resolve ~/.config/agbridge/config and nothing else), so 'install.sh farm --instance $instance' would write a config nothing opens and report success"
    # ⚠️ The `--statedir` requirement USED to be the next line. It lives in
    # `role_mac` now, after the adoption that can satisfy it -- and the move is
    # safe only because of the check immediately above: `--instance` is refused
    # outright for the farm role, so there is no argv in which a statedir rule
    # inside `role_mac` fails to run for an instance that has one. Keep that
    # check HERE. Moving it too would make the requirement mac-only by
    # accident rather than by construction.
    #
    # ⚠️ WHETHER `--config` was TYPED, recorded at the line that erases the
    # difference: below this point `$config` is one string, and a derived path
    # and an explicitly-passed one are spelled identically. `role_mac` adopts a
    # statedir only from the config `--instance` DERIVED, because
    # `--instance hostb --config <the other cluster's config>` is a legal shape
    # and adopting through it would hand hostb's bridge the other cluster's
    # directory -- ssh to the right machine, read the wrong disk, which is the
    # one failure the statedir rule exists to prevent. This flag is the only
    # surviving evidence of which of the two `$config` is.
    if [ -n "$config" ]; then config_given=yes
    else config="$DEFAULT_CONFIG_DIR/$instance/config"; fi
    [ -n "$logdir" ] || logdir="$DEFAULT_LOGDIR/$instance"
    [ -n "$label" ] || label="$DEFAULT_LABEL.$instance"
fi

[ -n "$config" ] || config="$DEFAULT_CONFIG"
# ABSOLUTE, like every other path this script writes down. `--config` is the one
# that reaches launchd: it is rendered UNCONDITIONALLY into ProgramArguments,
# and the job runs with `WorkingDirectory /tmp`. So `--config relcfg/config`
# writes a real config to `$PWD/relcfg/config`, reports success, and hands the
# bridge `/tmp/relcfg/config` -- a path that does not exist there, read as `{}`,
# with KeepAlive turning it into a permanent restart loop naming a file that
# exists where the operator was standing. The quoted `~` form is the same bug
# with a worse tell: `install-config` expands it and the plist does not.
# `/tmp` is world-writable, so the relative path is plantable too, and a config
# supplies `feed_host`/`remote_python`/`jump_host` straight into the ssh argv.
# Checked for both roles: on the farm a relative config is written where no
# farm-side reader looks (`agb hook` resolves `agb.config_path()` and nothing
# else), which is the same silent no-op with no launchd to make it loud.
absolute "--config" "$config"
if [ -n "$statedir" ]; then shell_safe "--statedir" "$statedir"
                            absolute "--statedir" "$statedir"; fi
if [ -n "$macid" ]; then shell_safe "--mac-id" "$macid"; fi
# Checked HERE and not in role_mac, because `role_mac` forwards it to the farm
# and the farm role also takes it directly: validated in one role only, an
# `install.sh farm --jump-host -oProxyCommand=...` writes it straight into the
# config. Both consumers refuse it at use (`pane_ssh_argv`, `prune_ssh_argv`),
# so this is the loud half of a rule that was already enforced late.
if [ -n "$jumphost" ]; then shell_safe "--jump-host" "$jumphost"; fi

# ---------------------------------------------------------------------------
# mac
# ---------------------------------------------------------------------------

role_mac() {
    # ⚠️ HERE, beside the other two requirements, and BEFORE any filesystem
    # mutation (the first is `mkdir -p "$dest"` below), so a refusal installs
    # nothing rather than leaving a half-copied tree. It is also before
    # `probe_farmhost`, so a refused install makes no ssh call either: the probe
    # is never consulted, which is what makes "no name can be invented" true
    # rather than merely untested.
    #
    # The mac role and NOT the farm role: a farm host has exactly one identity,
    # and `agb hook` resolves `agb.config_path()` -- the default path -- on every
    # invocation, so a named farm config is a file nothing opens. That is the
    # same reason `--instance` is refused for the farm role above; here the
    # asymmetry runs the other way, and both halves of it come from the one fact.
    #
    # A hard error and not a warning: a warning on a first install gets ignored,
    # and the asymmetry it warned about then becomes permanent on that Mac.
    # Single-quoted, because a double-quoted `agb instances` is command
    # substitution and `die` would RUN it.
    [ -n "$instance" ] || die 'mac: --instance is required. Every Mac-side instance is named, so `agb instances` can say what exists and no command has to guess which one you meant. Pass --instance <name>, or --instance auto to name it after --feed-host.'
    [ -n "$feedhost" ] || die "mac: --feed-host is required (the bridge cannot invent the ssh target, and one that silently never connects is the failure this tool exists to remove)"
    [ -n "$remotepath" ] || die "mac: --agb-remote-path is required: the absolute path of agb ON THE FARM, e.g. /opt/agbridge/agb"
    # `shell_safe "--feed-host"` is NOT here: it runs before the `--instance
    # auto` block above, because that block ssh's to this host and a check
    # after it would be a check after the connection. See the comment there.
    shell_safe "--agb-remote-path" "$remotepath"
    absolute "--agb-remote-path" "$remotepath"
    if [ -n "$remotepython" ]; then shell_safe "--remote-python" "$remotepython"
                                    absolute "--remote-python" "$remotepython"; fi
    if [ -n "$farm" ]; then shell_safe "--farm" "$farm"; fi
    [ -n "$dest" ] || dest="$DEFAULT_DEST"
    [ -n "$agentsdir" ] || agentsdir="$DEFAULT_AGENTS"
    [ -n "$launchpath" ] || launchpath="$DEFAULT_LAUNCH_PATH"
    # ⚠️ `$logdir` and `$label` are NOT defaulted here any more, and their two
    # `[ -n ... ] ||` lines were DELETED rather than left dead. The sugar block
    # up top runs under the identical `[ -n "$instance" ]` test that the refusal
    # above now makes mandatory, and it leaves both non-empty -- so
    # `logdir="$DEFAULT_LOGDIR"` and `label="$DEFAULT_LABEL"` were unreachable,
    # and the second in particular said a mac install could still be labelled
    # `com.agbridge`, which is the opposite of what this role now guarantees.
    # (Contrast the `install-config` argv's `if [ -n "$statedir" ]` below, dead
    # by the same argument and deliberately KEPT: the difference is that these
    # two claimed something false, where that one only spells a condition that
    # happens always to hold.)

    say "agb install (mac) -- from $SELF"
    say "python:   $python"
    # Said out loud, every run, and UNCONDITIONALLY since `--instance` became
    # required -- which instance this is acting on is the one thing that cannot
    # be inferred from the rest of the output, and acting on the wrong one is
    # this feature's worst (and quietest) failure.
    say "instance: $instance -- label $label, config $config"

    # ⚠️ HERE -- after the header, before the first filesystem mutation
    # (`mkdir -p "$dest"` below) -- and NOT beside the `--instance` sugar up
    # top, where the requirement it satisfies used to live. Two reasons, and
    # both are hard errors rather than style:
    #
    #   * `$installed` does not exist yet up there. It is assigned in the two
    #     branches below, so under `set -u` naming it earlier aborts the script
    #     on an unset variable instead of falling back to anything;
    #   * moving the rule past `installed=` instead would put it after
    #     `mkdir -p "$dest"`, and a refused install would leave a half-made
    #     tree behind. Refusals in this script write nothing.
    #
    # ⚠️ "Write nothing" means nothing an operator would have to find and clean
    # up, not that no byte lands anywhere: running `agb` out of `$SELF` imports
    # `agb_mac` and `agb_ops` BY PATH, and CPython caches their bytecode in
    # `$SELF/__pycache__`. That is inside the checkout the installer was run
    # from, gitignored, outside every path this install would have created, and
    # read by nothing but the interpreter. Said out loud because the sentence
    # above is otherwise literally false on the refusal path, and a rule that is
    # nearly true is one nobody can check.
    #
    # So it runs against `$SELF/agb` -- the tree this script was invoked from,
    # proved to exist for all three files before role dispatch, and exactly
    # what the `--dry-run` branch below already runs. `$python` is resolved
    # before role dispatch too, so `run_agb` is usable this early.
    #
    # ⚠️ NOT a symmetric mirror of the mac-id adoption further down, and the
    # asymmetry is the point. That one falls back to `$DEFAULT_CONFIG` on
    # purpose: one Mac has one identity, so sharing an id across instances is
    # the truth. Sharing a STATEDIR is the precise failure the requirement
    # below exists to prevent -- two clusters share no disk, so the default
    # config's path names a directory this instance's farm cannot see, and
    # `agb feed` would create it over there and report an empty farm for ever.
    # Hence one candidate here, never a loop, and only when `--config` was not
    # typed (`$config_given`): an explicit `--config` may name any file at all,
    # including another instance's, and keeps today's behaviour exactly.
    #
    # `if ... then ... fi`, never `[ -n "$statedir" ] && say ...`: a trailing
    # AND-list that evaluates false takes the whole compound's status, which is
    # one `set -e` reading away from a silent `exit 1` with no message at all.
    #
    # ⚠️ THE STATUS IS READ, not merely tested for zero, and that is the whole
    # of this block's honesty. `--print-statedir` answers
    # `$PRINT_STATEDIR_NONE` for "this file carries none of its own" -- an
    # ANSWER -- and 1 for everything else: a config that exists and cannot be
    # read (mode 000, ESTALE, EIO), one that is not UTF-8, an `agb` that does
    # not know the flag. Folding all of those into `|| statedir=""` reported an
    # unreadable config as "carries none to adopt" and sent the operator after
    # `--statedir`, a flag that was not the problem -- measured, and exactly the
    # class this project spells out as "'I could not answer' is not 'the answer
    # is nothing'". So: 0 adopts, NONE falls through to the requirement below,
    # anything else is fatal and says which file it could not read.
    #
    # An assignment from a command substitution takes that command's status, so
    # `|| adopt_rc=$?` is what keeps `set -e` from killing the script on the
    # ordinary "no own statedir" answer -- the same reason the mac-id loop
    # spells `|| adopted=""`.
    #
    # `--dry-run` is passed even on a real install: the query is read-only by
    # construction (it answers the instant the file is parsed and returns), but
    # `--dry-run` makes `install.sh --dry-run`'s "write nothing" promise
    # STRUCTURAL rather than dependent on statement order in another file. It
    # costs nothing -- the flag is explicitly one of the two this query
    # tolerates -- and the mac-id probe below already spells it the same way.
    #
    # `verify_tree` first for the other half of that: a `$SELF` tree that cannot
    # run `agb` at all would otherwise read as "this config carries no
    # statedir", and the operator would be told to pass `--statedir` for an
    # instance that already records one. Its own output is suppressed because
    # the `verified:` line below names the tree being INSTALLED, and two of them
    # -- with the first landing between `instance:` and `statedir:` -- is worse
    # than none here; a failure is still loud, since `verify_tree` dies on
    # stderr rather than reporting. The second call re-reports from the memo
    # rather than re-running three interpreters (see `verify_tree`).
    if [ -z "$statedir" ] && [ "$config_given" = no ]; then
        verify_tree "$python" "$SELF/agb" >/dev/null
        adopt_rc=0
        statedir=$(run_agb "$python" "$SELF/agb" install-config \
                           --config "$config" --dry-run --print-statedir \
                       2>/dev/null) || adopt_rc=$?
        if [ "$adopt_rc" -eq 0 ]; then
            # The top-level `shell_safe`/`absolute` pair ran before this value
            # existed, so it would reach the config and the farm hint unchecked.
            # Defence in depth -- `check_config_value` vetted it on the way in
            # -- and exactly why the adopted mac-id is re-checked below.
            shell_safe "the adopted statedir" "$statedir"
            absolute   "the adopted statedir" "$statedir"
            say "statedir: adopted $statedir from $config"
        else
            statedir=""
            [ "$adopt_rc" -eq "$PRINT_STATEDIR_NONE" ] \
                || die "could not read $config (agb install-config --print-statedir exited $adopt_rc, not $PRINT_STATEDIR_NONE). That is NOT 'it carries no statedir': the file is there and this installer could not read it -- unreadable, or not UTF-8. Run '$python -S -E $SELF/agb install-config --config $config --print-statedir' to see why. Passing --statedir instead would install this instance against a config nothing here can read"
        fi
    fi
    # Moved from the `--instance` sugar block above; see the comment there for
    # why the move is safe and why `[ "$role" = mac ]` stayed behind.
    #
    # ⚠️ THREE MESSAGES, because this line now serves three different runs and
    # one string was false in two of them. It was written for "a SECOND instance
    # without --statedir"; making `--instance` mandatory turned it into the
    # message every FIRST install gets, where "a second machine shares no disk
    # with the first" describes a first machine that does not exist -- and
    # "$config carries none to adopt" is plainly wrong when `--config` was typed
    # and that file does carry one. The REASON is load-bearing here: it is what
    # tells the operator whether to pass the flag, fix the file, or drop
    # `--config`.
    if [ -n "$statedir" ]; then :
    elif [ "$config_given" = yes ]; then
        die "--instance $instance needs --statedir: nothing is adopted when --config is given, whatever that file carries. $config is this instance's own by your say-so and not by construction -- '--instance $instance --config <another instance's config>' is the same shape -- and adopting a statedir through it would point this instance's bridge at a disk its farm cannot see. Pass --statedir, or drop --config and let --instance derive it"
    elif [ -e "$config" ]; then
        die "--instance $instance needs --statedir: $config exists but carries no statedir of its own, and nothing is guessed here -- the fallback would be the default config's value, i.e. another machine's directory on a disk this instance's farm cannot see, with an empty farm reported for ever. Pass --statedir, or add a statedir line to that file"
    else
        die "--instance $instance needs --statedir: this is a NEW instance ($config does not exist yet), so there is no config to read one back out of. It is the farm-side directory, absolute, as spelled ON THE FARM -- a Mac-side ~ expands to the wrong home. A LATER re-install of this instance may drop the flag: it is adopted from that config"
    fi

    if [ "$dry" = yes ]; then
        say "dry run:  would copy $FILES to $dest"
        verify_tree "$python" "$SELF/agb"
        installed="$SELF/agb"
    else
        mkdir -p "$dest"
        for f in $FILES; do
            cp "$SELF/$f" "$dest/$f.tmp.$$"
            # 644, never executable: agb has no shebang and is always run as
            # `<python> -S -E agb`, so an executable bit would only invite the
            # one invocation that cannot pass the interpreter flags.
            chmod 644 "$dest/$f.tmp.$$"
            mv -f "$dest/$f.tmp.$$" "$dest/$f"
        done
        for f in $FILES; do
            [ -f "$dest/$f" ] || die "copy of $f to $dest did not land"
        done
        say "copied:   $FILES -> $dest"
        # The Mac-side helper. Not in $FILES: that is the three-file core the
        # tree is verified through, and agb-refresh is a convenience whose
        # absence breaks nothing.
        if [ -f "$SELF/agb-refresh" ]; then
            cp "$SELF/agb-refresh" "$dest/agb-refresh" \
                && chmod +x "$dest/agb-refresh" \
                && say "copied:   agb-refresh -> $dest"
        fi
        installed="$dest/agb"
        verify_tree "$python" "$installed"
    fi

    # `ssh_target_for` maps a record's hostname to an ssh alias through
    # `host_<name>`, and without it `agb pane` tries to ssh to a name this Mac
    # cannot resolve -- the row renders fine and simply refuses to open, which
    # is a confusing place to land. So the feed host's own hostname is read back
    # (`probe_farmhost`, above). Read-only, never fatal HERE, and skipped
    # entirely by --no-probe or by naming that host explicitly.
    #
    # `--instance auto` has usually asked already, in which case this costs no
    # second ssh and cannot disagree with the name that was derived from it:
    # one answer, two readers.
    if [ "$probe" = yes ] && [ -n "$feedhost" ]; then
        probe_farmhost
        case "$farmhost" in
            "")
                say "note:     could not read a hostname back from $feedhost."
                say "          If a row will not attach, add:  --host <its-hostname>=$feedhost" ;;
            *)  case " $hosts " in
                    *" $farmhost="*)
                        say "probed:   $feedhost is '$farmhost' (already mapped explicitly)" ;;
                    *)  hosts="$hosts $farmhost=$feedhost"
                        say "probed:   $feedhost is '$farmhost' -> host_$farmhost = $feedhost" ;;
                esac ;;
        esac
    fi

    # A second instance ADOPTS an existing mac-id rather than minting a second
    # one. The id names THIS MAC, not this connection: each instance's bridge
    # writes bridge/<mac-id>.beat inside its OWN statedir, and those statedirs
    # share no disk, so the same id in both is the truth and not a collision.
    #
    # ⚠️ The reason recorded here used to be "a fresh id would have to be
    # re-installed on every farm host of the new cluster", and that reason does
    # NOT hold -- kept, per the house rule, because a withdrawn reason that is
    # deleted gets re-proposed. The Mac instance is installed FIRST, and the
    # `install.sh farm --mac-id` hint below carries whatever id this instance
    # ended up with, so a new cluster's hosts would simply be installed with the
    # new id and nothing would need re-installing. The reason that does hold is
    # OPERATOR LEGIBILITY: one Mac, one id means `bridge/<mac-id>.beat` names
    # the same machine in every cluster, so `agb doctor` and `agb status-line`
    # are talking about one identity rather than N that have to be kept straight
    # -- and pasting the wrong one of N into a farm install produces a beat file
    # nobody writes, which reads exactly like a dead bridge.
    #
    # ⚠️ ITS OWN config first, the default's only as a fall-back. The adoption
    # fires on EVERY `--instance` run without `--mac-id`, i.e. on a routine
    # upgrade -- and `resolve_mac_id` gives `given` priority over `existing`, so
    # probing only the default config would REPLACE an id this instance already
    # recorded. Every farm host of that cluster still watches the old
    # `bridge/<old-id>.beat`, so `agb status-line` reads `bridge:DOWN` for ever
    # and `agb doctor` reports no beat, out of an install that changed nothing
    # anybody asked to change.
    #
    # Read back through `agb` itself -- a dry run that prints the id it resolved
    # -- and never by grepping `key = value` here: a second reader of the config
    # format is the sort that drifts from the first, which is why
    # --print-mac-id exists at all.
    #
    # `|| adopted=""` catches a NON-ZERO EXIT, not an empty line. On a config
    # with no mac_id (or none at all) `resolve_mac_id` RAISES rather than
    # answering empty, and under `set -e` an unguarded command substitution
    # would abort the install of the first instance on a Mac that never had a
    # default one -- exactly the case the fall-back to minting is for.
    # `[ -n "$instance" ]` used to lead this test and is gone: `--instance` is
    # required for this role, so it could only ever be true. Deleted rather than
    # left dead for the reason the two defaults above were -- a reader cannot
    # tell a condition that guards something from one that cannot fail.
    if [ -z "$macid" ]; then
        for known in "$config" "$DEFAULT_CONFIG"; do
            adopted=$(run_agb "$python" "$installed" install-config \
                              --config "$known" --dry-run --print-mac-id \
                          2>/dev/null) || adopted=""
            if [ -n "$adopted" ]; then
                shell_safe "the adopted mac-id" "$adopted"
                macid=$adopted
                say "mac-id:   adopted $macid from $known"
                break
            fi
        done
    fi

    # The config, and with it the mac-id: --print-mac-id puts the id alone on
    # stdout and the report on stderr, so this reads back the exact value that
    # was persisted instead of re-parsing `key = value` in sh.
    set -- install-config --config "$config" --print-mac-id \
           --feed-host "$feedhost" --agb-remote-path "$remotepath"
    if [ -n "$macid" ]; then set -- "$@" --mac-id "$macid"
    else set -- "$@" --generate-mac-id; fi
    if [ -n "$statedir" ]; then set -- "$@" --statedir "$statedir"; fi
    if [ -n "$remotepython" ]; then set -- "$@" --remote-python "$remotepython"; fi
    if [ -n "$jumphost" ]; then set -- "$@" --jump-host "$jumphost"; fi
    for h in $hosts; do set -- "$@" --host "$h"; done
    if [ "$dry" = yes ]; then set -- "$@" --dry-run; fi
    macid=$(run_agb "$python" "$installed" "$@") || die "install-config failed"
    shell_safe "the minted mac-id" "$macid"

    # The launchd job.
    [ -f "$SELF/$TEMPLATE" ] || die "missing $TEMPLATE beside install.sh"
    plist="$agentsdir/$label.plist"
    if [ "$dry" = yes ]; then
        say "dry run:  would render $TEMPLATE -> $plist"
    else
        mkdir -p "$agentsdir" "$logdir"
        tmp="$plist.tmp.$$"
        sed -e "s|@LABEL@|$(rep "$label")|g" \
            -e "s|@PYTHON@|$(rep "$python")|g" \
            -e "s|@AGB@|$(rep "$installed")|g" \
            -e "s|@LOGDIR@|$(rep "$logdir")|g" \
            -e "s|@PATH@|$(rep "$launchpath")|g" \
            -e "s|@CONFIG@|$(rep "$config")|g" \
            "$SELF/$TEMPLATE" > "$tmp"
        # Validate what was RENDERED, not what the template looked like. A
        # leftover placeholder is only one of the two ways this file can be
        # wrong: a substituted value containing a double hyphen, or an unescaped
        # `&`/`<`, produces a plist that parses as invalid XML, and a plist
        # launchd cannot parse is a job it silently never starts -- the exact
        # failure class this whole tool exists to remove.
        if grep -q '@[A-Z_][A-Z_]*@' "$tmp"; then
            rm -f "$tmp"
            die "$TEMPLATE still has unfilled placeholders after rendering; nothing installed"
        fi
        # `plutil` is macOS-only, so this is a strict addition where it exists
        # and a no-op where it does not (the Linux test box, where the grep
        # above is the tested path).
        if command -v plutil >/dev/null 2>&1; then
            plutil -lint "$tmp" >/dev/null 2>&1 || {
                plutil -lint "$tmp" || :
                rm -f "$tmp"
                die "the rendered $TEMPLATE is not a valid plist; nothing installed. Check the values for characters XML cannot carry"
            }
        fi
        mv -f "$tmp" "$plist"
        say "plist:    $plist"
        say "logs:     $logdir/bridge.log, $logdir/bridge.err.log"
    fi

    if [ "$load" = no ] || [ "$dry" = yes ]; then
        say "launchd:  not loaded (asked not to); load it with:"
        say "            launchctl bootstrap gui/\$(id -u) $plist"
    elif command -v launchctl >/dev/null 2>&1; then
        launchctl bootout "gui/$(id -u)/$label" >/dev/null 2>&1 || :
        if launchctl bootstrap "gui/$(id -u)" "$plist" >/dev/null 2>&1; then
            say "launchd:  bootstrapped $label"
        else
            launchctl unload "$plist" >/dev/null 2>&1 || :
            launchctl load -w "$plist" || die "launchctl could not load $plist"
            say "launchd:  loaded $label (legacy load -w)"
        fi
    else
        say "launchd:  launchctl not found -- $plist written but not loaded"
    fi

    [ "$wrapper" = yes ] && write_wrapper "$python" "$installed" \
        "${bindir:-$DEFAULT_BINDIR}"

    # `agb-refresh` needs to be reachable by name too. It is the recovery
    # command -- run when the sidebar has gone wrong and you are already
    # annoyed -- and one you had to type an absolute path for is one you will
    # not reach for. A symlink rather than a wrapper: unlike `agb`, it has a
    # shebang and is executable, so nothing needs generating.
    link_refresh "${bindir:-$DEFAULT_BINDIR}" "$dest/agb-refresh"

    command -v agtermctl >/dev/null 2>&1 \
        || say "note:     agtermctl is not on this PATH; the bridge needs it, and the launchd job looks for it in $launchpath"

    # The farm side. Never silently skipped: it is where the hooks and the
    # segment's config live, and the mac-id has to travel with it.
    remotedir=$(dirname "$remotepath")
    # ONE argv, built once, whether it is run over ssh or printed for a human to
    # paste. Printing a shorter command than the one --farm runs is how the two
    # halves come to disagree: --statedir used to be dropped from the printed
    # form, so a copy-paste install wrote hooks against the default statedir
    # while the bridge's ssh set AGB_STATEDIR to the configured one, and the
    # feed then reported an empty farm for ever. Same class as the mac-id
    # warning in role_farm, for the other value both halves must agree on.
    #
    # EVERY farm-side option the Mac was given travels, for the same reason:
    #
    #   --remote-python -> the farm's --python. The Mac records it as the
    #     interpreter its ssh runs `agb feed` under (constraint #14, an absolute
    #     path because `ssh host cmd` sources no profile). Dropped, the farm
    #     falls through to find_python() and BAKES A DIFFERENT INTERPRETER INTO
    #     THE HOOK COMMAND -- one runtime for the feed and another for every
    #     hook on the same box, decided by whatever is first on that host's
    #     PATH, and nothing later reports the divergence.
    #   --jump-host / --host. These are the ssh routes `agb prune --via-ssh`
    #     uses to reach the host that owns an entry -- the only path that can
    #     turn a heuristic into a proof. The Mac's config gets them; without
    #     this the FARM's config does not, and --via-ssh has no route.
    #     --host is the load-bearing half: `ssh_target_for` has NO other
    #     source. --jump-host travels for the farm that is a hub and spokes,
    #     where reaching one spoke from another does need the hub -- and
    #     `prune_jump_host` drops it whenever it names this host or the target,
    #     which is every case in the Mac -> box #2 -> #3 topology.
    #
    # ⚠️ `--statedir` is UNCONDITIONAL here, and the conditional it replaced was
    # dead code the moment `--instance` became required: every mac install now
    # has an instance, and `[ -n "$statedir" ] || die` above already refuses an
    # instance without a statedir -- so nothing reaching this line can have an
    # empty one. The farm role never builds this hint.
    #
    # ⚠️ The `install-config` argv above spells the same `if [ -n "$statedir" ]`
    # and is dead by exactly the same argument. It is left conditional
    # DELIBERATELY, so the inconsistency is a decision rather than an oversight:
    # nothing tests it, whereas this line's conditionality was the subject of a
    # named test, and a conditional that is asserted has to be either kept or
    # replaced rather than quietly left to rot. (`role_farm`'s two are a
    # different case again -- the farm role does not require `--statedir`, so
    # theirs are live and correctly stay conditional. That is the one property
    # this move could have broken and it used to be an argument rather than a
    # test: `test_install_sh_farm_still_installs_with_no_statedir_either`.)
    set -- sh "$remotedir/install.sh" farm --mac-id "$macid" \
           --statedir "$statedir"
    if [ -n "$remotepython" ]; then set -- "$@" --python "$remotepython"; fi
    if [ -n "$jumphost" ]; then set -- "$@" --jump-host "$jumphost"; fi
    for h in $hosts; do set -- "$@" --host "$h"; done
    if [ "$dry" = yes ]; then set -- "$@" --dry-run; fi
    if [ -n "$farm" ]; then
        say "farm:     ssh $farm $*"
        ssh "$farm" "$@" || die "the farm side failed; the Mac is configured, the farm is not"
    else
        say "next:     the farm side is NOT a no-op -- NFS shares the files, not"
        say "          the configuration. On EVERY farm host that runs agents:"
        say "            $*"
    fi
    say "mac-id:   $macid"
}

# ---------------------------------------------------------------------------
# farm
# ---------------------------------------------------------------------------

role_farm() {
    [ -n "$macid" ] || die "farm: --mac-id is required. The Mac mints it (install.sh mac prints it); generating a second one here would name a bridge/<mac-id>.beat that nothing writes, and 'agb status-line' would read bridge:DOWN for ever with a healthy bridge running"
    [ -n "$agbpath" ] || agbpath="$SELF/agb"
    absolute "--agb" "$agbpath"
    shell_safe "--agb" "$agbpath"
    [ -f "$agbpath" ] || die "$agbpath does not exist"

    say "agb install (farm) -- from $SELF"
    say "python:   $python"
    verify_tree "$python" "$agbpath"

    set -- install-config --config "$config" --mac-id "$macid"
    if [ -n "$statedir" ]; then set -- "$@" --statedir "$statedir"; fi
    if [ -n "$jumphost" ]; then set -- "$@" --jump-host "$jumphost"; fi
    for h in $hosts; do set -- "$@" --host "$h"; done
    if [ "$dry" = yes ]; then set -- "$@" --dry-run; fi
    run_agb "$python" "$agbpath" "$@" || die "install-config failed"

    if [ "$hooks" = no ]; then
        say "hooks:    skipped (--no-hooks)"
    else
        set -- install-hooks --python "$python" --agb "$agbpath"
        if [ -n "$statedir" ]; then set -- "$@" --statedir "$statedir"; fi
        if [ -n "$settings" ]; then set -- "$@" --settings "$settings"; fi
        if [ "$dry" = yes ]; then set -- "$@" --dry-run; fi
        run_agb "$python" "$agbpath" "$@" || die "install-hooks failed"
    fi

    [ "$wrapper" = yes ] && write_wrapper "$python" "$agbpath" \
        "${bindir:-$DEFAULT_BINDIR}"

    say "next:     add the segment to ~/.tmux.conf (see docs/tmux.md), and run"
    if [ "$wrapper" = yes ]; then
        say "            agb doctor"
    else
        say "            $python -S -E $agbpath doctor"
    fi
}

case "$role" in
    mac) role_mac ;;
    farm) role_farm ;;
esac
