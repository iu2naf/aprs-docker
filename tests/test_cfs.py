#!/usr/bin/env python3
"""Test di integrazione per cfs.py (fake APRS-IS server)."""

import os
import signal
import socket
import subprocess
import sys
import time
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFS_SCRIPT = os.path.join(REPO_ROOT, "cfs.py")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class CFSTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port_igate = free_port()
        cls.port_viewer = free_port()
        env = dict(os.environ)
        env.update({
            "CFS_PORT_IGATE": str(cls.port_igate),
            "CFS_PORT_VIEWER": str(cls.port_viewer),
            "CFS_HEARTBEAT_INTERVAL": "2",
            "CFS_IDLE_TIMEOUT": "300",
            "CFS_LOG_STDOUT": "0",
        })
        cls.err_log = open("/tmp/cfs_test_err.log", "w")
        cls.proc = subprocess.Popen(
            [sys.executable, CFS_SCRIPT],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=cls.err_log,
        )
        cls.wait_port(cls.port_igate)
        cls.wait_port(cls.port_viewer)

    @classmethod
    def tearDownClass(cls):
        if cls.proc.poll() is None:
            cls.proc.send_signal(signal.SIGTERM)
            cls.proc.wait(timeout=5)
        cls.err_log.close()

    @staticmethod
    def wait_port(port, timeout=10):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), 0.5):
                    return
            except OSError:
                time.sleep(0.1)
        raise AssertionError(f"port {port} never became ready")

    def connect(self, port):
        sock = socket.create_connection(("127.0.0.1", port), 5)
        sock.settimeout(3)
        return sock

    @staticmethod
    def recv_until(sock, marker, timeout=5):
        data = b""
        sock.settimeout(timeout)
        deadline = time.time() + timeout
        while time.time() < deadline and marker not in data:
            try:
                chunk = sock.recv(1024)
            except socket.timeout:
                break
            if not chunk:
                break
            data += chunk
        return data

    def test_login_response(self):
        with self.connect(self.port_igate) as s:
            s.sendall(b"user CIVILE pass 12345\r\n")
            data = self.recv_until(s, b"logresp")
            self.assertIn(b"logresp CIVILE verified", data)

    def test_relay_14580_to_14581(self):
        with self.connect(self.port_viewer) as viewer:
            with self.connect(self.port_igate) as igate:
                packet = b"TESTCALL>APRS:test relay packet"
                igate.sendall(packet + b"\r\n")
                data = self.recv_until(viewer, b"test relay packet")
                self.assertIn(packet, data)

    def test_dedup(self):
        with self.connect(self.port_viewer) as viewer:
            with self.connect(self.port_igate) as igate:
                packet = b"TESTDUP>APRS:duplicate test packet"
                igate.sendall(packet + b"\r\n")
                self.recv_until(viewer, b"duplicate test packet")
                time.sleep(0.5)
                # Invia lo stesso pacchetto: non deve essere inoltrato di nuovo
                igate.sendall(packet + b"\r\n")
                time.sleep(1.0)
                viewer.settimeout(1.0)
                try:
                    extra = viewer.recv(1024)
                except socket.timeout:
                    extra = b""
                self.assertNotIn(b"duplicate test packet", extra)

    def test_heartbeat(self):
        with self.connect(self.port_viewer) as s:
            data = self.recv_until(s, b"aprsc 2.1.19-civile", timeout=10)
            self.assertIn(b"LoRa iGate Status", data)


if __name__ == "__main__":
    unittest.main()
