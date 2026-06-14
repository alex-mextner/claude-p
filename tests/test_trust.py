"""Unit tests for ensure_workspace_trusted (deterministic headless auto-trust).

claude-p drives the interactive claude TUI under a PTY; in an untrusted cwd it
blocks on the "Do you trust this folder?" gate. The legacy keystroke path
(maybe_accept_workspace_trust_prompt) presses Enter only when it happens to spot
the prompt in the terminal transcript, which is timing-dependent. ensure_workspace_trusted
seeds the trust flag in ~/.claude.json directly before claude launches so the
prompt never fires. These tests pin its contract; HOME is isolated to a tmp dir
so the real config is never touched.
"""
from __future__ import annotations

import json
import os

from claude_p.cli import ensure_workspace_trusted


def _seed_home(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


def test_seeds_trust_for_absent_project(monkeypatch, tmp_path):
    home = _seed_home(monkeypatch, tmp_path)
    cfg = home / ".claude.json"
    cfg.write_text(json.dumps({"projects": {}}))
    proj = tmp_path / "repo"
    proj.mkdir()
    assert ensure_workspace_trusted(str(proj)) is True
    data = json.loads(cfg.read_text())
    key = os.path.realpath(str(proj))
    assert data["projects"][key]["hasTrustDialogAccepted"] is True
    assert data["projects"][key]["hasCompletedProjectOnboarding"] is True


def test_idempotent_when_already_trusted(monkeypatch, tmp_path):
    home = _seed_home(monkeypatch, tmp_path)
    proj = tmp_path / "repo"
    proj.mkdir()
    key = os.path.realpath(str(proj))
    cfg = home / ".claude.json"
    cfg.write_text(json.dumps({"projects": {key: {
        "hasTrustDialogAccepted": True, "hasCompletedProjectOnboarding": True, "sentinel": 1}}}))
    before = cfg.read_text()
    assert ensure_workspace_trusted(str(proj)) is True
    # already fully trusted -> no rewrite (never clobbers a concurrent claude session)
    assert cfg.read_text() == before


def test_reseeds_when_trusted_but_onboarding_missing_or_false(monkeypatch, tmp_path):
    home = _seed_home(monkeypatch, tmp_path)
    proj = tmp_path / "repo"
    proj.mkdir()
    key = os.path.realpath(str(proj))
    cfg = home / ".claude.json"
    # trust accepted but onboarding still false: the fast path must NOT early-return
    # on hasTrustDialogAccepted alone, or claude still blocks on the onboarding gate.
    cfg.write_text(json.dumps({"projects": {key: {
        "hasTrustDialogAccepted": True, "hasCompletedProjectOnboarding": False}}}))
    assert ensure_workspace_trusted(str(proj)) is True
    entry = json.loads(cfg.read_text())["projects"][key]
    assert entry["hasTrustDialogAccepted"] is True
    assert entry["hasCompletedProjectOnboarding"] is True


def test_non_dict_projects_is_left_untouched(monkeypatch, tmp_path):
    home = _seed_home(monkeypatch, tmp_path)
    cfg = home / ".claude.json"
    # a present-but-wrong-shaped "projects" must be treated as best-effort failure,
    # never silently overwritten with {} (that would drop whatever it held).
    cfg.write_text(json.dumps({"projects": "corrupt", "numStartups": 7}))
    proj = tmp_path / "repo"
    proj.mkdir()
    assert ensure_workspace_trusted(str(proj)) is False
    assert json.loads(cfg.read_text()) == {"projects": "corrupt", "numStartups": 7}


def test_key_is_the_resolved_real_path(monkeypatch, tmp_path):
    home = _seed_home(monkeypatch, tmp_path)
    cfg = home / ".claude.json"
    cfg.write_text(json.dumps({"projects": {}}))
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    os.symlink(real, link)
    assert ensure_workspace_trusted(str(link)) is True
    data = json.loads(cfg.read_text())
    # claude canonicalises cwd, so the entry must be keyed by the real path
    assert data["projects"][os.path.realpath(str(real))]["hasTrustDialogAccepted"] is True
    assert str(link) not in data["projects"]


def test_missing_config_is_noop(monkeypatch, tmp_path):
    home = _seed_home(monkeypatch, tmp_path)
    proj = tmp_path / "repo"
    proj.mkdir()
    # best-effort: never fabricate a config the user did not have
    assert ensure_workspace_trusted(str(proj)) is False
    assert not (home / ".claude.json").exists()


def test_garbage_config_is_left_untouched(monkeypatch, tmp_path):
    home = _seed_home(monkeypatch, tmp_path)
    cfg = home / ".claude.json"
    cfg.write_text("{ this is not json")
    proj = tmp_path / "repo"
    proj.mkdir()
    assert ensure_workspace_trusted(str(proj)) is False
    assert cfg.read_text() == "{ this is not json"


def test_preserves_other_projects_and_top_level_keys(monkeypatch, tmp_path):
    home = _seed_home(monkeypatch, tmp_path)
    cfg = home / ".claude.json"
    other = os.path.realpath(str(tmp_path / "other"))
    cfg.write_text(json.dumps({
        "numStartups": 42,
        "projects": {other: {"hasTrustDialogAccepted": True, "lastCost": 0.5}},
    }))
    proj = tmp_path / "repo"
    proj.mkdir()
    assert ensure_workspace_trusted(str(proj)) is True
    data = json.loads(cfg.read_text())
    assert data["numStartups"] == 42  # top-level state untouched
    assert data["projects"][other] == {"hasTrustDialogAccepted": True, "lastCost": 0.5}
    assert data["projects"][os.path.realpath(str(proj))]["hasTrustDialogAccepted"] is True


def test_forces_onboarding_true_on_partial_untrusted_entry(monkeypatch, tmp_path):
    home = _seed_home(monkeypatch, tmp_path)
    cfg = home / ".claude.json"
    proj = tmp_path / "repo"
    proj.mkdir()
    key = os.path.realpath(str(proj))
    # a prior blocked attempt can leave a partial NOT-trusted entry with
    # onboarding=false; seeding must force BOTH true or claude still gates
    cfg.write_text(json.dumps({
        "projects": {key: {"hasTrustDialogAccepted": False, "hasCompletedProjectOnboarding": False}},
    }))
    assert ensure_workspace_trusted(str(proj)) is True
    entry = json.loads(cfg.read_text())["projects"][key]
    assert entry["hasTrustDialogAccepted"] is True
    assert entry["hasCompletedProjectOnboarding"] is True
