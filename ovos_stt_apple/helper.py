"""Persistent client for apple-stt-helper --serve (JSON lines)."""
import json
import queue
import subprocess
import threading
import uuid

from ovos_utils.log import LOG


class HelperError(RuntimeError):
    pass


class PersistentHelper:
    def __init__(self, path: str, startup_timeout: float = 20.0):
        self.path = path
        self.startup_timeout = startup_timeout
        self._proc = None
        self._q = None
        self._lock = threading.Lock()

    # -- process management -------------------------------------------------
    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _spawn(self):
        self._kill()
        LOG.info(f"Starting {self.path} --serve")
        self._proc = subprocess.Popen(
            [self.path, "--serve"], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1)
        self._q = queue.Queue()
        threading.Thread(target=self._reader, args=(self._proc, self._q),
                         daemon=True).start()
        try:
            msg = self._q.get(timeout=self.startup_timeout)
        except queue.Empty:
            self._kill()
            raise HelperError("helper did not become ready (permission prompt pending?)")
        if not msg or not msg.get("ready"):
            auth = msg.get("auth") if msg else None
            self._kill()
            raise HelperError(
                f"speech recognition not authorized (status {auth}); grant "
                "access under System Settings > Privacy & Security > Speech Recognition")

    @staticmethod
    def _reader(proc, q):
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    q.put(json.loads(line))
                except ValueError:
                    LOG.debug(f"helper non-json output: {line}")
        finally:
            q.put(None)

    def _kill(self):
        if self._proc is not None:
            try:
                self._proc.stdin.close()
            except Exception:
                pass
            try:
                self._proc.kill()
                self._proc.wait(timeout=2)
            except Exception:
                pass
        self._proc = None

    def close(self):
        with self._lock:
            self._kill()

    # -- requests -------------------------------------------------------------
    def request(self, payload: dict, timeout: float = 30.0) -> dict:
        with self._lock:
            if not self._alive():
                self._spawn()
            rid = uuid.uuid4().hex
            payload = dict(payload, id=rid)
            try:
                self._proc.stdin.write(json.dumps(payload) + "\n")
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError) as e:
                self._kill()
                raise HelperError(f"helper pipe broken: {e}")
            import time
            deadline = time.monotonic() + timeout + 5
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._kill()
                    raise HelperError("helper request timed out")
                try:
                    msg = self._q.get(timeout=remaining)
                except queue.Empty:
                    self._kill()
                    raise HelperError("helper request timed out")
                if msg is None:
                    self._kill()
                    raise HelperError("helper exited unexpectedly")
                if msg.get("id") == rid:
                    return msg
