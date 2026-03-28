<p align="center">
  <img src="assets/banner.svg" alt="RocketRide Python SDK" width="900">
</p>

<p align="center">
  <strong>Python SDK for the RocketRide Engine</strong> &mdash; build, run, and manage AI pipelines from Python.
</p>

---

## Quick Start

```bash
pip install rocketride
```

```python
from rocketride import RocketRideClient

async with RocketRideClient(uri="https://cloud.rocketride.ai", auth="YOUR_API_KEY") as client:
    result = await client.use(filepath="./pipeline.pipe")
    token = result["token"]
    output = await client.send(token, "Hello, pipeline!", {"name": "input.txt"}, "text/plain")
    print(output)
    await client.terminate(token)
```

Don't have a pipeline yet? Visit [RocketRide on GitHub](https://github.com/rocketride-org/rocketride-server) or download the extension directly in your IDE.

## What is RocketRide?

[RocketRide](https://rocketride.org) is an open source, developer-native AI pipeline platform.
It lets you build, debug, and deploy production AI workflows without leaving your IDE &mdash;
using a visual drag-and-drop canvas or code-first with TypeScript and Python SDKs.

- **50+ ready-to-use nodes** &mdash; 13 LLM providers, 8 vector databases, OCR, NER, PII anonymization, and more
- **High-performance C++ engine** &mdash; production-grade speed and reliability
- **Deploy anywhere** &mdash; locally, on-premises, or self-hosted with Docker
- **MIT licensed** &mdash; fully open source, OSI-compliant

## Features

- **Pipeline execution** &mdash; start with `use()`, send data via `send()`, `send_files()`, or `pipe()`
- **Chat** &mdash; conversational AI via `chat()` and `Question`
- **Event streaming** &mdash; real-time events via `on_event` and `set_events()`
- **File upload** &mdash; `send_files()` with progress; streaming with `pipe()`
- **Bundled engine** &mdash; the SDK can automatically download and manage a local RocketRide engine
- **Full async support** &mdash; built on `asyncio` and `websockets`

---

## CLI Reference

The `rocketride` command-line interface provides two categories of commands: **pipeline/task management** (for running and monitoring pipelines) and **engine management** (for controlling local engine instances).

### Pipeline & Task Commands

#### `rocketride start <pipeline>`

Start a new pipeline execution.

```bash
rocketride start ./pipeline.pipe
rocketride start ./pipeline.pipe --uri http://localhost:5566 --apikey YOUR_KEY
rocketride start ./pipeline.pipe --threads 4 --args key1=value1 key2=value2
```

| Flag | Description |
| --- | --- |
| `--uri` | Engine URI (default: `$ROCKETRIDE_URI` or auto-spawn) |
| `--apikey` | API key (default: `$ROCKETRIDE_APIKEY`) |
| `--token` | Reuse an existing task token |
| `--threads` | Number of threads for the pipeline |
| `--args` | Key-value arguments passed to the pipeline |

#### `rocketride upload <files> [--pipeline_path <path>]`

Upload files to an existing or new pipeline.

```bash
rocketride upload ./data/*.csv --pipeline_path ./pipeline.pipe
rocketride upload report.pdf --token TASK_TOKEN --uri http://localhost:5566
```

| Flag | Description |
| --- | --- |
| `--pipeline_path` | Pipeline to start if no `--token` is given |
| `--token` | Upload to an existing task |
| `--uri` | Engine URI |
| `--apikey` | API key |
| `--threads` | Number of threads |
| `--args` | Key-value arguments |

#### `rocketride status`

Monitor task execution status continuously.

```bash
rocketride status --token TASK_TOKEN --uri http://localhost:5566
```

#### `rocketride stop`

Terminate a running task.

```bash
rocketride stop --token TASK_TOKEN --uri http://localhost:5566
```

#### `rocketride events [event_types...]`

Monitor task events with optional filtering.

```bash
rocketride events --token TASK_TOKEN
rocketride events apaevt_status_upload apaevt_status_processing --token TASK_TOKEN
rocketride events --log --token TASK_TOKEN
```

#### `rocketride list`

List all active tasks on the engine.

```bash
rocketride list --uri http://localhost:5566
rocketride list --json
```

### Engine Management

The engine commands manage local RocketRide engine instances. The SDK can automatically download, install, and run the engine binary &mdash; no separate install needed.

Use `rocketride engine <command>` (or the shorthand `rocketride e <command>`).

#### `rocketride engine install [version]`

Download and register an engine binary.

```bash
rocketride engine install              # latest compatible version
rocketride engine install 3.0.5        # specific version
rocketride engine install 3.0.5 --force  # skip compatibility check
```

#### `rocketride engine list`

List all tracked engine instances with status, port, PID, memory, and uptime.

```bash
rocketride engine list
```

```
VERSION  ID  PID    PORT  OWNER  STATUS  RESTARTED  UPTIME  MEMORY
3.0.5    0   12340  5566  cli    online  2          4m      86.9 MB
3.0.3    1   -      -     cli    stopped 0          0s      -
```

#### `rocketride engine start [id]`

Start an engine instance.

```bash
rocketride engine start 0                         # by instance ID
rocketride engine start --version 3.0.5            # by version
rocketride engine start --version 3.0.5 --port 7777  # explicit port
```

| Flag | Description |
| --- | --- |
| `--version` | Engine version to use (looks up the registered instance for that version) |
| `--port` | Explicit port (default: auto-assigned) |

#### `rocketride engine stop <id>`

Stop a running engine instance.

```bash
rocketride engine stop 0
```

#### `rocketride engine delete <id>`

Deregister an engine instance. The engine binary is kept on disk so `install` can re-register it without re-downloading.

```bash
rocketride engine delete 0            # deregister only
rocketride engine delete 0 --purge    # also remove the binary from disk
```

| Flag | Description |
| --- | --- |
| `--purge` | Also remove the engine binary from disk |

#### `rocketride engine logs <id>`

Tail the log output of an engine instance.

```bash
rocketride engine logs 0
```

#### `rocketride engine run <pipeline>`

Run a pipeline with an auto-managed engine. The engine is started before execution and stopped afterward.

```bash
rocketride engine run ./pipeline.pipe --apikey YOUR_KEY
rocketride engine run ./pipeline.pipe --engine 0    # use a specific instance
```

---

## RocketRideClient

### Constructor

```python
RocketRideClient(
    uri: str = "",
    auth: str = "",
    persist: bool = False,
    max_retry_time: int = None,
    request_timeout: int = None,
    env: dict = None,
    on_event: Callable = None,
    on_connected: Callable = None,
    on_disconnected: Callable = None,
    on_connect_error: Callable = None,
)
```

Creates a client instance. It does **not** connect until you call `connect()` (or use `async with`). `auth` and `uri` are optional at construction and default to `ROCKETRIDE_APIKEY` and `ROCKETRIDE_URI` environment variables.

Supports `async with` for automatic connect/disconnect:

```python
async with RocketRideClient(uri=uri, auth=key) as client:
    ...
```

### Connection

| Method | Signature | Description |
| --- | --- | --- |
| `connect` | `async connect(uri=None, auth=None, timeout=None)` | Opens the WebSocket and authenticates. In **persist** mode, retries with exponential backoff on failure. |
| `disconnect` | `async disconnect()` | Closes the connection and cancels any pending reconnection. |
| `is_connected` | `is_connected() -> bool` | Whether the client is currently connected. |
| `get_connection_info` | `get_connection_info() -> dict` | Returns `{connected, transport, uri}`. |
| `get_apikey` | `get_apikey() -> str | None` | The API key in use. |

### Pipeline Execution

| Method | Signature | Description |
| --- | --- | --- |
| `use` | `async use(*, filepath=None, pipeline=None, token=None, source=None, threads=None, use_existing=None, args=None, ttl=None) -> dict` | Starts a pipeline from a file path or config object. Returns `{"token": "...", ...}`. |
| `terminate` | `async terminate(token: str)` | Stops the pipeline and frees server resources. |
| `get_task_status` | `async get_task_status(token: str) -> dict` | Returns task status: `state`, `completed`, `completedCount`, `totalCount`, `exitCode`, etc. |
| `validate` | `async validate(pipeline, *, source=None) -> dict` | Validates a pipeline config without starting it. |

### Data

| Method | Signature | Description |
| --- | --- | --- |
| `send` | `async send(token, data, objinfo=None, mimetype=None) -> dict` | Sends data in one shot. Use for small payloads. |
| `send_files` | `async send_files(files, token) -> list` | Uploads multiple files. Each entry is a path string or `(path, objinfo, mimetype)` tuple. |
| `pipe` | `async pipe(token, objinfo=None, mime_type=None, provider=None) -> DataPipe` | Creates a streaming data pipe for large or incremental payloads. |

### Chat

| Method | Signature | Description |
| --- | --- | --- |
| `chat` | `async chat(*, token, question) -> dict` | Sends a `Question` to the AI and returns the pipeline result. |

### Events

| Method | Signature | Description |
| --- | --- | --- |
| `set_events` | `async set_events(token, event_types, pipe_id=None)` | Subscribes to event types (e.g. `apaevt_status_upload`, `apaevt_status_processing`). Events are delivered to your `on_event` callback. |

### Services & Ping

| Method | Signature | Description |
| --- | --- | --- |
| `get_services` | `async get_services() -> dict` | Returns all service/connector definitions from the server. |
| `get_service` | `async get_service(service) -> dict | None` | Returns the definition for one service by name. |
| `ping` | `async ping(token=None)` | Lightweight liveness check. |

---

## DataPipe

Returned by `client.pipe()`. Represents one streaming upload: **open** -> one or more **write** -> **close**.

| Property | Type | Description |
| --- | --- | --- |
| `is_opened` | `bool` | Whether the pipe has been opened and not yet closed. |
| `pipe_id` | `int | None` | Server-assigned pipe ID; set after `open()`. |

| Method | Signature | Description |
| --- | --- | --- |
| `open` | `async open() -> DataPipe` | Opens the pipe on the server. Must be called before `write()`. |
| `write` | `async write(buffer: bytes)` | Writes a chunk. Pipe must be open. |
| `close` | `async close() -> dict | None` | Closes the pipe and returns the processing result. |

Supports `async with` for automatic open/close:

```python
async with await client.pipe(token, {"name": "data.csv"}, "text/csv") as p:
    for chunk in chunks:
        await p.write(chunk)
```

---

## Question

Build a question for `client.chat(token=token, question=q)`. Add instructions, examples, context, history, and documents.

```python
from rocketride import Question, Answer

q = Question(expect_json=True)
q.addInstruction("Format", "Return JSON with keys: summary, keywords.")
q.addExample("Summarize X", {"summary": "...", "keywords": ["a", "b"]})
q.addQuestion("Summarize the main points and list keywords.")

result = await client.chat(token=token, question=q)
parsed = Answer.parseJson(result.get("data", {}).get("answer", ""))
```

### Methods

| Method | Signature | Description |
| --- | --- | --- |
| `addInstruction` | `addInstruction(title, instruction)` | Adds an instruction for the AI. |
| `addExample` | `addExample(given, result)` | Adds an example input/output pair. |
| `addContext` | `addContext(context)` | Adds background context (string, dict, or list). |
| `addHistory` | `addHistory(item)` | Adds a `QuestionHistory(role, content)` for multi-turn chat. |
| `addQuestion` | `addQuestion(question)` | Appends the main question text. |
| `addDocuments` | `addDocuments(documents)` | Adds `Doc` objects for the AI to reference. |
| `addGoal` | `addGoal(goal)` | Adds a high-level goal. |

---

## Answer

Static helpers for parsing AI response content.

| Method | Description |
| --- | --- |
| `Answer.parseJson(value)` | Parses JSON from AI text (strips markdown/code blocks). |
| `Answer.parsePython(value)` | Extracts Python code from a code block in the response. |

---

## Exceptions

| Exception | Description |
| --- | --- |
| `RocketRideException` | Base exception for all SDK errors. |
| `AuthenticationException` | Raised on DAP auth failure. In persist mode the client does not retry, so the app can fix credentials and call `connect()` again. |
| `EngineError` | Raised when an engine operation fails. |
| `EngineNotFoundError` | Raised when no compatible engine binary is found. |
| `UnsupportedPlatformError` | Raised when the current platform is not supported for engine management. |

---

## Examples

### 1. Minimal: connect, run, send, disconnect

```python
from rocketride import RocketRideClient

async with RocketRideClient(uri="https://cloud.rocketride.ai", auth="my-key") as client:
    result = await client.use(filepath="./pipeline.pipe")
    output = await client.send(result["token"], "Hello!", {"name": "input.txt"}, "text/plain")
    print(output)
    await client.terminate(result["token"])
```

### 2. Upload files and poll until complete

```python
from rocketride import RocketRideClient
import asyncio

async with RocketRideClient(uri=uri, auth=key) as client:
    result = await client.use(filepath="./vectorize.pipe")
    token = result["token"]
    await client.set_events(token, ["apaevt_status_upload"])
    await client.send_files(["doc1.pdf", "doc2.pdf"], token)

    while True:
        status = await client.get_task_status(token)
        if status.get("completed"):
            break
        await asyncio.sleep(2)

    await client.terminate(token)
```

### 3. Stream large data with a pipe

```python
from rocketride import RocketRideClient

async with RocketRideClient(uri=uri, auth=key) as client:
    result = await client.use(filepath="./pipeline.pipe")
    token = result["token"]

    async with await client.pipe(token, {"name": "large.csv"}, "text/csv") as p:
        with open("large.csv", "rb") as f:
            while chunk := f.read(65536):
                await p.write(chunk)

    await client.terminate(token)
```

### 4. Chat with structured JSON response

```python
from rocketride import RocketRideClient, Question, Answer

async with RocketRideClient(uri=uri, auth=key) as client:
    result = await client.use(filepath="./chat-pipeline.pipe")
    token = result["token"]

    q = Question(expect_json=True)
    q.addInstruction("Format", "Return JSON with keys: summary, keywords.")
    q.addQuestion("Summarize the main points.")

    response = await client.chat(token=token, question=q)
    print(Answer.parseJson(response["data"]["answer"]))

    await client.terminate(token)
```

### 5. Local engine: install, start, run pipeline, stop

```bash
# Install and start an engine
rocketride engine install
rocketride engine start 0 --port 5566

# Run a pipeline against it
rocketride start ./pipeline.pipe --uri http://localhost:5566

# When done
rocketride engine stop 0
```

---

## Links

- [Documentation](https://docs.rocketride.org/)
- [GitHub](https://github.com/rocketride-org/rocketride-server)
- [Discord](https://discord.gg/9hr3tdZmEG)
- [Contributing](https://github.com/rocketride-org/rocketride-server/blob/develop/CONTRIBUTING.md)

## License

MIT &mdash; see [LICENSE](https://github.com/rocketride-org/rocketride-server/blob/develop/LICENSE).
