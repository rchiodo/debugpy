# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See LICENSE in the project root
# for license information.

import argparse
import json
import os
from pathlib import Path
import queue
import socket
import ssl
import subprocess
import sys
import threading
import time

import debugpy

ROOT = Path(__file__).resolve().parent / "_output"
HOST = "localhost"
CONNECT_CODE = """
import debugpy
import sys

debugpy.log_to(sys.argv[2])
debugpy.configure(subProcess=False)
print("CONNECT_START", flush=True)
debugpy.connect(("localhost", int(sys.argv[1])))
print("CONNECT_RETURNED", flush=True)
print("ATTACHED=" + str(debugpy.is_client_connected()), flush=True)
"""


def _debuggee_command(port, log_dir, cli=False):
    command = [sys.executable, "-X", "frozen_modules=off"]
    if cli:
        return command + [
            "-m",
            "debugpy",
            "--connect",
            f"{HOST}:{port}",
            "--configure-subProcess",
            "False",
            "--log-to",
            str(log_dir),
            "-c",
            "import debugpy; print('DEBUGGEE_RAN', flush=True); "
            "print('ATTACHED=' + str(debugpy.is_client_connected()), flush=True)",
        ]
    return command + ["-c", CONNECT_CODE, str(port), str(log_dir)]


def _finish(process, timeout=8):
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        stdout, stderr = process.communicate(timeout=5)
    return {
        "exit_code": process.returncode,
        "timed_out": timed_out,
        "stdout": stdout,
        "stderr": stderr,
    }


def _tls_listener(context, results, label, listener):
    with listener:
        try:
            sock, peer = listener.accept()
        except TimeoutError as exc:
            results.put(
                {
                    "case": label,
                    "tls_handshake": "not attempted",
                    "error": f"{type(exc).__name__}: no incoming TCP connection",
                }
            )
            return
        with sock:
            sock.settimeout(4)
            try:
                first_bytes = sock.recv(256, socket.MSG_PEEK)
            except TimeoutError:
                first_bytes = b""
            result = {
                "case": label,
                "peer": list(peer),
                "initial_bytes_hex": first_bytes.hex(),
                "initial_bytes_text": first_bytes.decode("ascii", errors="replace"),
            }
            try:
                with context.wrap_socket(sock, server_side=True) as connection:
                    result["tls_handshake"] = "succeeded"
                    result["tls_version"] = connection.version()
                    data = connection.recv(256)
                    result["application_bytes"] = data.decode("ascii")
                    connection.sendall(b"TLS echo: " + data)
            except (ssl.SSLError, TimeoutError, ConnectionError) as exc:
                result["tls_handshake"] = "failed"
                result["error"] = f"{type(exc).__name__}: {exc}"
            results.put(result)


def _run_tls_case(context, label, tls_control=False, cli=False):
    results = queue.Queue()
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(15)
    port = listener.getsockname()[1]
    thread = threading.Thread(
        target=_tls_listener, args=(context, results, label, listener)
    )
    thread.start()
    process = None
    try:
        if tls_control:
            client_context = ssl.create_default_context(cafile=str(ROOT / "cert.pem"))
            with socket.create_connection((HOST, port), timeout=5) as raw:
                with client_context.wrap_socket(raw, server_hostname=HOST) as client:
                    client.sendall(b"control")
                    assert client.recv(256) == b"TLS echo: control"
        else:
            log_dir = ROOT / label
            log_dir.mkdir(exist_ok=True)
            process = subprocess.Popen(
                _debuggee_command(port, log_dir, cli),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        result = results.get(timeout=18)
        result["endpoint"] = f"tcps://{HOST}:{port}"
        if process is not None:
            result["debugpy_process"] = _finish(process)
        expected = "succeeded" if tls_control else "failed"
        assert result["tls_handshake"] == expected, result
        if not tls_control:
            output = result["debugpy_process"]["stdout"]
            assert "CONNECT_RETURNED" not in output, result
            assert "DEBUGGEE_RAN" not in output, result
        return result
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
        thread.join(timeout=6)
        assert not thread.is_alive(), "TLS listener did not stop"


class _DapClient:
    def __init__(self, sock):
        self.sock = sock
        self.buffer = b""
        self.sequence = 0
        self.messages = []

    def send(self, command, arguments):
        self.sequence += 1
        body = json.dumps(
            {
                "seq": self.sequence,
                "type": "request",
                "command": command,
                "arguments": arguments,
            }
        ).encode("utf-8")
        self.sock.sendall(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
        return self.sequence

    def receive(self):
        while b"\r\n\r\n" not in self.buffer:
            self._read()
        headers, self.buffer = self.buffer.split(b"\r\n\r\n", 1)
        length = int(headers.decode("ascii").split(": ", 1)[1])
        while len(self.buffer) < length:
            self._read()
        body, self.buffer = self.buffer[:length], self.buffer[length:]
        message = json.loads(body)
        self.messages.append(message)
        return message

    def _read(self):
        data = self.sock.recv(65536)
        if not data:
            raise EOFError("DAP connection closed")
        self.buffer += data

    def wait(self, predicate):
        for _ in range(100):
            message = self.receive()
            if predicate(message):
                return message
        raise RuntimeError("Expected DAP message was not received")


def _plain_attach_control(cli=False):
    endpoints_file = ROOT / "adapter-endpoints.json"
    log_dir = ROOT / ("plaintext-cli-control" if cli else "plaintext-control")
    log_dir.mkdir(exist_ok=True)
    environment = dict(os.environ, DEBUGPY_ADAPTER_ENDPOINTS=str(endpoints_file))
    adapter = subprocess.Popen(
        [
            sys.executable,
            "-X",
            "frozen_modules=off",
            "-m",
            "debugpy.adapter",
            "--host",
            "127.0.0.1",
            "--port",
            "0",
            "--log-dir",
            str(log_dir),
        ],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    debuggee = None
    try:
        deadline = time.monotonic() + 15
        while not endpoints_file.exists():
            if adapter.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError(f"Adapter startup failed: {_finish(adapter)}")
            time.sleep(0.05)
        endpoints = json.loads(endpoints_file.read_text())
        client = endpoints["client"]
        with socket.create_connection(
            (client["host"], client["port"]), timeout=10
        ) as sock:
            dap = _DapClient(sock)
            initialize = dap.send(
                "initialize",
                {
                    "clientID": "tcps-experiment",
                    "adapterID": "python",
                    "pathFormat": "path",
                    "linesStartAt1": True,
                    "columnsStartAt1": True,
                    "supportsConfigurationDoneRequest": True,
                },
            )
            response = dap.wait(lambda m: m.get("request_seq") == initialize)
            assert response["success"], response
            attach = dap.send(
                "attach",
                {
                    "listen": {"host": "127.0.0.1", "port": 0},
                    "subProcess": False,
                },
            )
            waiting = dap.wait(lambda m: m.get("event") == "debugpyWaitingForServer")
            port = waiting["body"]["port"]
            debuggee = subprocess.Popen(
                _debuggee_command(port, log_dir, cli),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            dap.wait(lambda m: m.get("event") == "initialized")
            configuration = dap.send("configurationDone", {})
            responses = {}
            while len(responses) < 2:
                message = dap.receive()
                request_sequence = message.get("request_seq")
                if request_sequence in (attach, configuration):
                    assert message["success"], message
                    responses[request_sequence] = message
            process_result = _finish(debuggee)
            marker = "DEBUGGEE_RAN" if cli else "CONNECT_RETURNED"
            assert marker in process_result["stdout"], process_result
            assert "ATTACHED=True" in process_result["stdout"], process_result
            assert not process_result["timed_out"], process_result
            assert process_result["exit_code"] == 0, process_result
            return {
                "case": (
                    "plaintext-cli-full-attach-control"
                    if cli
                    else "plaintext-full-attach-control"
                ),
                "entry_point": "cli" if cli else "api",
                "endpoint": f"tcp://{HOST}:{port}",
                "attach_response_success": responses[attach]["success"],
                "configuration_done_success": responses[configuration]["success"],
                "debugpy_process": process_result,
            }
    finally:
        if debuggee is not None and debuggee.poll() is None:
            debuggee.kill()
            debuggee.communicate(timeout=5)
        if adapter.poll() is None:
            adapter.kill()
        adapter.communicate(timeout=5)
        endpoints_file.unlink(missing_ok=True)


def _main():
    parser = argparse.ArgumentParser(description="Local debugpy connection experiment")
    parser.add_argument(
        "--tcp-only",
        action="store_true",
        help="Run real API and CLI attaches over plain TCP without TLS or certificates",
    )
    args = parser.parse_args()
    ROOT.mkdir(exist_ok=True)
    if args.tcp_only:
        results = [_plain_attach_control(), _plain_attach_control(cli=True)]
    else:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(str(ROOT / "cert.pem"), str(ROOT / "key.pem"))
        results = [
            _run_tls_case(context, "tls-client-control", tls_control=True),
            _run_tls_case(context, "debugpy-api"),
            _run_tls_case(context, "debugpy-cli", cli=True),
            _plain_attach_control(),
        ]
    report = {
        "transport": "tcp" if args.tcp_only else "tcps",
        "python": sys.version,
        "source": os.environ["PYTHONPATH"],
        "debugpy_version": debugpy.__version__,
        "debugpy_path": debugpy.__file__,
        "results": results,
    }
    report_file = ROOT / ("tcp-results.json" if args.tcp_only else "tcps-results.json")
    report_file.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    _main()
