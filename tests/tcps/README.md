# TCPS connection experiment

This standalone, local-only harness checks whether debugpy can attach to a
TLS-only endpoint when supplied only its hostname and port. It is not collected
by pytest and does not change debugpy.

## Run on Windows

Requirements: Python 3.10 or later and PowerShell. The TLS runner additionally
requires OpenSSL on `PATH`. Git for Windows includes OpenSSL.

From the repository root:

```powershell
.\tests\tcps\run_tcps.ps1
```

The runner uses this checkout's `src` directory, generates a one-day self-signed
certificate for `localhost`, and runs four cases:

| Case | Expected result |
| --- | --- |
| TLS client control | Verified TLS handshake and encrypted echo succeed. |
| `debugpy.connect(("localhost", port))` | TCP connects, but TLS handshake fails and attach does not complete. |
| `python -m debugpy --connect localhost:port` | Same failure as the API case. |
| Plain TCP debug adapter control | DAP attach and configuration succeed; `is_client_connected()` is `True`. |

All listeners bind to `127.0.0.1` on automatically assigned ports. The certificate
and private key are removed in the runner's `finally` block. Results and debugpy
logs are retained in the ignored `_output` directory.

## Plain TCP runner

Run just the successful attach cases, without OpenSSL, certificates, or TLS:

```powershell
.\tests\tcps\run_tcp.ps1
```

This uses the same harness and debuggee commands as the TLS runner, but connects
to a real debug adapter over plain TCP. Both the API and CLI cases must complete
DAP `attach` and `configurationDone`, report `ATTACHED=True`, and exit with code
zero without being killed. The API also reports `CONNECT_RETURNED`; the CLI
reports `DEBUGGEE_RAN`.

Results are saved as `_output\tcp-results.json`, separately from
`_output\tcps-results.json`. Debugpy logs remain in `_output`. This runner
does not create a TLS context or require certificate files.

## What the original run observed

Using debugpy `1.8.22+6.g8902fbb1` and Python 3.13.15 on Windows, neither debugpy
attempt sent a TLS ClientHello. The TLS server reported:

```text
TimeoutError: _ssl.c:1015: The handshake operation timed out
```

This was a server-side error, not an exception raised by `debugpy.connect()`.
After the server closed the socket, debugpy's internal logs contained:

```text
ReaderThread: empty contents received (len(line) == 0).
ConnectionAbortedError: [WinError 10053] An established connection was aborted by the software in your host machine
```

The latter occurred while the writer thread tried to send its termination
message. Both clients remained blocked and were killed by the harness after its
timeout. The recorded killed-process exit codes are not attach-error exit codes
from debugpy. Exact socket errors can vary across operating systems.

The TLS endpoint is an echo server, not a TLS-enabled debug adapter. The
experiment establishes failure to negotiate TLS, which prevents any subsequent
debug protocol session. The separate plaintext control completes a real attach.
It does not test an IDE's URL handling or endpoints that also accept plaintext.
