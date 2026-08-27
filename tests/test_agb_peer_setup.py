"""agb-peer-setup -- the interactive roster builder.

The flows drive injected `read_line` and `Ctl` fakes rather than a terminal or
a real `agtermctl`, so a test can assert what was NOT prompted and what was NOT
written -- which is the shape most of these are.

Plan: docs/plans/20260826-agb-peer-setup-roster-builder.md
"""

import ast
import importlib.util
import io
import os
import sys

import pytest
from importlib.machinery import SourceFileLoader

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETUP_PATH = os.path.join(REPO_ROOT, "agb-peer-setup")
PEER_PATH = os.path.join(REPO_ROOT, "agb-peer")


class Out(object):
    def __init__(self):
        self.text = ""

    def write(self, s):
        self.text += s


# ---------------------------------------------------------------------------
# loading -- one module object, shared with agb-peer's own tests
# ---------------------------------------------------------------------------

def test_the_two_modules_share_one_PeerError(setup, peer):
    """⚠️ The named identity guard, and the reason the fixture moved.

    `load_peer` returns `sys.modules[PEER_MODULE]` when it is there. If the
    conftest fixture did not register it -- as the old copy in
    `test_agb_peer.py` did not -- this builds a second module object whose
    `PeerError` is a different class, so `except peer.PeerError` around a call
    into this script silently does not catch.
    """
    assert setup.load_peer() is peer
    assert setup.load_peer().PeerError is peer.PeerError
    assert setup.load_peer().RosterConflict is peer.RosterConflict


def test_the_sys_modules_key_agrees_with_conftest(setup):
    """A cross-file agreement with no single source of truth (invariant 14):
    the fixture and the script must spell the key identically."""
    from conftest import PEER_MODULE
    assert setup.PEER_MODULE == PEER_MODULE


def test_load_peer_resolves_what_the_flows_need(setup):
    """Non-vacuity first: assert the module actually loaded before asserting
    what is on it."""
    mod = setup.load_peer()
    assert mod is not None
    for name in ("parse_roster_text", "parse_participants", "match_sessions",
                 "render_roster_lines", "roster_bytes", "write_roster_file",
                 "write_draft_file", "RosterConflict", "Ctl", "PANE_KINDS"):
        assert hasattr(mod, name), name


def test_peer_path_resolves_through_a_symlink(setup, tmp_path):
    """⚠️ The documented install is a SYMLINK onto `$PATH` beside `agb-peer`.

    `dirname(__file__)` on an unresolved path looks in the symlink's directory
    and finds nothing. A test run from the checkout never notices, because
    there the two coincide -- which is exactly why this test builds the
    symlink case explicitly.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    link = str(bindir / "agb-peer-setup")
    os.symlink(SETUP_PATH, link)
    loader = SourceFileLoader("agb_peer_setup_linked", link)
    spec = importlib.util.spec_from_file_location("agb_peer_setup_linked",
                                                  link, loader=loader)
    linked = importlib.util.module_from_spec(spec)
    loader.exec_module(linked)
    assert linked.peer_path() == PEER_PATH
    assert os.path.exists(linked.peer_path())


def test_a_failed_load_leaves_nothing_registered(setup, tmp_path):
    """Without the `del`, the next call returns a half-initialised module as
    if it had loaded, so the second failure is a baffling AttributeError."""
    broken = tmp_path / "broken-peer"
    broken.write_text("this is not python(\n")
    key = setup.PEER_MODULE
    saved = sys.modules.pop(key, None)
    try:
        with pytest.raises(SyntaxError):
            setup.load_peer(str(broken))
        assert key not in sys.modules
    finally:
        if saved is not None:
            sys.modules[key] = saved


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------

def test_no_argument_is_a_usage_error_that_writes_nothing(setup, tmp_path):
    before = sorted(os.listdir(str(tmp_path)))
    out = Out()
    with pytest.raises(setup.SetupError) as err:
        setup.main([], out=out)
    assert err.value.code == 1
    assert "usage" in str(err.value)
    assert sorted(os.listdir(str(tmp_path))) == before
    assert out.text == ""


def test_version_is_answered_without_loading_the_sibling(setup):
    out = Out()
    assert setup.main(["--version"], out=out) == 0
    assert out.text.startswith("agb-peer-setup ")


def test_validate_takes_exactly_one_file(setup):
    with pytest.raises(setup.SetupError):
        setup.main(["validate"], out=Out())
    with pytest.raises(setup.SetupError):
        setup.main(["validate", "a", "b"], out=Out())


def test_an_unknown_flag_is_a_usage_error(setup):
    with pytest.raises(setup.SetupError) as err:
        setup.main(["--nope"], out=Out())
    assert "usage" in str(err.value)


def test_the_guard_names_every_class_that_can_reach_it(setup):
    """⚠️ Four classes escape into this script, not one, and each was a real
    escape: PeerError/RosterConflict from the sibling, AgbError from
    `agb_ops.parse_pane_args` on a row's arbitrary argv, and OSError/IOError
    from `agb.read_config`, which re-raises every errno but ENOENT/ENOTDIR.

    Structural because the alternative is provoking each one through a menu.
    """
    tree = ast.parse(io.open(SETUP_PATH, encoding="utf-8").read())

    def literal(node):
        # ast.Str on 3.6-3.7, ast.Constant from 3.8. This project's floor is
        # 3.6.8 and CI may be newer, so both spellings have to work.
        if isinstance(node, ast.Str):
            return node.s
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return None

    names = set()
    for node in ast.walk(tree):
        # `type(error).__name__ in (...)` -- the left side is an Attribute
        # over a Call, not a Call, which is what the first version of this
        # guard got wrong: it matched nothing and asserted over an empty set.
        if not isinstance(node, ast.Compare):
            continue
        for cmp_node in node.comparators:
            if isinstance(cmp_node, ast.Tuple):
                names.update(v for v in (literal(e) for e in cmp_node.elts)
                             if v is not None)
    assert names, "no class-name tuple found -- the walk is broken"
    for cls in ("PeerError", "RosterConflict", "AgbError", "OSError", "IOError"):
        assert cls in names, "%s can reach __main__ and is not handled" % (cls,)
