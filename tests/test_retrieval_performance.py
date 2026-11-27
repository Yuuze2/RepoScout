"""Legacy migration stays incremental without losing version or line provenance."""

from contextlib import contextmanager

from reposcout.retrieval import RepositoryIndex
from reposcout.settings import Settings
from reposcout.storage import Store


def add_evidence(store, run, repository, commit, path, content, start=1):
    return store.evidence(
        run,
        repository=repository,
        kind="file",
        commit=commit,
        path=path,
        start_line=start,
        end_line=start,
        url="https://github.com/fixture/demo",
        summary="fixture",
        content=f"{start}: {content}",
    )


def test_complete_documents_do_not_open_one_connection_per_evidence(tmp_path, monkeypatch):
    store = Store(Settings(home=tmp_path))
    run = store.create("query", "offline")
    index = RepositoryIndex(store)
    for i in range(30):
        path = f"file{i}.py"
        index.add("fixture/demo", "a" * 40, path, "checkpoint")
        add_evidence(store, run, "fixture/demo", "a" * 40, path, "checkpoint")
    connections = []
    connect = store.connect

    @contextmanager
    def counted():
        connections.append(1)
        with connect() as db:
            yield db

    monkeypatch.setattr(store, "connect", counted)
    index.legacy(run, "fixture/demo", "a" * 40)
    assert len(connections) <= 2, "already indexed evidence must not cause per-file connections"


def test_late_legacy_evidence_is_found_and_scope_is_preserved(tmp_path):
    store = Store(Settings(home=tmp_path))
    run = store.create("query", "offline")
    other_run = store.create("other", "offline")
    repository, commit = "fixture/demo", "a" * 40
    store.put(run, "repository", repository, {"commit": commit})
    index = RepositoryIndex(store)
    assert index.search(run, repository, "checkpoint")["hits"] == []
    add_evidence(store, run, repository, commit, "old.py", "checkpoint", start=101)
    add_evidence(store, other_run, repository, commit, "foreign-run.py", "checkpoint")
    add_evidence(store, run, "other/repo", commit, "foreign-repo.py", "checkpoint")
    add_evidence(store, run, repository, "b" * 40, "foreign-commit.py", "checkpoint")
    hit = index.search(run, repository, "checkpoint")["hits"]
    assert [(row["path"], row["start_line"], row["content"]) for row in hit] == [
        ("old.py", 101, "101: checkpoint")
    ]
    index.add(repository, commit, "old.py", "complete version replaces legacy excerpt")
    assert index.search(run, repository, "checkpoint")["hits"] == []
