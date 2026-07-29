#!/bin/sh
# agbridge installer -- run once per machine, and it has TWO sides.
#
#   sh install.sh mac  --feed-host <target> --agb-remote-path <farm path>
#   sh install.sh farm --mac-id <id>
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
DEFAULT_CONFIG="$HOME/.config/agbridge/config"
# A LaunchAgent inherits almost no PATH, and the bridge shells out to
# `agtermctl` and `ssh`. Homebrew first (both architectures), then the system.
DEFAULT_LAUNCH_PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

die() { printf 'install.sh: %s\n' "$*" >&2; exit 1; }
say() { printf '%s\n' "$*"; }

usage() {
    cat >&2 <<'EOF'
usage: install.sh mac  --feed-host <ssh-target> --agb-remote-path <farm path> [options]
       install.sh farm --mac-id <id> [options]

mac -- copy agb, agb_mac and agb_ops, write ~/.config/agbridge/config with a
       freshly minted mac_id, render the launchd plist and load it.

  --feed-host <target>       ssh target of the farm box running `agb feed`  (required)
  --agb-remote-path <path>   absolute path of `agb` on the farm             (required)
  --statedir <path>          farm-side statedir            (default: agb's own default)
  --remote-python <path>     absolute farm-side interpreter        (default /bin/python3)
  --jump-host <target>       ssh jump host for machine #3
  --host <name>=<target>     ssh target for a record's host; repeatable
  --mac-id <id>              adopt an existing mac-id instead of minting one
  --dest <dir>               where the three files go       (default ~/.local/lib/agbridge)
  --python <path>            absolute interpreter to run the bridge with
  --config <path>            config file                (default ~/.config/agbridge/config)
  --launch-agents <dir>      (default ~/Library/LaunchAgents)
  --log-dir <dir>            (default ~/Library/Logs/agbridge)
  --launch-path <PATH>       PATH given to the launchd job
  --label <name>             launchd label                          (default com.agbridge)
  --farm <ssh-target>        run the farm side over ssh with the minted mac-id
  --no-load                  write the plist but do not load it
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

# Prove the tree at $2 works, through all three files, before anything is
# configured against it. `version` alone would pass with agb_mac and agb_ops
# missing entirely -- which is precisely the failure this checks for.
verify_tree() {
    vpython=$1
    vagb=$2
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
    say "verified: $answer at $vagb, with agb_mac and agb_ops beside it"
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
load=yes; hooks=yes; dry=no

need() { [ "$1" -gt 1 ] || die "$2 needs a value"; }

while [ $# -gt 0 ]; do
    case "$1" in
        --dest) need $# "$1"; dest=$2; shift 2 ;;
        --python) need $# "$1"; python=$2; shift 2 ;;
        --config) need $# "$1"; config=$2; shift 2 ;;
        --statedir) need $# "$1"; statedir=$2; shift 2 ;;
        --mac-id) need $# "$1"; macid=$2; shift 2 ;;
        --feed-host) need $# "$1"; feedhost=$2; shift 2 ;;
        --agb-remote-path) need $# "$1"; remotepath=$2; shift 2 ;;
        --remote-python) need $# "$1"; remotepython=$2; shift 2 ;;
        --jump-host) need $# "$1"; jumphost=$2; shift 2 ;;
        --host) need $# "$1"
                shell_safe "--host" "$2"
                case "$2" in *=*) ;; *) die "--host wants <name>=<ssh-target>" ;; esac
                hosts="$hosts $2"; shift 2 ;;
        --launch-agents) need $# "$1"; agentsdir=$2; shift 2 ;;
        --log-dir) need $# "$1"; logdir=$2; shift 2 ;;
        --launch-path) need $# "$1"; launchpath=$2; shift 2 ;;
        --label) need $# "$1"; label=$2; shift 2 ;;
        --farm) need $# "$1"; farm=$2; shift 2 ;;
        --settings) need $# "$1"; settings=$2; shift 2 ;;
        --agb) need $# "$1"; agbpath=$2; shift 2 ;;
        --no-load) load=no; shift ;;
        --no-hooks) hooks=no; shift ;;
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
[ -n "$config" ] || config="$DEFAULT_CONFIG"
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
    [ -n "$feedhost" ] || die "mac: --feed-host is required (the bridge cannot invent the ssh target, and one that silently never connects is the failure this tool exists to remove)"
    [ -n "$remotepath" ] || die "mac: --agb-remote-path is required: the absolute path of agb ON THE FARM, e.g. /opt/agbridge/agb"
    shell_safe "--feed-host" "$feedhost"
    shell_safe "--agb-remote-path" "$remotepath"
    absolute "--agb-remote-path" "$remotepath"
    if [ -n "$remotepython" ]; then shell_safe "--remote-python" "$remotepython"
                                    absolute "--remote-python" "$remotepython"; fi
    if [ -n "$farm" ]; then shell_safe "--farm" "$farm"; fi
    [ -n "$dest" ] || dest="$DEFAULT_DEST"
    [ -n "$agentsdir" ] || agentsdir="$DEFAULT_AGENTS"
    [ -n "$logdir" ] || logdir="$DEFAULT_LOGDIR"
    [ -n "$launchpath" ] || launchpath="$DEFAULT_LAUNCH_PATH"
    [ -n "$label" ] || label="$DEFAULT_LABEL"

    say "agb install (mac) -- from $SELF"
    say "python:   $python"

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
        installed="$dest/agb"
        verify_tree "$python" "$installed"
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
    set -- sh "$remotedir/install.sh" farm --mac-id "$macid"
    if [ -n "$statedir" ]; then set -- "$@" --statedir "$statedir"; fi
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

    say "next:     add the segment to ~/.tmux.conf (see docs/tmux.md), and run"
    say "            $python -S -E $agbpath doctor"
}

case "$role" in
    mac) role_mac ;;
    farm) role_farm ;;
esac
