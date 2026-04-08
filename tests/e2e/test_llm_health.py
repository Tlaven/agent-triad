"""LLM connectivity and latency health checks.

Run these FIRST before any E2E tests to quickly surface:
  - API key issues (401/403)
  - Network/DNS problems
  - Proxy misconfiguration
  - Region/base_url misconfiguration
  - Model name typos
  - Timeout thresholds that are too tight

Recommended run order:
    1. test_print_configured_models          (always passes, shows config)
    2. test_network_connectivity_*           (fast TCP checks, no LLM calls)
    3. test_siliconflow_*_model_responds     (real LLM ping, ~5-15s each)
    4. tests/e2e/test_v1_acceptance.py       (full scenarios, only if above pass)

Usage:
    uv run pytest tests/e2e/test_llm_health.py -q -s --tb=line
    # verbose:
    uv run pytest tests/e2e/test_llm_health.py -v -s

Each test has a tight per-model timeout so failures are obvious within seconds,
not minutes.
"""

import asyncio
import os
import random
import socket
import time
import urllib.request

import pytest
from langchain_core.messages import HumanMessage

# All health tests require real API keys
pytestmark = pytest.mark.live_llm

PING_MESSAGE = [HumanMessage(content="Reply with exactly the word: pong")]

# Per-model call timeout (seconds).  Fail fast rather than hanging forever.
LLM_TIMEOUT_SECONDS = 20
FIRST_TOKEN_TIMEOUT_SUPERVISOR_SECONDS = 15.0
FIRST_TOKEN_TIMEOUT_PLANNER_SECONDS = 25.0

# SiliconFlow occasionally returns transient 5xx (e.g. HTTP 500 with code 50507).
# Health checks should be resilient to brief upstream instability, without masking
# auth/network issues.
SILICONFLOW_LLM_MAX_RETRIES = 3
SILICONFLOW_LLM_RETRY_BASE_DELAY_S = 0.75


def _has_siliconflow_key() -> bool:
    return bool(os.getenv("SILICONFLOW_API_KEY"))


def _has_dashscope_key() -> bool:
    return bool(os.getenv("DASHSCOPE_API_KEY"))


def _is_retryable_openai_error(exc: BaseException) -> bool:
    """Return True for transient upstream failures that are worth retrying."""
    try:
        from openai import (
            APIConnectionError,
            APITimeoutError,
            InternalServerError,
            RateLimitError,
        )
    except Exception:
        return False

    if isinstance(exc, (InternalServerError, RateLimitError, APIConnectionError, APITimeoutError)):
        return True

    # Some SDK versions wrap provider-specific JSON errors as generic APIError.
    code = getattr(exc, "code", None)
    if code in (500, 502, 503, 504):
        return True

    status_code = getattr(exc, "status_code", None)
    if status_code in (500, 502, 503, 504):
        return True

    return False


async def _retry_async(
    op_name: str,
    model_name: str,
    fn,
    *,
    max_retries: int = SILICONFLOW_LLM_MAX_RETRIES,
    base_delay_s: float = SILICONFLOW_LLM_RETRY_BASE_DELAY_S,
):
    """Run async callable `fn` with small exponential backoff on transient errors."""
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            return await fn()
        except Exception as exc:
            last_exc = exc
            if attempt >= max_retries - 1 or not _is_retryable_openai_error(exc):
                raise

            # jitter helps avoid thundering herd when upstream is flaky
            delay = base_delay_s * (2**attempt) + random.random() * 0.25
            print(  # noqa: T201
                f"\n  WARN: {op_name} failed for {model_name!r} "
                f"(attempt {attempt + 1}/{max_retries}): {type(exc).__name__}: {exc}. "
                f"Retrying in {delay:.2f}s..."
            )
            await asyncio.sleep(delay)

    # Should be unreachable, but keeps type checkers happy.
    assert last_exc is not None
    raise last_exc


async def _ping_model(model_name: str) -> tuple[str, float]:
    """Call model with a minimal prompt, return (response_text, elapsed_seconds).

    Raises TimeoutError (wrapped in pytest.fail) if no response within LLM_TIMEOUT_SECONDS.
    """
    from src.common.utils import load_chat_model

    model = load_chat_model(model_name)
    t0 = time.monotonic()

    async def _call():
        return await asyncio.wait_for(
            model.ainvoke(PING_MESSAGE),
            timeout=LLM_TIMEOUT_SECONDS,
        )

    try:
        response = await _retry_async("ainvoke", model_name, _call)
    except (asyncio.TimeoutError, TimeoutError):
        elapsed = time.monotonic() - t0
        pytest.fail(
            f"Model {model_name!r} did not respond within {LLM_TIMEOUT_SECONDS}s "
            f"(waited {elapsed:.1f}s).\n"
            "Possible causes:\n"
            "  1. Proxy intercepting HTTPS traffic (check HTTP_PROXY/HTTPS_PROXY)\n"
            "  2. API endpoint unreachable (run test_network_tcp_connectivity first)\n"
            "  3. Model is overloaded or deprecated\n"
            "  4. API key quota exhausted\n"
            "Run test_network_* tests first to narrow down the issue."
        )
    elapsed = time.monotonic() - t0
    content = response.content if isinstance(response.content, str) else str(response.content)
    return content.strip(), elapsed


# ---------------------------------------------------------------------------
# First-token latency (streaming)  — faster than full response
# ---------------------------------------------------------------------------

async def _first_token_latency(model_name: str, timeout: float = 15.0) -> float:
    """Return time-to-first-token (seconds) by streaming the response.

    Much faster than waiting for the complete response, and enough to confirm
    the model is alive and generating. Raises TimeoutError via pytest.fail on timeout.
    """
    from src.common.utils import load_chat_model

    model = load_chat_model(model_name)
    t0 = time.monotonic()

    async def _stream() -> float:
        async def _iter():
            async for _ in model.astream(PING_MESSAGE):
                return time.monotonic() - t0
            return time.monotonic() - t0

        return await _retry_async("astream", model_name, _iter)

    try:
        ttft = await asyncio.wait_for(_stream(), timeout=timeout)
    except (asyncio.TimeoutError, TimeoutError):
        elapsed = time.monotonic() - t0
        pytest.fail(
            f"No first token from {model_name!r} within {timeout}s "
            f"(waited {elapsed:.1f}s).\n"
            "Possible causes:\n"
            "  1. System proxy (detected: 127.0.0.1:7890) buffering/blocking streaming\n"
            "     → Try setting NO_PROXY=api.siliconflow.cn in your .env\n"
            "  2. Model overloaded — retry in a few minutes\n"
            "  3. API key quota exhausted\n"
            "  4. LLM_TIMEOUT_SECONDS too short for current network conditions"
        )
    return ttft


@pytest.mark.skipif(not _has_siliconflow_key(), reason="SILICONFLOW_API_KEY not set")
async def test_siliconflow_supervisor_model_first_token() -> None:
    """Time-to-first-token for supervisor model. Should be under configured threshold."""
    from src.common.context import Context

    model_name = Context().supervisor_model
    print(f"\n  model: {model_name}")  # noqa: T201

    timeout = FIRST_TOKEN_TIMEOUT_SUPERVISOR_SECONDS
    ttft = await _first_token_latency(model_name, timeout=timeout)
    print(f"  first token in: {ttft:.2f}s")  # noqa: T201
    assert ttft < timeout, f"First token too slow: {ttft:.1f}s >= {timeout:.1f}s"


@pytest.mark.skipif(not _has_siliconflow_key(), reason="SILICONFLOW_API_KEY not set")
async def test_siliconflow_planner_model_first_token() -> None:
    """Time-to-first-token for planner model. Should be under configured threshold."""
    from src.common.context import Context

    model_name = Context().planner_model
    print(f"\n  model: {model_name}")  # noqa: T201

    timeout = FIRST_TOKEN_TIMEOUT_PLANNER_SECONDS
    ttft = await _first_token_latency(model_name, timeout=timeout)
    print(f"  first token in: {ttft:.2f}s")  # noqa: T201
    assert ttft < timeout, f"First token too slow: {ttft:.1f}s >= {timeout:.1f}s"


# ---------------------------------------------------------------------------
# SiliconFlow models (full response)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _has_siliconflow_key(), reason="SILICONFLOW_API_KEY not set")
async def test_siliconflow_supervisor_model_responds() -> None:
    """Supervisor model (Step-3.5-Flash) must respond within timeout."""
    from src.common.context import Context

    model_name = Context().supervisor_model
    print(f"\n  model: {model_name}")  # noqa: T201

    content, elapsed = await _ping_model(model_name)
    print(f"  response: {content!r}  ({elapsed:.2f}s)")  # noqa: T201

    assert content, "Model returned empty response"
    assert elapsed < LLM_TIMEOUT_SECONDS, f"Model too slow: {elapsed:.1f}s >= {LLM_TIMEOUT_SECONDS}s"


@pytest.mark.skipif(not _has_siliconflow_key(), reason="SILICONFLOW_API_KEY not set")
async def test_siliconflow_planner_model_responds() -> None:
    """Planner model (GLM-5) must respond within timeout."""
    from src.common.context import Context

    model_name = Context().planner_model
    print(f"\n  model: {model_name}")  # noqa: T201

    content, elapsed = await _ping_model(model_name)
    print(f"  response: {content!r}  ({elapsed:.2f}s)")  # noqa: T201

    assert content, "Model returned empty response"
    assert elapsed < LLM_TIMEOUT_SECONDS, f"Model too slow: {elapsed:.1f}s >= {LLM_TIMEOUT_SECONDS}s"


@pytest.mark.skipif(not _has_siliconflow_key(), reason="SILICONFLOW_API_KEY not set")
async def test_siliconflow_executor_model_responds() -> None:
    """Executor model must respond within timeout."""
    from src.common.context import Context

    model_name = Context().executor_model
    print(f"\n  model: {model_name}")  # noqa: T201

    content, elapsed = await _ping_model(model_name)
    print(f"  response: {content!r}  ({elapsed:.2f}s)")  # noqa: T201

    assert content, "Model returned empty response"
    assert elapsed < LLM_TIMEOUT_SECONDS, f"Model too slow: {elapsed:.1f}s >= {LLM_TIMEOUT_SECONDS}s"


# ---------------------------------------------------------------------------
# Dashscope / Qwen models (optional)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _has_dashscope_key(), reason="DASHSCOPE_API_KEY not set")
async def test_dashscope_model_responds() -> None:
    """If DASHSCOPE_API_KEY is present, verify a Qwen model responds."""
    from src.common.context import Context

    model_name = os.getenv("DASHSCOPE_MODEL", Context().supervisor_model)
    print(f"\n  model: {model_name}")  # noqa: T201

    content, elapsed = await _ping_model(model_name)
    print(f"  response: {content!r}  ({elapsed:.2f}s)")  # noqa: T201

    assert content, "Model returned empty response"
    assert elapsed < LLM_TIMEOUT_SECONDS, f"Model too slow: {elapsed:.1f}s >= {LLM_TIMEOUT_SECONDS}s"


# ---------------------------------------------------------------------------
# Network connectivity checks (fast, no LLM generation needed)
# ---------------------------------------------------------------------------

def _get_siliconflow_base_url() -> str:
    """Return the base URL that will be used for SiliconFlow API calls."""
    from src.common.utils import normalize_region

    region = os.getenv("REGION", "").strip()
    normalized = normalize_region(region) if region else None
    if normalized == "prc":
        return "https://api.siliconflow.cn"
    if normalized == "international":
        return "https://api.siliconflow.com"
    return "https://api.siliconflow.com"


def test_print_network_info() -> None:
    """Always runs. Prints proxy settings and the resolved API endpoint."""
    base_url = _get_siliconflow_base_url()
    http_proxy = os.getenv("HTTP_PROXY") or os.getenv("http_proxy") or "(not set)"
    https_proxy = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy") or "(not set)"
    no_proxy = os.getenv("NO_PROXY") or os.getenv("no_proxy") or "(not set)"

    # Try to get system proxy via urllib
    try:
        proxies = urllib.request.getproxies()
        system_proxies = str(proxies) if proxies else "(none detected)"
    except Exception:
        system_proxies = "(detection failed)"

    print(  # noqa: T201
        f"\n  API base URL      : {base_url}"
        f"\n  HTTP_PROXY env    : {http_proxy}"
        f"\n  HTTPS_PROXY env   : {https_proxy}"
        f"\n  NO_PROXY env      : {no_proxy}"
        f"\n  System proxies    : {system_proxies}"
    )
    assert True


@pytest.mark.skipif(not _has_siliconflow_key(), reason="SILICONFLOW_API_KEY not set")
def test_network_tcp_connectivity_to_api() -> None:
    """TCP connect to port 443 of the SiliconFlow API endpoint.

    This is much faster than a full LLM call (~1-2s) and distinguishes:
      - TCP unreachable  → firewall / DNS / routing issue
      - TCP reachable    → server is up, but LLM generation may still be slow
    """
    import urllib.parse

    base_url = _get_siliconflow_base_url()
    parsed = urllib.parse.urlparse(base_url)
    host = parsed.hostname or "api.siliconflow.cn"
    port = parsed.port or 443

    print(f"\n  Testing TCP {host}:{port} ...", end="", flush=True)  # noqa: T201
    try:
        sock = socket.create_connection((host, port), timeout=8)
        sock.close()
        print(" OK")  # noqa: T201
    except socket.timeout:
        pytest.fail(
            f"TCP connection to {host}:{port} timed out after 8s. "
            "Possible causes: firewall blocking port 443, DNS not resolving, "
            "or proxy required but not configured."
        )
    except OSError as e:
        pytest.fail(
            f"TCP connection to {host}:{port} failed: {e}. "
            "Check DNS resolution and network routing."
        )


@pytest.mark.skipif(not _has_siliconflow_key(), reason="SILICONFLOW_API_KEY not set")
def test_network_https_handshake_to_api() -> None:
    """TLS handshake + HEAD request to /v1/models (or root) of the API endpoint.

    Validates:
      - TLS cert is valid
      - Server returns HTTP response (even 404/401 means server is alive)
      - Distinguishes 'server alive but API key invalid' from 'no response at all'
    """
    import ssl
    import urllib.parse
    import urllib.request

    base_url = _get_siliconflow_base_url()
    check_url = base_url.rstrip("/") + "/v1/models"
    key = os.getenv("SILICONFLOW_API_KEY", "")

    req = urllib.request.Request(
        check_url,
        headers={"Authorization": f"Bearer {key}", "User-Agent": "AgentTriad-healthcheck/1.0"},
        method="GET",
    )
    print(f"\n  GET {check_url} ...", end="", flush=True)  # noqa: T201
    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            status = resp.status
            print(f" HTTP {status}")  # noqa: T201
            # 200 = OK, 401 = bad key, 403 = forbidden — all mean server is alive
            assert status < 500, f"Server returned {status} (server-side error)"
    except urllib.error.HTTPError as e:
        print(f" HTTP {e.code}")  # noqa: T201
        if e.code == 401:
            pytest.fail(
                f"HTTP 401 Unauthorized from {check_url}. "
                "API key is set but rejected. Check SILICONFLOW_API_KEY value."
            )
        elif e.code == 403:
            print("  HTTP 403 (key has no list-models permission, but server IS alive)")  # noqa: T201
            # 403 is fine — server responded, key may just lack model-list permission
        else:
            pytest.fail(f"HTTP {e.code} from {check_url}: {e.reason}")
    except urllib.error.URLError as e:
        pytest.fail(
            f"URL error reaching {check_url}: {e.reason}. "
            "Possible proxy misconfiguration or network issue. "
            "Check HTTP_PROXY / HTTPS_PROXY environment variables."
        )
    except TimeoutError:
        pytest.fail(
            f"HTTPS request to {check_url} timed out after 10s. "
            "Server is TCP-reachable but not returning HTTP headers. "
            "Possible: transparent proxy blocking HTTPS, or TLS inspection stripping responses."
        )


# ---------------------------------------------------------------------------
# API key format sanity (fast, no network call)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _has_siliconflow_key(), reason="SILICONFLOW_API_KEY not set")
def test_siliconflow_api_key_format() -> None:
    """Key must start with 'sk-' and be at least 20 chars long."""
    key = os.getenv("SILICONFLOW_API_KEY", "")
    assert key.startswith("sk-"), f"Key does not start with 'sk-': {key[:8]}..."
    assert len(key) >= 20, f"Key looks too short ({len(key)} chars)"


# ---------------------------------------------------------------------------
# Region / base_url resolution
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _has_siliconflow_key(), reason="SILICONFLOW_API_KEY not set")
def test_region_resolves_to_known_endpoint() -> None:
    """REGION env var (if set) must resolve to a known SiliconFlow endpoint."""
    from src.common.utils import normalize_region

    region = os.getenv("REGION", "").strip()
    if not region:
        pytest.skip("REGION not set; skipping endpoint resolution check")

    normalized = normalize_region(region)
    assert normalized in ("prc", "international"), (
        f"REGION={region!r} resolves to unknown value: {normalized!r}. "
        "Accepted: prc/cn or international/en"
    )


# ---------------------------------------------------------------------------
# Summary helper: print all configured model names
# ---------------------------------------------------------------------------

def test_print_configured_models() -> None:
    """Always runs (no API call). Prints the model names that will be used."""
    from src.common.context import Context

    ctx = Context()
    print(  # noqa: T201
        f"\n  supervisor_model : {ctx.supervisor_model}"
        f"\n  planner_model    : {ctx.planner_model}"
        f"\n  executor_model   : {ctx.executor_model}"
        f"\n  max_replan       : {ctx.max_replan}"
        f"\n  SILICONFLOW_KEY  : {'set (' + str(len(os.getenv('SILICONFLOW_API_KEY',''))) + ' chars)' if _has_siliconflow_key() else 'NOT SET'}"
        f"\n  DASHSCOPE_KEY    : {'set' if _has_dashscope_key() else 'NOT SET'}"
        f"\n  REGION           : {os.getenv('REGION', '(not set)')}"
    )
    # This test always passes - it's purely informational
    assert True
