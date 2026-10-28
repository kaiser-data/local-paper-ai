from datetime import date
from pathlib import Path

from local_paper_ai.backup import (
    LocalTarget,
    RcloneTarget,
    export_name,
    sha256_file,
    sync,
)
from local_paper_ai.models import Analysis, Document, Verdict
from local_paper_ai.state import State
from tests.fakes import FakePaperless

DOC = Document(
    id=42,
    title="Steuerbescheid 2025/Einkommen",
    content="x",
    created=date(2026, 3, 14),
    correspondent="Finanzamt München",
)


def test_export_name_is_safe_and_unique() -> None:
    assert export_name(DOC) == "2026-03-14_Finanzamt-München_Steuerbescheid-2025-Einkommen_42.pdf"
    anon = DOC.model_copy(update={"correspondent": None, "title": "///"})
    assert export_name(anon) == "2026-03-14_Unbekannt_Unbenannt_42.pdf"


def test_local_target_put_and_verify(tmp_path: Path) -> None:
    src = tmp_path / "a.pdf"
    src.write_bytes(b"%PDF")
    target = LocalTarget("nas", tmp_path / "nas")
    target.put(src)
    assert target.verify(src, sha256_file(src))
    (tmp_path / "nas" / "a.pdf").write_bytes(b"tampered")
    assert not target.verify(src, sha256_file(src))


class FakeRunner:
    def __init__(self, fail: str = "") -> None:
        self.calls: list[list[str]] = []
        self.fail = fail

    def __call__(self, cmd: list[str]) -> int:
        self.calls.append(cmd)
        return 1 if cmd[1] == self.fail else 0


def test_rclone_commands(tmp_path: Path) -> None:
    src = tmp_path / "a.pdf"
    src.write_bytes(b"%PDF")
    runner = FakeRunner()
    RcloneTarget("cloud", "crypt:lpa", crypt=True, runner=runner).put(src)
    assert RcloneTarget("cloud", "crypt:lpa", crypt=True, runner=runner).verify(src, "x")
    assert RcloneTarget("gd", "gd:lpa", runner=runner).verify(src, "x")
    assert runner.calls == [
        ["rclone", "copyto", str(src), "crypt:lpa/a.pdf"],
        ["rclone", "cryptcheck", str(tmp_path), "crypt:lpa", "--one-way", "--include", "a.pdf"],
        ["rclone", "check", str(tmp_path), "gd:lpa", "--one-way", "--include", "a.pdf"],
    ]


def _state_with_doc(tmp_path: Path, store: FakePaperless) -> State:
    state = State(tmp_path / "s.db")
    store.add(DOC)
    state.save_analysis(DOC, Analysis(doc_id=42, verdict=Verdict(kind="may_shred")))
    return state


def test_sync_copies_verifies_and_is_incremental(tmp_path: Path) -> None:
    store = FakePaperless()
    state = _state_with_doc(tmp_path, store)
    targets = [LocalTarget("nas", tmp_path / "nas")]
    report = sync(store, state, targets, tmp_path / "export")
    assert (report.exported, report.copied, report.failed) == (1, 1, [])
    assert state.verified_copies(42) == 2
    again = sync(store, state, targets, tmp_path / "export")
    assert (again.exported, again.copied) == (0, 0)
    assert store.downloads == 1


def test_sync_reports_failing_target(tmp_path: Path) -> None:
    store = FakePaperless()
    state = _state_with_doc(tmp_path, store)
    target = RcloneTarget("cloud", "crypt:lpa", runner=FakeRunner(fail="copyto"))
    report = sync(store, state, [target], tmp_path / "export")
    assert report.failed == ["42 -> cloud"]
    assert state.verified_copies(42) == 1
