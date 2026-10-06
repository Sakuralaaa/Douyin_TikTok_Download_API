"""Cloud-only browser regression check using an authenticated loopback proxy."""

import asyncio
import socketserver
import struct
import threading

from browser_rpc.backends.cloak import CloakBackend
from browser_rpc.geo import FALLBACK_PROFILE
from browser_rpc.settings import Settings
from browser_rpc.validation import validate_proxy_url

USER = b"smoke-user"
PASSWORD = b"p@ss=word"
TARGET = "proxy-target.invalid"
requests = []


class SocksProxy(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(20)
        version, count = self.rfile.read(2)
        methods = self.rfile.read(count)
        if version != 5 or 2 not in methods:
            self.wfile.write(b"\x05\xff")
            return
        self.wfile.write(b"\x05\x02")
        if self.rfile.read(1) != b"\x01":
            return
        user = self.rfile.read(self.rfile.read(1)[0])
        password = self.rfile.read(self.rfile.read(1)[0])
        accepted = user == USER and password == PASSWORD
        self.wfile.write(b"\x01\x00" if accepted else b"\x01\x01")
        if not accepted:
            return
        version, command, _, address_type = self.rfile.read(4)
        if version != 5 or command != 1 or address_type != 3:
            self.wfile.write(b"\x05\x08\x00\x01" + b"\x00" * 6)
            return
        # The .invalid hostname must arrive here, never resolve locally.
        host = self.rfile.read(self.rfile.read(1)[0]).decode()
        port = struct.unpack("!H", self.rfile.read(2))[0]
        self.wfile.write(b"\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x50")
        first_line = self.rfile.readline()
        while self.rfile.readline() not in (b"\r\n", b"\n", b""):
            pass
        requests.append((host, port, first_line))
        body = b"<title>authenticated-proxy-ok</title>"
        self.wfile.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nConnection: close\r\n"
            + f"Content-Length: {len(body)}\r\n\r\n".encode()
            + body
        )


class ProxyServer(socketserver.ThreadingTCPServer):
    daemon_threads = True


async def main(port):
    backend = CloakBackend(Settings(backend="cloak"))
    await backend.start()
    try:
        proxy = validate_proxy_url(f"socks5h://smoke-user:p%40ss%3Dword@127.0.0.1:{port}")
        context = await backend._launch_context(
            "/profiles/proxy-smoke", FALLBACK_PROFILE, proxy, 20
        )
        try:
            page = await context.new_page()
            await page.goto(f"http://{TARGET}/smoke", timeout=20000)
            assert await page.title() == "authenticated-proxy-ok"
            assert any(host == TARGET and port == 80 and b"/smoke" in line
                       for host, port, line in requests), requests
            print("Authenticated SOCKS5h browser and remote DNS checks passed")
        finally:
            await context.close()
    finally:
        await backend.close()


with ProxyServer(("127.0.0.1", 0), SocksProxy) as server:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        asyncio.run(main(server.server_address[1]))
    finally:
        server.shutdown()
        thread.join()
