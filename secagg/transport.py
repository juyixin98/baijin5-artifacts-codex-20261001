"""HTTP 传输适配:把客户端 Transport 接口接到 httpx 上.

服务器返回的错误体 {category, code, message, detail} 被还原为 SecAggError,
因此测试在客户端侧也能断言与服务器一致的错误类别。
"""

from __future__ import annotations

import httpx

from .errors import ErrorCategory, SecAggError


class HttpTransport:
    def __init__(self, http: httpx.Client) -> None:
        self._http = http

    def _handle(self, resp: httpx.Response) -> dict:
        if resp.status_code >= 400:
            try:
                body = resp.json()
                raise SecAggError(
                    ErrorCategory(body.get("category", "computation_failure")),
                    body.get("code", "http_error"),
                    body.get("message", resp.text),
                    body.get("detail") or {},
                )
            except (ValueError, KeyError) as exc:
                raise SecAggError(
                    ErrorCategory.COMPUTATION_FAILURE,
                    "http_error",
                    f"HTTP {resp.status_code}: {resp.text[:200]}",
                ) from exc
        return resp.json()

    def post(self, path: str, payload: dict) -> dict:
        return self._handle(self._http.post(path, json=payload))

    def get(self, path: str) -> dict:
        return self._handle(self._http.get(path))
