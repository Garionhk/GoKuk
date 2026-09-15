import json

import _protocol as proto


def test_parse_line():
    assert proto.parse_line('{"type": "stage", "id": "sing"}') == {"type": "stage", "id": "sing"}
    assert proto.parse_line("warning: something") is None
    assert proto.parse_line("{not json") is None
    assert proto.parse_line('{"no_type": 1}') is None


def test_classify_error():
    assert proto.classify_error("torch.OutOfMemoryError: CUDA out of memory") == "out_of_memory"
    assert proto.classify_error("RuntimeError: CUDA is not available") == "no_gpu"
    assert proto.classify_error("OSError: [Errno 2] No such file or directory: 'x'") == "missing_files"
    assert proto.classify_error("ValueError: odd") == "unknown"


def test_cancel_file(tmp_path, monkeypatch):
    job = tmp_path / "job.json"
    job.write_text(json.dumps({"a": 1}))
    proto.cancel_path(job).write_text("stale")          # left over from an earlier run
    monkeypatch.setattr(proto, "_cancel_seen", False)
    assert proto.read_job([str(job)]) == {"a": 1}
    assert not proto.cancel_path(job).exists()           # read_job clears stale cancels
    monkeypatch.setattr(proto, "_cancel_checked", 0.0)
    assert proto.cancelled() is False
    proto.cancel_path(job).write_text("cancel")
    monkeypatch.setattr(proto, "_cancel_checked", 0.0)
    assert proto.cancelled() is True
    monkeypatch.setattr(proto, "_cancel_seen", False)
    monkeypatch.setattr(proto, "_cancel_file", None)
