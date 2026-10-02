import sys
import io
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import local_worker

import pytest


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(local_worker, "ROOT", tmp_path)
    (tmp_path / "src").mkdir()
    return tmp_path


def write_file(path, content):
    path.write_text(content, encoding="utf-8")


def test_full_file(root):
    src = root / "src" / "example.py"
    write_file(src, "a\nb\nc\n")
    result = local_worker.read_context("src/example.py", tracked={"src/example.py"})
    assert result == "FILE src/example.py\na\nb\nc\n"


def test_range(root):
    src = root / "src" / "example.py"
    write_file(src, "a\nb\nc\n")
    result = local_worker.read_context("src/example.py:2:3", tracked={"src/example.py"})
    assert result == "FILE src/example.py:2:3\nb\nc\n"


@pytest.mark.parametrize("spec,message", [("0:1", "ascending"), ("3:2", "ascending"), ("1:9", "exceeds"), ("x:2", "integers")])
def test_bad_ranges(root, spec, message):
    src = root / "src" / "example.py"
    write_file(src, "a\nb\nc\n")
    with pytest.raises(ValueError, match=message):
        local_worker.read_context(f"src/example.py:{spec}", tracked={"src/example.py"})


def test_untracked(root):
    src = root / "src" / "example.py"
    write_file(src, "a\nb\nc\n")
    with pytest.raises(ValueError, match="Context must be a tracked project source file"):
        local_worker.read_context("src/example.py", tracked=set())


def test_symlink(root, tmp_path):
    # The resolved destination escapes the repository root even if the link is tracked.
    outside = root.parent / (root.name + "-outside.py")
    outside.write_text("outside\n")
    link = root / "link.py"
    try:
        link.symlink_to(outside)
        with pytest.raises(ValueError, match="Context must be a tracked"):
            local_worker.read_context("link.py", tracked={"link.py"})
    finally:
        outside.unlink()


def test_txt_extension(root):
    src = root / "src" / "example.txt"
    write_file(src, "a\nb\nc\n")
    with pytest.raises(ValueError, match="Unsupported context file type"):
        local_worker.read_context("src/example.txt", tracked={"src/example.txt"})


def test_large_source(root):
    src = root / "src" / "example.py"
    write_file(src, "a\n" * 14000)
    with pytest.raises(ValueError, match="Choose a smaller source file or use path:START:END"):
        local_worker.read_context("src/example.py", tracked={"src/example.py"})
    # small range accepted
    result = local_worker.read_context("src/example.py:1:1", tracked={"src/example.py"})
    assert result.startswith("FILE src/example.py:1:1\n")


def test_unicode_exceed(root):
    src = root / "src" / "example.py"
    line = "é" * 7000 + "\n"
    write_file(src, line * 2)
    with pytest.raises(ValueError, match="Source excerpt is too large; select fewer lines"):
        local_worker.read_context("src/example.py:1:1", tracked={"src/example.py"})


@pytest.fixture
def worker_env(root, monkeypatch):
    monkeypatch.setattr(local_worker, "DIRECTORY", root / ".local" / "worker")
    monkeypatch.setattr(local_worker.subprocess, "check_output", lambda *a, **kw: "src/example.py\n")
    calls = []
    monkeypatch.setattr(local_worker, "record_task", lambda *a, **kw: calls.append((a, kw)))
    return calls


def test_prompt_budget_rejects_before_inference(root, worker_env, monkeypatch):
    def forbidden(*args):
        pytest.fail("Oversized prompt must never reach Ollama")
    monkeypatch.setattr(local_worker.urllib.request, "build_opener", forbidden)
    monkeypatch.setattr(sys, "argv", ["local_worker.py", "é" * 6000])
    with pytest.raises(SystemExit) as error:
        local_worker.main()
    assert error.value.code == 2
    assert worker_env == []


@pytest.mark.parametrize("incomplete", [False, True])
def test_inference_limits_and_draft_status(root, worker_env, monkeypatch, incomplete):
    class Opener:
        def open(self, request, timeout):
            payload = json.loads(request.data)
            assert request.full_url == "http://127.0.0.1:11434/api/generate"
            assert payload["model"] == "gpt-oss:20b"
            assert payload["options"] == {"temperature": 0, "num_ctx": 16384, "num_predict": 2048}
            assert payload["keep_alive"] == "5m"
            assert payload["think"] == "low"
            assert timeout == 120
            return io.BytesIO(json.dumps({
                "response": "review this draft", "done": True,
                "done_reason": "length" if incomplete else "stop",
                "prompt_eval_count": 12, "eval_count": 34,
            }).encode())
    def opener(handler):
        assert handler.proxies == {}
        return Opener()
    monkeypatch.setattr(local_worker.urllib.request, "build_opener", opener)
    monkeypatch.setattr(sys, "argv", ["local_worker.py", "Small task", "--label", "Test draft"])
    if incomplete:
        with pytest.raises(SystemExit) as error:
            local_worker.main()
        assert error.value.code == 2
    else:
        local_worker.main()
    assert [call[0][2] for call in worker_env] == ["running", "incomplete" if incomplete else "done"]
    drafts = list((root / ".local" / "worker").glob("*.txt"))
    assert len(drafts) == 1
    assert drafts[0].read_text() == "review this draft\n"
    assert worker_env[-1][1]["output_tokens"] == 34
