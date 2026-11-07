"""Exercise actual PINE framing and deadline handling on a local Unix socket."""
import socket
import struct
import sys
import tempfile
import threading
import time
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))
from capture_boot import pine_probe


class PineTransportTests(unittest.TestCase):
    def run_server(self, handler):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "pine.sock"
        listener = socket.socket(socket.AF_UNIX)
        self.addCleanup(listener.close)
        listener.bind(str(path))
        listener.listen(1)
        errors = []
        def serve():
            try:
                with listener.accept()[0] as stream:
                    stream.settimeout(3)
                    handler(stream)
            except Exception as error:
                errors.append(error)
        worker = threading.Thread(target=serve)
        worker.start()
        return path, worker, errors

    @staticmethod
    def request(stream):
        def exact(count):
            value = b""
            while len(value) < count:
                part = stream.recv(count - len(value))
                if not part:
                    raise EOFError("client closed")
                value += part
            return value
        size = struct.unpack("<I", exact(4))[0]
        return exact(size - 4)

    @staticmethod
    def reply(stream, value):
        stream.sendall(struct.pack("<IBI", 9, 0, value))

    def test_bad_frame_closes_connection_before_later_scene_reads(self):
        requests = []
        def server(stream):
            requests.append(self.request(stream))
            self.reply(stream, 0)
            requests.append(self.request(stream))
            self.reply(stream, 0x70000C28)
            requests.append(self.request(stream))
            stream.sendall(struct.pack("<I", 4))
            self.assertEqual(stream.recv(1), b"")
        path, worker, errors = self.run_server(server)
        result = pine_probe(path, scene=True)
        worker.join(4)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(requests), 3)
        self.assertFalse(result["transport_usable_at_end"])
        self.assertFalse(result["scene_snapshot"]["complete"])
        self.assertEqual(result["scene_snapshot"]["completed_words"], 0)

    def test_deadline_covers_multiple_individually_slow_replies(self):
        def server(stream):
            self.request(stream)
            time.sleep(1.1)
            self.reply(stream, 0)
            self.request(stream)
            time.sleep(1.1)
            try:
                self.reply(stream, 0x70000C28)
            except BrokenPipeError:
                pass
        path, worker, errors = self.run_server(server)
        result = pine_probe(path, scene=True)
        worker.join(4)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertFalse(result["transport_usable_at_end"])
        self.assertIn("error", result)
        self.assertLess(result["probe_elapsed_seconds"], 2.3)
        self.assertNotIn("scene_snapshot", result)


if __name__ == "__main__":
    unittest.main()
