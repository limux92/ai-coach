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


@pytest.fixture
def ollama_api(monkeypatch):
    requests = []
    responses = [
        {"thinking": {"values": ["low", "medium", "xhigh"], "default": "xhigh"}},
        {"response": "review this draft", "done": True, "done_reason": "stop",
         "prompt_eval_count": 12, "eval_count": 34},
    ]

    class Opener:
        def open(self, request, timeout):
            requests.append((request.full_url, json.loads(request.data) if request.data is not None else None, timeout))
            result = responses[len(requests) - 1]
            if isinstance(result, Exception):
                raise result
            return io.BytesIO(json.dumps(result).encode())

    def opener(handler):
        assert handler.proxies == {}
        return Opener()

    monkeypatch.setattr(local_worker.urllib.request, "build_opener", opener)
    return requests, responses


@pytest.mark.parametrize("answer,done,reason,status", [
    ("review this draft", True, "stop", "done"),
    ("review this draft", True, "length", "incomplete"),
    ("", True, "stop", "incomplete"),
    ("review this draft", False, "stop", "incomplete"),
])
def test_inference_limits_and_draft_status(root, worker_env, monkeypatch, ollama_api, answer, done, reason, status):
    requests, responses = ollama_api
    responses[1].update(response=answer, done=done, done_reason=reason)
    monkeypatch.setattr(sys, "argv", ["local_worker.py", "Small task", "--label", "Test draft"])
    if status == "incomplete":
        with pytest.raises(SystemExit) as error:
            local_worker.main()
        assert error.value.code == 2
    else:
        local_worker.main()
    assert requests == [
        ("http://127.0.0.1:11434/api/show", {"model": "qwen3.8:27b-q4_K_M"}, 120),
        ("http://127.0.0.1:11434/api/generate", {
            "model": "qwen3.8:27b-q4_K_M", "prompt": "Small task\n\n",
            "system": local_worker.PRESETS["draft"], "stream": False, "keep_alive": "5m",
            "options": {"num_ctx": 8192, "num_predict": 2048}, "think": "low",
        }, 120),
    ]
    assert [call[0][2] for call in worker_env] == ["running", status]
    assert all(call[0][0] == "qwen" and call[1]["model"] == "qwen3.8:27b-q4_K_M" for call in worker_env)
    drafts = list((root / ".local" / "worker").glob("*.txt"))
    assert len(drafts) == 1
    assert drafts[0].read_text() == answer + "\n"
    assert worker_env[-1][1]["output_tokens"] == 34


@pytest.mark.parametrize("reasoning", ["low", "medium", "xhigh"])
def test_explicit_reasoning_levels(worker_env, monkeypatch, ollama_api, reasoning):
    requests, _ = ollama_api
    monkeypatch.setattr(sys, "argv", ["local_worker.py", "Small task", "--reasoning", reasoning])
    local_worker.main()
    assert len(requests) == 2
    assert requests[1][1]["think"] == reasoning


def test_gpt_oss_high_reasoning_rejected_before_requests(worker_env, monkeypatch, ollama_api):
    requests, _ = ollama_api
    monkeypatch.setattr(sys, "argv", ["local_worker.py", "Small task", "--reasoning", "high"])
    with pytest.raises(SystemExit) as error:
        local_worker.main()
    assert error.value.code == 2
    assert requests == []
    assert worker_env == []


@pytest.mark.parametrize("metadata", [
    {}, {"thinking": None}, {"thinking": {"values": "low"}},
    {"thinking": {"values": [True, False]}},
    {"thinking": {"values": ["medium", "xhigh"]}},
    {"error": "model not found"}, None,
])
def test_unsupported_or_missing_thinking_metadata_never_generates(root, worker_env, monkeypatch, ollama_api, metadata):
    requests, responses = ollama_api
    responses[0] = metadata
    monkeypatch.setattr(sys, "argv", ["local_worker.py", "Small task"])
    with pytest.raises(SystemExit, match="does not advertise thinking level 'low'"):
        local_worker.main()
    assert requests == [("http://127.0.0.1:11434/api/show", {"model": "qwen3.8:27b-q4_K_M"}, 120)]
    assert [call[0][2] for call in worker_env] == ["running", "failed"]
    assert not (root / ".local" / "worker").exists()


def test_missing_model_never_generates_or_falls_back(root, worker_env, monkeypatch, ollama_api):
    requests, responses = ollama_api
    responses[0] = local_worker.urllib.error.HTTPError(
        "http://127.0.0.1:11434/api/show", 404, "Not Found", None, None,
    )
    monkeypatch.setattr(sys, "argv", ["local_worker.py", "Small task"])
    with pytest.raises(SystemExit, match="Check `ollama list`"):
        local_worker.main()
    assert requests == [("http://127.0.0.1:11434/api/show", {"model": "qwen3.8:27b-q4_K_M"}, 120)]
    assert [call[0][2] for call in worker_env] == ["running", "failed"]
    assert not (root / ".local" / "worker").exists()


@pytest.fixture
def legacy_qwen_metadata():
    return {"modelfile": "FROM /local/model\nRENDERER qwen3.8\nPARSER qwen3.5\n",
            "capabilities": ["completion", "vision", "tools", "thinking"]}


@pytest.mark.parametrize("reasoning,native", [("low", "low"), ("medium", "medium"), ("xhigh", "high")])
def test_verified_legacy_renderer_maps_reasoning_explicitly(worker_env, monkeypatch, ollama_api,
                                                          legacy_qwen_metadata, capsys, reasoning, native):
    requests, responses = ollama_api
    responses[0] = legacy_qwen_metadata
    responses.insert(1, {"version": "0.32.15"})
    monkeypatch.setattr(sys, "argv", ["local_worker.py", "Small task", "--reasoning", reasoning])
    local_worker.main()
    assert len(requests) == 3
    assert requests[0] == ("http://127.0.0.1:11434/api/show", {"model": "qwen3.8:27b-q4_K_M"}, 120)
    assert requests[1] == ("http://127.0.0.1:11434/api/version", None, 120)
    assert requests[2][0] == "http://127.0.0.1:11434/api/generate"
    assert requests[2][1]["model"] == "qwen3.8:27b-q4_K_M"
    assert requests[2][1]["think"] == native
    assert f"native think={native!r} for requested {reasoning}" in capsys.readouterr().out
    assert [call[0][2] for call in worker_env] == ["running", "done"]


@pytest.mark.parametrize("version", ["0.32.14", "0.32.16", "0.32.15-rc0", None])
def test_unverified_runtime_never_generates(root, worker_env, monkeypatch, ollama_api, legacy_qwen_metadata, version):
    requests, responses = ollama_api
    responses[0] = legacy_qwen_metadata
    responses.insert(1, {"version": version})
    monkeypatch.setattr(sys, "argv", ["local_worker.py", "Small task"])
    with pytest.raises(SystemExit, match="no verified compatibility"):
        local_worker.main()
    assert [request[0] for request in requests] == [
        "http://127.0.0.1:11434/api/show", "http://127.0.0.1:11434/api/version",
    ]
    assert [call[0][2] for call in worker_env] == ["running", "failed"]
    assert not (root / ".local" / "worker").exists()


@pytest.mark.parametrize("modelfile", [
    "RENDERER qwen3.5\n", "RENDERER qwen3.8-other\n", "# RENDERER qwen3.8\n",
    "RENDERER qwen3.8\nRENDERER qwen3.5\n", None,
])
def test_unverified_renderer_never_generates(root, worker_env, monkeypatch, ollama_api, legacy_qwen_metadata, modelfile):
    requests, responses = ollama_api
    responses[0] = {**legacy_qwen_metadata, "modelfile": modelfile}
    monkeypatch.setattr(sys, "argv", ["local_worker.py", "Small task"])
    with pytest.raises(SystemExit, match="no verified compatibility"):
        local_worker.main()
    assert len(requests) == 1
    assert [call[0][2] for call in worker_env] == ["running", "failed"]
    assert not (root / ".local" / "worker").exists()


@pytest.mark.parametrize("thinking,accepted", [
    ({"values": ["low", "medium", "xhigh"]}, True),
    ({"values": ["low", "medium", "high"]}, False),
    ({"values": []}, False), ({}, False), (None, False),
])
def test_explicit_metadata_takes_precedence_over_legacy_compatibility(worker_env, monkeypatch, ollama_api,
                                                                    legacy_qwen_metadata, capsys, thinking, accepted):
    requests, responses = ollama_api
    responses[0] = {**legacy_qwen_metadata, "thinking": thinking}
    monkeypatch.setattr(sys, "argv", ["local_worker.py", "Small task", "--reasoning", "xhigh"])
    if accepted:
        local_worker.main()
        assert len(requests) == 2
        assert requests[1][1]["think"] == "xhigh"
    else:
        with pytest.raises(SystemExit, match="no verified compatibility"):
            local_worker.main()
        assert len(requests) == 1
    assert "http://127.0.0.1:11434/api/version" not in [request[0] for request in requests]
    assert "Compatibility:" not in capsys.readouterr().out
