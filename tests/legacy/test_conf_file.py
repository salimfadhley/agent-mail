"""Config discovery and identity inference for the CLI (mission cli-primary-client).

These build real temporary git repositories rather than mocking ``git``, because the
whole point of the inference is that it agrees with what git actually reports.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent_mailbox_old.conf_file import (
    CONFIG_NAME,
    describe_missing,
    find_config,
    git_root,
    infer_agent,
    infer_identity,
    infer_project,
    infer_role,
    normalize,
    write_config,
)


def _git_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    return root


def test_normalize_produces_address_tokens() -> None:
    assert normalize("Agent Inbox") == "agent_inbox"
    assert normalize("goldberg-casework") == "goldberg_casework"
    assert normalize("  Mixed_Case  ") == "mixed_case"


def test_project_is_the_git_repository_name(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "my-project")
    inferred = infer_project(repo)
    assert inferred.value == "my_project"
    assert "show-toplevel" in inferred.source


def test_sibling_repos_are_separate_projects(tmp_path: Path) -> None:
    """One repo is one project — the shared parent directory is not a project.

    Earlier guidance told agents to use an "umbrella" name spanning sibling repos. That
    was wrong and has been retracted; this test pins the corrected rule.
    """
    parent = tmp_path / "project_goldberg"
    system = _git_repo(parent / "goldberg_system")
    casework = _git_repo(parent / "goldberg_casework")

    assert infer_project(system).value == "goldberg_system"
    assert infer_project(casework).value == "goldberg_casework"


def test_project_inferred_from_a_nested_directory(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "deep-repo")
    nested = repo / "src" / "pkg"
    nested.mkdir(parents=True)
    assert infer_project(nested).value == "deep_repo"


def test_generic_repo_name_falls_back_to_the_parent(tmp_path: Path) -> None:
    owner = tmp_path / "acme"
    repo = _git_repo(owner / "main")
    inferred = infer_project(repo)
    assert inferred.value == "acme"
    assert "generic" in inferred.source


def test_project_outside_a_git_repo_uses_the_directory(tmp_path: Path) -> None:
    plain = tmp_path / "loose_folder"
    plain.mkdir()
    inferred = infer_project(plain)
    assert inferred.value == "loose_folder"
    assert "not a git repository" in inferred.source


def test_agent_inferred_from_engine_marker() -> None:
    assert infer_agent({"CLAUDECODE": "1"}).value == "claude"
    assert infer_agent({"CODEX_SANDBOX": "seatbelt"}).value == "codex"


def test_unknown_engine_is_reported_not_guessed() -> None:
    """Guessing an engine would let two agents collide on one inbox."""
    inferred = infer_agent({})
    assert inferred.value is None
    assert not inferred.known


def test_role_defaults_to_agent() -> None:
    assert infer_role().value == "agent"


def test_identity_address_is_three_part(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "widgets")
    identity = infer_identity(repo, {"CLAUDECODE": "1"})
    assert identity.address == "widgets/claude/agent"
    assert identity.complete


def test_find_config_walks_up_to_the_git_root(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "walker")
    (repo / CONFIG_NAME).write_text('hub = "http://example.invalid"\n')
    nested = repo / "a" / "b"
    nested.mkdir(parents=True)
    assert find_config(nested) == repo / CONFIG_NAME


def test_find_config_stops_at_the_repository_boundary(tmp_path: Path) -> None:
    """A config above the repo belongs to something else and must not be adopted."""
    outer = tmp_path / "outside"
    outer.mkdir()
    (outer / CONFIG_NAME).write_text('hub = "http://not-ours.invalid"\n')
    repo = _git_repo(outer / "inner_repo")
    assert find_config(repo) is None


def test_find_config_returns_none_when_absent(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "bare")
    assert find_config(repo) is None


def test_describe_missing_explains_itself(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "explainer")
    identity = infer_identity(repo, {"CLAUDECODE": "1"})
    text = describe_missing(identity, repo, hub="http://hub.invalid")

    assert CONFIG_NAME in text
    assert "explainer" in text and "claude" in text and "agent" in text
    assert "show-toplevel" in text  # provenance, not just the value
    assert "agent-inbox init --hub http://hub.invalid" in text


def test_describe_missing_tells_an_unknown_engine_to_name_itself(
    tmp_path: Path,
) -> None:
    repo = _git_repo(tmp_path / "nameless")
    text = describe_missing(infer_identity(repo, {}), repo)
    assert "--agent <name>" in text


def test_write_config_round_trips(tmp_path: Path) -> None:
    import tomllib

    target = tmp_path / CONFIG_NAME
    write_config(target, "http://hub.invalid", "widgets", "claude", "agent")
    loaded = tomllib.loads(target.read_text())

    assert loaded == {
        "hub": "http://hub.invalid",
        "project": "widgets",
        "agent_id": "claude",
        "role": "agent",
    }


def test_write_config_refuses_to_clobber(tmp_path: Path) -> None:
    target = tmp_path / CONFIG_NAME
    write_config(target, "http://a.invalid", "p", "a", "agent")
    with pytest.raises(FileExistsError, match="--force"):
        write_config(target, "http://b.invalid", "p", "a", "agent")

    write_config(target, "http://b.invalid", "p", "a", "agent", force=True)
    assert "b.invalid" in target.read_text()


def test_git_root_is_none_outside_a_repo(tmp_path: Path) -> None:
    plain = tmp_path / "nowhere"
    plain.mkdir()
    assert git_root(plain) is None
