import httpx


class ScriptedTransport(httpx.AsyncBaseTransport):
    def __init__(self):
        self.calls: list[str] = []
        self._rules: list[tuple[str, str, object]] = []

    def route(self, method: str, url_part: str, handler):
        self._rules.append((method.upper(), url_part, handler))
        return self

    def respond(self, method: str, url_part: str, status: int = 200, json_body=None, text: str | None = None, headers=None):
        def handler(_request):
            if text is not None:
                return httpx.Response(status, text=text, headers=headers)
            return httpx.Response(status, json=json_body, headers=headers)

        return self.route(method, url_part, handler)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(f"{request.method} {request.url}")
        for method, part, handler in reversed(self._rules):
            if request.method == method and part in str(request.url):
                result = handler(request)
                if hasattr(result, "__await__"):
                    result = await result
                return result
        return httpx.Response(599, text=f"unmocked {request.method} {request.url}")

    def matching(self, part: str) -> list[str]:
        return [call for call in self.calls if part in call]
