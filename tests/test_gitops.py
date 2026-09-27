"""C3 — git del episodio (ADR-005)."""
from __future__ import annotations

import pytest

from swe_agent.gitops import EpisodeGit, GitError

from .conftest import git


def test_start_crea_rama_propia_y_no_toca_la_del_usuario(make_repo):
    repo = make_repo()
    main_sha = git(repo, "rev-parse", "main").strip()
    g = EpisodeGit(repo)
    branch, baseline = g.start()
    assert branch.startswith("agent/") and baseline == main_sha
    (repo / "calc.py").write_text("x = 1\n", newline="\n")
    sha = g.commit_if_dirty("paso 0: edit")
    assert sha and sha != baseline
    assert git(repo, "rev-parse", "main").strip() == main_sha        # main intacta
    assert "swe-agent" in git(repo, "log", "-1", "--format=%an")


def test_dos_episodios_seguidos_no_chocan_de_nombre(make_repo):
    repo = make_repo()
    b1, _ = EpisodeGit(repo).start()
    b2, _ = EpisodeGit(repo).start()
    assert b1 != b2


def test_commit_if_dirty_sin_cambios_e_ignora_pycache(make_repo):
    repo = make_repo()
    g = EpisodeGit(repo)
    g.start()
    (repo / "__pycache__").mkdir()
    (repo / "__pycache__" / "calc.cpython-314.pyc").write_bytes(b"\x00")
    assert g.commit_if_dirty("nada") is None


def test_patch_incluye_lo_no_commiteado_y_archivos_nuevos(make_repo):
    repo = make_repo()
    g = EpisodeGit(repo)
    g.start()
    (repo / "nuevo.py").write_text("y = 2\n", newline="\n")
    p = g.patch()
    assert "+y = 2" in p and "nuevo.py" in p


def test_repo_sucio_o_sin_git_no_arranca(make_repo, tmp_path):
    repo = make_repo()
    (repo / "sin_trackear.txt").write_text("x")
    with pytest.raises(GitError, match="sin commitear"):
        EpisodeGit(repo).start()
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(GitError, match="no es un repositorio"):
        EpisodeGit(plain).start()
