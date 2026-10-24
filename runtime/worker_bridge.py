"""Headless, per-service MCP edge: stdio or streaming HTTP behind Loopback OAuth.

No local filesystem or shell tools are added. The explicitly configured upstream
is the whole tool surface. It runs on the selected worker, never on the controller.
"""
from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, StreamingResponse
from starlette.routing import Route

import auth_http
from bootstrap import build_context
from node_agent import read_json, write_json

HOP = {'host', 'connection', 'transfer-encoding', 'content-length', 'keep-alive', 'upgrade', 'proxy-authorization',
       'proxy-authenticate', 'trailer', 'te', 'set-cookie', 'authorization', 'cookie', 'x-api-key', 'api-key',
       'forwarded', 'x-forwarded-host', 'x-forwarded-for', 'x-forwarded-proto'}


class EdgeGate:
    def __init__(self, app, ctx, directory):
        self.app, self.ctx, self.directory = app, ctx, directory
        self.traffic = read_json(directory / 'traffic.json', {'rx_bytes': 0, 'tx_bytes': 0})

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope.get('path') != '/mcp' or scope['method'] == 'OPTIONS':
            return await self.app(scope, receive, send)
        request = Request(scope)
        p = self.ctx.auth.authenticate(request.headers.get('authorization'), legacy_machine_token=False)
        if p is None:
            return await auth_http.unauthorized(self.ctx, request)(scope, receive, send)
        if not p.has('loopback:exec'):
            return await auth_http.forbidden_scope(self.ctx, request, 'loopback:exec', 'This upstream can execute tools')(scope, receive, send)
        # SDK sessions remain bound to one authenticated principal.
        scope.setdefault('state', {})['principal'] = p
        async def recv():
            msg = await receive()
            self.traffic['rx_bytes'] += len(msg.get('body', b''))
            return msg
        async def emit(msg):
            self.traffic['tx_bytes'] += len(msg.get('body', b''))
            await send(msg)
        try:
            await self.app(scope, recv, emit)
        finally:
            write_json(self.directory / 'traffic.json', self.traffic)


def create_app(directory):
    directory = Path(directory)
    binding = read_json(directory / 'binding.json')
    if not binding:
        raise ValueError('Missing binding configuration')
    ctx = build_context(directory, start_managers=False)
    manager = None
    client = httpx.AsyncClient(timeout=httpx.Timeout(300, connect=10), follow_redirects=False)
    if binding['transport'] == 'stdio':
        @contextlib.asynccontextmanager
        async def upstream_life(server):
            command = binding['command']
            params = StdioServerParameters(command=command[0], args=command[1:], cwd=binding.get('cwd') or None)
            async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                yield session

        async def list_tools(c, p):
            return await c.lifespan_context.list_tools(params=p)
        async def call_tool(c, p):
            return await c.lifespan_context.call_tool(p.name, p.arguments)
        async def list_resources(c, p):
            return await c.lifespan_context.list_resources(params=p)
        async def read_resource(c, p):
            return await c.lifespan_context.read_resource(p.uri)
        async def list_templates(c, p):
            return await c.lifespan_context.list_resource_templates(params=p)
        async def list_prompts(c, p):
            return await c.lifespan_context.list_prompts(params=p)
        async def get_prompt(c, p):
            return await c.lifespan_context.get_prompt(p.name, p.arguments)
        server = Server('Loopback worker: ' + binding['name'], lifespan=upstream_life,
                        on_list_tools=list_tools, on_call_tool=call_tool, on_list_resources=list_resources,
                        on_read_resource=read_resource, on_list_resource_templates=list_templates,
                        on_list_prompts=list_prompts, on_get_prompt=get_prompt)
        # The process listens on loopback only and is behind a TLS ingress and the OAuth gate.
        manager = StreamableHTTPSessionManager(server, json_response=True, stateless=True,
                    security_settings=TransportSecuritySettings(enable_dns_rebinding_protection=False))

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with client:
            if manager:
                async with manager.run():
                    yield
            else:
                yield

    async def proxy(request):
        body = await request.body()
        if len(body) > 4 * 1024 * 1024:
            return JSONResponse({'error': 'request too large'}, status_code=413)
        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP}
        upstream_token = binding.get('upstream_token_file')
        if upstream_token:
            headers['authorization'] = 'Bearer ' + Path(upstream_token).expanduser().read_text().strip()
        try:
            req = client.build_request(request.method, binding['upstream'], headers=headers, content=body)
            response = await client.send(req, stream=True)
        except (httpx.HTTPError, OSError):
            return JSONResponse({'error': 'upstream unavailable'}, status_code=502)
        async def chunks():
            try:
                async for chunk in response.aiter_raw():
                    yield chunk
            finally:
                await response.aclose()
        return StreamingResponse(chunks(), status_code=response.status_code,
                    headers={k: v for k, v in response.headers.items() if k.lower() not in HOP})

    async def mcp_endpoint(scope, receive, send):
        if manager:
            await manager.handle_request(scope, receive, send)
        else:
            await (await proxy(Request(scope, receive)))(scope, receive, send)

    async def health(request):
        return JSONResponse({'ok': True, 'service': binding['name'], 'transport': binding['transport']})

    async def home(request):
        return RedirectResponse('/admin/enroll')

    class Endpoint:
        async def __call__(self, scope, receive, send):
            await mcp_endpoint(scope, receive, send)

    app = Starlette(routes=[*auth_http.routes(ctx), Route('/healthz', health), Route('/admin', home),
                            Route('/mcp', Endpoint(), methods=['GET', 'POST', 'DELETE'])], lifespan=lifespan)
    origins = ctx.config.data['auth']['allowed_origins']
    return EdgeGate(CORSMiddleware(app, allow_origins=origins, allow_methods=['GET', 'POST', 'DELETE', 'OPTIONS'],
                    allow_headers=['Authorization', 'Content-Type', 'Mcp-Session-Id', 'Mcp-Protocol-Version'],
                    expose_headers=['Mcp-Session-Id', 'WWW-Authenticate']), ctx, directory)


if __name__ == '__main__':
    import uvicorn
    binding_dir = Path(sys.argv[1]).resolve()
    os.environ['LOOPBACK_CONFIG_DIR'] = str(binding_dir)
    uvicorn.run(create_app(binding_dir), host='127.0.0.1', port=read_json(binding_dir / 'binding.json')['port'], log_level='warning')
