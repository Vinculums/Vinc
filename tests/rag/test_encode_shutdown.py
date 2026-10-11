"""합성 HTTP 요청의 종료 계약과 Linux PID 1의 실제 SIGTERM 전달을 검증한다."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import socketserver
import subprocess
import sys
import threading
import time
import urllib.request

import pytest


ROOT = Path(__file__).resolve().parents[2]


def load_worker():
    spec = importlib.util.spec_from_file_location(
        "shutdown_worker", ROOT / "vinc_rag/embed/worker.py")
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    worker.ENCODE_BIND = "127.0.0.1"
    worker.ENCODE_TOKEN = ""
    worker.REMOTE_ENCODE = ""
    return worker


@pytest.fixture
def worker():
    return load_worker()


def wait_until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition did not become true before the deadline")


def call(port, texts=None):
    data = None if texts is None else json.dumps({"texts": texts, "raw": True}).encode()
    path = "health" if texts is None else "encode"
    request = urllib.request.Request(f"http://127.0.0.1:{port}/{path}", data=data)
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def listener_closed(port):
    with socket.socket() as client:
        client.settimeout(0.1)
        return client.connect_ex(("127.0.0.1", port)) != 0


def test_idle_close_keeps_model_lazy_and_closes_listener(worker):
    emb = worker._Embedder(prepare_split=False)
    service = worker._start_encode(emb, 0)
    try:
        assert call(service.port)["loaded"] is False
    finally:
        assert service.close()
    assert emb._sess is None
    assert not service.thread.is_alive()
    assert listener_closed(service.port)


def test_close_finishes_every_batch_and_delivers_complete_response(worker):
    entered, release = threading.Event(), threading.Event()
    emb = worker._Embedder(prepare_split=False)
    model = object()
    emb._sess = model
    batches = []

    def encode_batch(texts, **kwargs):
        batches.append(list(texts))
        if len(batches) == 1:
            entered.set()
            assert release.wait(10)
        assert emb._sess is model
        return [[float(text), 1.0] for text in texts]

    emb._encode_batch = encode_batch
    texts = [str(i) for i in range(worker.EMBED_BATCH * 2 + 1)]
    service = worker._start_encode(emb, 0)
    with ThreadPoolExecutor(max_workers=2) as pool:
        response = pool.submit(call, service.port, texts)
        try:
            assert entered.wait(5)
            closed = pool.submit(service.close, 5)
            wait_until(lambda: listener_closed(service.port))
            assert not closed.done(), "shutdown returned before the held batch finished"
            release.set()
            assert response.result(timeout=5)["vectors"] == [[float(t), 1.0] for t in texts]
            assert closed.result(timeout=5)
            assert len(batches) == 3
            assert emb._sess is model
        finally:
            release.set()
            service.close()


def test_partial_body_is_bounded_and_reported_unfinished(worker, capsys):
    service = worker._start_encode(worker._Embedder(prepare_split=False), 0)
    client = socket.create_connection(("127.0.0.1", service.port), timeout=2)
    try:
        client.sendall(b"POST /encode HTTP/1.0\r\nContent-Length: 500\r\n\r\n{")
        wait_until(lambda: service.server._active == 1)
        started = time.monotonic()
        assert service.close(timeout=0.8) is False
        assert time.monotonic() - started < 2
        assert listener_closed(service.port)
        assert "pending=1" in capsys.readouterr().out
    finally:
        client.close()
        wait_until(lambda: service.server._active == 0)


def test_close_waits_for_response_write_not_only_inference(worker, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original_write = socketserver._SocketWriter.write

    def held_write(stream, data):
        if b'"vectors"' in data:
            entered.set()
            assert release.wait(10)
        return original_write(stream, data)

    monkeypatch.setattr(socketserver._SocketWriter, "write", held_write)
    emb = worker._Embedder(prepare_split=False)
    emb._encode_batch = lambda texts, **kwargs: [[0.0, 1.0] for _ in texts]
    service = worker._start_encode(emb, 0)
    with ThreadPoolExecutor(max_workers=2) as pool:
        response = pool.submit(call, service.port, ["synthetic"])
        try:
            assert entered.wait(5)
            closed = pool.submit(service.close, 5)
            wait_until(lambda: listener_closed(service.port))
            assert not closed.done(), "inference completion must not finish the drain"
            release.set()
            assert response.result(timeout=5)["vectors"] == [[0.0, 1.0]]
            assert closed.result(timeout=5)
        finally:
            release.set()
            service.close()


def test_close_waits_for_all_concurrent_requests(worker):
    entered = threading.Barrier(3)
    release = threading.Event()
    emb = worker._Embedder(prepare_split=False)

    def encode_batch(texts, **kwargs):
        entered.wait(timeout=5)
        assert release.wait(10)
        return [[float(t), 1.0] for t in texts]

    emb._encode_batch = encode_batch
    service = worker._start_encode(emb, 0)
    with ThreadPoolExecutor(max_workers=3) as pool:
        responses = [pool.submit(call, service.port, [str(i)]) for i in range(2)]
        try:
            entered.wait(timeout=5)
            closed = pool.submit(service.close, 5)
            wait_until(lambda: listener_closed(service.port))
            assert not closed.done()
            assert service.server._active == 2
            release.set()
            for i, response in enumerate(responses):
                assert response.result(timeout=5)["vectors"] == [[float(i), 1.0]]
            assert closed.result(timeout=5)
        finally:
            release.set()
            service.close()


def test_disconnected_client_releases_request_tracking(worker):
    entered, release = threading.Event(), threading.Event()
    emb = worker._Embedder(prepare_split=False)

    def encode_batch(texts, **kwargs):
        entered.set()
        assert release.wait(5)
        return [[0.0, 1.0] for _ in texts]

    emb._encode_batch = encode_batch
    service = worker._start_encode(emb, 0)
    client = socket.create_connection(("127.0.0.1", service.port), timeout=2)
    body = b'{"texts":["synthetic"]}'
    try:
        client.sendall(b"POST /encode HTTP/1.0\r\nContent-Length: "
                       + str(len(body)).encode() + b"\r\n\r\n" + body)
        assert entered.wait(5)
        client.shutdown(socket.SHUT_RDWR)
        client.close()
        release.set()
        assert service.close(timeout=3)
        assert service.server._active == 0
    finally:
        release.set()
        client.close()
        service.close()


def test_request_thread_start_failure_does_not_leak_count(worker, monkeypatch):
    service = worker._start_encode(worker._Embedder(prepare_split=False), 0)
    try:
        with monkeypatch.context() as patch:
            def fail_start(thread):
                raise RuntimeError("synthetic thread start failure")
            patch.setattr(threading.Thread, "start", fail_start)
            with pytest.raises(RuntimeError, match="synthetic"):
                service.server.process_request(object(), ("127.0.0.1", 1))
        assert service.server._active == 0
        assert service.close()
    finally:
        service.close()


def test_shutdown_wait_itself_is_inside_the_deadline(worker, monkeypatch):
    from http.server import BaseHTTPRequestHandler
    server = worker._EncodeServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    release = threading.Event()
    # 수신 루프와 shutdown 대기가 둘 다 멈춘 상태를 재현한다.
    receiver = threading.Thread(target=release.wait, args=(5,), daemon=True)
    receiver.start()
    service = worker._EncodeService(server, receiver)
    monkeypatch.setattr(server, "shutdown", lambda: release.wait(5))
    try:
        started = time.monotonic()
        assert service.close(timeout=0.2) is False
        assert time.monotonic() - started < 1.5
        assert listener_closed(service.port)
    finally:
        release.set()
        service.thread.join(3)
    assert not service.thread.is_alive()


def test_startup_log_failure_closes_started_listener(worker, monkeypatch):
    import builtins
    original_print = builtins.print
    original_server = worker._EncodeServer
    servers = []

    def remember_server(*args):
        server = original_server(*args)
        servers.append(server)
        return server

    def fail_banner(*args, **kwargs):
        if args and str(args[0]).startswith("[embed] encode service"):
            raise OSError("synthetic logging failure")
        return original_print(*args, **kwargs)

    monkeypatch.setattr(worker, "_EncodeServer", remember_server)
    monkeypatch.setattr(builtins, "print", fail_banner)
    with pytest.raises(OSError, match="synthetic logging"):
        worker._start_encode(worker._Embedder(prepare_split=False), 0)
    assert len(servers) == 1
    assert servers[0].socket.fileno() == -1


def test_startup_failure_restores_signal_handlers(worker, monkeypatch):
    before = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}

    def fail_start(*args):
        raise OSError("synthetic bind failure")

    monkeypatch.setattr(worker, "_start_encode", fail_start)
    with pytest.raises(OSError, match="synthetic"):
        worker._serve_only(worker._Embedder(prepare_split=False), 0)
    assert {sig: signal.getsignal(sig) for sig in before} == before


def publish(path, text):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(text)
    temporary.replace(path)


def child_worker(folder):
    """모델/DB/외부 접속 없이 실제 main과 HTTP 서버를 실행한다."""
    worker = load_worker()
    from http.server import ThreadingHTTPServer
    original_process_request = ThreadingHTTPServer.process_request

    def record_accept(server, request, address):
        original_process_request(server, request, address)
        (folder / "accepted").touch()

    ThreadingHTTPServer.process_request = record_accept
    original_embedder = worker._Embedder

    class SyntheticEmbedder(original_embedder):
        def _encode_batch(self, texts, **kwargs):
            self._sess = object()
            if texts[0] == "0":
                publish(folder / "entered", str(threading.get_native_id()))
                while not (folder / "release").exists():
                    time.sleep(0.01)
            return [[float(text), 1.0] for text in texts]

    worker._Embedder = SyntheticEmbedder
    # 수정 전/후 모두 실제 main을 통과시킨다. port=0은 테스트에서만 치환한다.
    name = "_start_encode" if hasattr(worker, "_start_encode") else "serve_encode"
    original_start = getattr(worker, name)

    def start(emb, port):
        service = original_start(emb, 0)
        port = service.port if hasattr(service, "port") else service
        publish(folder / "ready", json.dumps({"pid": os.getpid(), "port": port}))
        return service

    setattr(worker, name, start)
    sys.argv = ["worker", "--serve-only", "--encode-port", "8791"]
    raise SystemExit(worker.main())


@pytest.fixture
def pid1(tmp_path):
    if os.environ.get("VINC_TEST_ENCODER_PID1") != "on":
        pytest.skip("run the dedicated Linux PID namespace CI step")
    assert sys.platform == "linux", "PID 1 proof requires Linux"
    assert shutil.which("unshare"), "the required PID namespace test cannot silently skip"
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("VINC_", "MEMORY_", "PYTHON"))}
    log = tmp_path / "worker.log"
    with log.open("w") as output:
        process = subprocess.Popen(
            ["unshare", "--pid", "--fork", "--mount-proc", "--kill-child=KILL",
             sys.executable, "-B", str(Path(__file__).resolve()), "--worker", str(tmp_path)],
            cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT,
            start_new_session=True)
        try:
            wait_until(lambda: (tmp_path / "ready").exists() or process.poll() is not None, 15)
            assert process.poll() is None, log.read_text()
            ready = json.loads((tmp_path / "ready").read_text())
            assert ready["pid"] == 1, "the worker must really be PID 1"
            children = Path(f"/proc/{process.pid}/task/{process.pid}/children").read_text().split()
            assert len(children) == 1, children
            yield process, int(children[0]), ready["port"], tmp_path, log
        finally:
            # 테스트가 직접 만든 격리 프로세스 그룹만 정리한다.
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_pid1_idle_signal_exits_cleanly_without_loading_model(pid1, sig):
    process, host_pid, port, folder, log = pid1
    assert call(port)["loaded"] is False
    started = time.monotonic()
    os.kill(host_pid, sig)
    assert process.wait(timeout=5) == 0, log.read_text()
    assert time.monotonic() - started < 5
    assert "pending=0 listener_stopped=True" in log.read_text()
    print(f"PID 1 {sig.name}: idle exit in {time.monotonic() - started:.3f}s")


@pytest.mark.parametrize("delivery", ["process", "http_thread"])
def test_pid1_signal_drains_inflight_batches_and_complete_response(pid1, delivery):
    process, host_pid, port, folder, log = pid1
    texts = [str(i) for i in range(17)]
    with ThreadPoolExecutor(max_workers=1) as pool:
        response = pool.submit(call, port, texts)
        try:
            wait_until(lambda: (folder / "entered").exists())
            started = time.monotonic()
            if delivery == "process":
                os.kill(host_pid, signal.SIGTERM)
            else:
                import ctypes
                # 자식 namespace TID를 /proc의 host TID로 매핑한다.
                namespace_tid = int((folder / "entered").read_text())
                tids = [p for p in Path(f"/proc/{host_pid}/task").iterdir()
                        if any(line.startswith("NSpid:") and int(line.split()[-1]) == namespace_tid
                               for line in (p / "status").read_text().splitlines())]
                assert len(tids) == 1
                libc = ctypes.CDLL(None, use_errno=True)
                assert libc.tgkill(host_pid, int(tids[0].name), signal.SIGTERM) == 0
            wait_until(lambda: listener_closed(port))
            assert process.poll() is None, "process exited while its response was blocked"
            os.kill(host_pid, signal.SIGTERM)
            os.kill(host_pid, signal.SIGINT)
            (folder / "release").touch()
            assert response.result(timeout=5)["vectors"] == [[float(t), 1.0] for t in texts]
            assert process.wait(timeout=5) == 0, log.read_text()
            print(f"PID 1 {delivery}: complete 17-vector response and exit in "
                  f"{time.monotonic() - started:.3f}s")
        finally:
            (folder / "release").touch()


@pytest.mark.parametrize("blocked", ["body", "inference"])
def test_pid1_unfinished_request_exits_nonzero_within_platform_grace(pid1, blocked):
    process, host_pid, port, folder, log = pid1
    client = socket.create_connection(("127.0.0.1", port), timeout=2)
    body = b'{"texts":["0"]}'
    try:
        if blocked == "body":
            client.sendall(b"POST /encode HTTP/1.0\r\nContent-Length: 500\r\n\r\n{")
            # 수신 스레드가 이 TCP 연결을 요청 스레드에 넘겼는지 확인한다.
            wait_until(lambda: (folder / "accepted").exists())
        else:
            client.sendall(b"POST /encode HTTP/1.0\r\nContent-Length: "
                           + str(len(body)).encode() + b"\r\n\r\n" + body)
            wait_until(lambda: (folder / "entered").exists())
        started = time.monotonic()
        os.kill(host_pid, signal.SIGTERM)
        wait_until(lambda: listener_closed(port))
        os.kill(host_pid, signal.SIGTERM)
        assert process.wait(timeout=28) == 1, log.read_text()
        elapsed = time.monotonic() - started
        assert 24 <= elapsed < 28, elapsed
        assert "pending=1 listener_stopped=True" in log.read_text()
        print(f"PID 1 blocked {blocked}: unfinished request reported, exit=1 in {elapsed:.3f}s")
    finally:
        client.close()


if __name__ == "__main__":
    assert sys.argv[1] == "--worker"
    child_worker(Path(sys.argv[2]))
