"""서버 전용 경로의 불필요한 토크나이저 적재와 로컬 분할 계약을 검증한다."""
import importlib.util
from pathlib import Path
import sys
import types

import pytest


@pytest.fixture
def model(monkeypatch, tmp_path):
    path = Path(__file__).resolve().parents[2] / "vinc_rag/embed/worker.py"
    spec = importlib.util.spec_from_file_location("split_tokenizer_worker", path)
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    tokenizers = []

    class Tokenizer:
        def __init__(self):
            self.truncate = None
            tokenizers.append(self)

        @classmethod
        def from_file(cls, path):
            return cls()

        def enable_truncation(self, maximum):
            self.truncate = maximum

        def enable_padding(self, **kwargs):
            pass

        def token_to_id(self, token):
            return 1

        def id_to_token(self, token_id):
            return "<pad>"

        def encode(self, text):
            length = min(len(text), self.truncate) if self.truncate else len(text)
            return types.SimpleNamespace(ids=range(length))

    monkeypatch.setitem(sys.modules, "numpy", types.SimpleNamespace())
    monkeypatch.setitem(sys.modules, "tokenizers", types.SimpleNamespace(Tokenizer=Tokenizer))
    monkeypatch.setitem(sys.modules, "huggingface_hub", types.SimpleNamespace(
        hf_hub_download=lambda *a, **k: "synthetic-tokenizer.json"))
    monkeypatch.setitem(sys.modules, "onnxruntime", types.SimpleNamespace(
        SessionOptions=types.SimpleNamespace,
        InferenceSession=lambda *a, **k: types.SimpleNamespace(
            get_inputs=lambda: [types.SimpleNamespace(name="input_ids")])) )
    monkeypatch.setattr(worker, "CACHE", str(tmp_path))
    monkeypatch.setattr(worker, "_resolve_model", lambda *a: "synthetic.onnx")
    monkeypatch.setattr(worker, "REMOTE_ENCODE", "")
    monkeypatch.setattr(worker, "ENCODE_BIND", "127.0.0.1")
    return worker, tokenizers


def test_default_local_embedder_keeps_untruncated_split_counter(model):
    worker, tokenizers = model
    emb = worker._Embedder()
    emb._load()
    assert len(tokenizers) == 2
    assert len(emb._tok.encode("x" * 900).ids) == worker.MAX_TOKENS
    assert worker._SPLIT.tok("x" * 900) == 900


def test_encoding_without_split_preloads_only_inference_tokenizer(model):
    worker, tokenizers = model
    emb = worker._Embedder(prepare_split=False)
    emb._load()
    emb._load()
    assert len(tokenizers) == 1
    assert emb._tok is tokenizers[0]
    assert worker._SPLIT.tok is None
    assert worker._SPLIT.tried is False


def test_encoding_without_split_preserves_existing_counter(model):
    worker, tokenizers = model
    counter = lambda text: len(text)
    worker._SPLIT.tok = counter
    worker._SPLIT.tried = True
    worker._Embedder(prepare_split=False)._load()
    assert len(tokenizers) == 1
    assert worker._SPLIT.tok is counter
    assert worker._SPLIT.tried is True


def test_serve_only_main_loads_one_tokenizer_on_first_request(model, monkeypatch):
    worker, tokenizers = model
    monkeypatch.setattr(sys, "argv", ["worker", "--serve-only", "--encode-port", "8791"])
    entered = []

    def serve(emb, port):
        assert not tokenizers
        emb._load()
        entered.append(port)

    def stop(seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(worker, "serve_encode", serve)
    monkeypatch.setattr(worker.time, "sleep", stop)
    assert worker.main() == 0
    assert entered == [8791]
    assert len(tokenizers) == 1
    assert worker._SPLIT.tok is None
