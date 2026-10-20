#!/usr/bin/env python3
"""SonyRemote local bridge.

Serves public/index.html and forwards key presses to a Sony Bravia TV (IRCC over HTTP).
The browser cannot call the TV directly (mixed content + CORS), so this proxy does it.

Usage:
    python server.py            # reachable from your phone on the same Wi-Fi
    python server.py --local    # only this computer (127.0.0.1)

Then open http://localhost:8000 (or the LAN URL printed on start).
Python 3.8+, standard library only.
"""
import ipaddress
import json
import socket
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = 8000
TIMEOUT_SECONDS = 4
ROOT = Path(__file__).resolve().parent / "public"

# Standard Sony IRCC codes (base64 payload sent inside the SOAP envelope)
IRCC_CODES = {
    "POWER": "AAAAAQAAAAEAAAAVAw==",
    "VOL_UP": "AAAAAQAAAAEAAAASAw==",
    "VOL_DOWN": "AAAAAQAAAAEAAAATAw==",
    "MUTE": "AAAAAQAAAAEAAAAUAw==",
    "CH_UP": "AAAAAQAAAAEAAAAQAw==",
    "CH_DOWN": "AAAAAQAAAAEAAAARAw==",
    "INPUT": "AAAAAQAAAAEAAAAlAw==",
    "UP": "AAAAAQAAAAEAAAB0Aw==",
    "DOWN": "AAAAAQAAAAEAAAB1Aw==",
    "LEFT": "AAAAAQAAAAEAAAA0Aw==",
    "RIGHT": "AAAAAQAAAAEAAAAzAw==",
    "CONFIRM": "AAAAAQAAAAEAAABlAw==",
    "HOME": "AAAAAQAAAAEAAABgAw==",
    "BACK": "AAAAAgAAAJcAAAAjAw==",
}

SOAP_TEMPLATE = (
    '<?xml version="1.0"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
    's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
    "<s:Body>"
    '<u:X_SendIRCC xmlns:u="urn:schemas-sony-com:service:IRCC:1">'
    "<IRCCCode>{code}</IRCCCode>"
    "</u:X_SendIRCC>"
    "</s:Body></s:Envelope>"
)


def is_private_ipv4(value):
    """Only literal private IPv4 addresses are allowed, so this proxy cannot be
    abused to reach the internet or arbitrary hosts."""
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    return ip.version == 4 and ip.is_private and not ip.is_loopback


def http_call(url, data=None, headers=None, method="GET"):
    request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            body = response.read(400).decode("utf-8", "replace")
            return {"status": response.status, "body": body}
    except urllib.error.HTTPError as error:
        return {"status": error.code, "body": error.read(400).decode("utf-8", "replace")}
    except Exception as error:  # timeout, refused, unreachable...
        return {"status": None, "body": str(error)}


def send_key(ip, psk, key):
    headers = {
        "Content-Type": "text/xml; charset=UTF-8",
        "SOAPACTION": '"urn:schemas-sony-com:service:IRCC:1#X_SendIRCC"',
    }
    if psk:
        headers["X-Auth-PSK"] = psk
    body = SOAP_TEMPLATE.format(code=IRCC_CODES[key]).encode("utf-8")
    result = http_call("http://%s/sony/IRCC" % ip, body, headers, "POST")
    result["ok"] = result["status"] == 200
    return result


def probe(ip, psk):
    """Hit a few well-known endpoints and report raw answers, so we can see
    which auth mode this TV generation actually expects."""
    auth = {"X-Auth-PSK": psk} if psk else {}
    rpc = json.dumps(
        {"method": "getRemoteControllerInfo", "id": 1, "params": [], "version": "1.0"}
    ).encode("utf-8")
    checks = [
        ("JSON-RPC getRemoteControllerInfo (com PSK)", "http://%s/sony/system" % ip,
         rpc, dict(auth, **{"Content-Type": "application/json"}), "POST"),
        ("Endpoint IRCC (GET)", "http://%s/sony/IRCC" % ip, None, auth, "GET"),
        ("UPnP :52323/dmr.xml", "http://%s:52323/dmr.xml" % ip, None, {}, "GET"),
        ("UPnP :52323/dd.xml", "http://%s:52323/dd.xml" % ip, None, {}, "GET"),
    ]
    results = []
    for name, url, data, headers, method in checks:
        answer = http_call(url, data, headers, method)
        answer.update({"name": name, "url": url})
        results.append(answer)
    return results


def lan_ip():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("10.255.255.255", 1))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def _send_json(self, code, payload):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path not in ("/", "/index.html"):
            self._send_json(404, {"ok": False, "body": "not found"})
            return
        data = (ROOT / "index.html").read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._send_json(400, {"ok": False, "status": None, "body": "JSON inválido"})
            return

        ip = str(payload.get("ip", "")).strip()
        psk = str(payload.get("psk", "")).strip()
        if not is_private_ipv4(ip):
            self._send_json(400, {
                "ok": False, "status": None,
                "body": "IP inválido: use o IP local da TV, ex.: 192.168.0.50",
            })
            return

        if self.path == "/api/send":
            key = payload.get("key")
            if key not in IRCC_CODES:
                self._send_json(400, {"ok": False, "status": None, "body": "tecla desconhecida"})
                return
            self._send_json(200, send_key(ip, psk, key))
        elif self.path == "/api/probe":
            self._send_json(200, {"results": probe(ip, psk)})
        else:
            self._send_json(404, {"ok": False, "body": "not found"})


def main():
    local_only = "--local" in sys.argv
    host = "127.0.0.1" if local_only else "0.0.0.0"
    server = ThreadingHTTPServer((host, PORT), Handler)
    print("SonyRemote rodando.")
    print("  Neste PC:   http://localhost:%d" % PORT)
    if not local_only:
        print("  No celular: http://%s:%d  (mesma rede Wi-Fi)" % (lan_ip(), PORT))
    print("Ctrl+C para parar.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nEncerrado.")


if __name__ == "__main__":
    main()
