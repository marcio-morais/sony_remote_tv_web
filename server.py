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
import concurrent.futures
import ipaddress
import json
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = 8000
TIMEOUT_SECONDS = 4
ROOT = Path(__file__).resolve().parent / "public"
STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".webmanifest": "application/manifest+json",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}

# TV discovery: SSDP multicast plus a scan of the local /24 as a backup
SSDP_ADDRESS = ("239.255.255.250", 1900)
SSDP_SEARCH_TARGETS = (
    "urn:schemas-sony-com:service:IRCC:1",
    "urn:schemas-sony-com:service:ScalarWebAPI:1",
    "urn:schemas-upnp-org:device:MediaRenderer:1",
)
SSDP_LISTEN_SECONDS = 3
SCAN_PATH = ":52323/dmr.xml"  # UPnP description served by Sony TVs
SCAN_TIMEOUT_SECONDS = 0.7
SCAN_WORKERS = 64

# Standard Sony IRCC codes (base64 payload sent inside the SOAP envelope)
IRCC_CODES = {
    "POWER": "AAAAAQAAAAEAAAAVAw==",  # toggle; send_key() replaces it, see handle_power()
    "POWER_OFF": "AAAAAQAAAAEAAAAvAw==",  # the only power code this KDL-42W655A obeys
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
    "OPTIONS": "AAAAAgAAAJcAAAA2Aw==",
    "GUIDE": "AAAAAgAAAKQAAABbAw==",
    "SEN": "AAAAAgAAABoAAAB9Aw==",
    "SYNC_MENU": "AAAAAgAAABoAAABYAw==",
    "INFO": "AAAAAQAAAAEAAAA6Aw==",
    "EXIT": "AAAAAQAAAAEAAABjAw==",
    "JUMP": "AAAAAQAAAAEAAAA7Aw==",
    "RED": "AAAAAgAAAJcAAAAlAw==",
    "GREEN": "AAAAAgAAAJcAAAAmAw==",
    "YELLOW": "AAAAAgAAAJcAAAAnAw==",
    "BLUE": "AAAAAgAAAJcAAAAkAw==",
    "PLAY": "AAAAAgAAAJcAAAAaAw==",
    "PAUSE": "AAAAAgAAAJcAAAAZAw==",
    "STOP": "AAAAAgAAAJcAAAAYAw==",
    "REWIND": "AAAAAgAAAJcAAAAbAw==",
    "FORWARD": "AAAAAgAAAJcAAAAcAw==",
}
# Digits: NUM1..NUM9 then NUM0 map to the base64 letters A..J
IRCC_CODES.update({
    "NUM%d" % digit: "AAAAAQAAAAEAAAA%sAw==" % "ABCDEFGHIJ"[(digit - 1) % 10]
    for digit in range(10)
})

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


MAC_PATTERN = re.compile(r"(?:[0-9a-fA-F]{1,2}[:-]){5}[0-9a-fA-F]{1,2}")
MAC_FILE = Path(__file__).resolve().parent / "tv_macs.json"
_mac_lock = threading.Lock()


def _load_macs():
    try:
        return json.loads(MAC_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _read_arp(ip):
    """Look the TV up in the OS neighbour table. Only works while the TV has
    talked to this PC recently, so we remember the result in tv_macs.json."""
    import subprocess

    if sys.platform.startswith("win"):
        command = ["arp", "-a", ip]
    elif sys.platform == "darwin":
        command = ["arp", "-n", ip]
    else:
        command = ["ip", "neigh", "show", ip]
    try:
        flags = 0x08000000 if sys.platform.startswith("win") else 0  # CREATE_NO_WINDOW
        output = subprocess.run(
            command, capture_output=True, text=True, timeout=3, creationflags=flags
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in output.splitlines():
        if ip in line or not sys.platform.startswith("win"):
            match = MAC_PATTERN.search(line)
            if match:
                parts = re.split(r"[:-]", match.group(0))
                mac = ":".join(part.zfill(2) for part in parts).lower()
                if mac not in ("00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff"):
                    return mac
    return None


def learn_mac(ip):
    """Return the TV's MAC, reading the ARP table and caching it on disk."""
    with _mac_lock:
        macs = _load_macs()
        found = _read_arp(ip)
        if found and macs.get(ip) != found:
            macs[ip] = found
            try:
                MAC_FILE.write_text(json.dumps(macs, indent=2), encoding="utf-8")
            except OSError:
                pass
        return found or macs.get(ip)


def wake_on_lan(mac, tv_ip):
    packet = bytes.fromhex("ff" * 6) + bytes.fromhex(mac.replace(":", "")) * 16
    subnet_broadcast = tv_ip.rsplit(".", 1)[0] + ".255"
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for target in ("255.255.255.255", subnet_broadcast):
            for port in (9, 7):
                try:
                    sock.sendto(packet, (target, port))
                except OSError:
                    pass


_last_power = {}  # ip -> "on" / "off": what we last asked the TV to do


def tv_is_reachable(ip):
    try:
        with socket.create_connection((ip, 80), timeout=1.5):
            return True
    except OSError:
        return False


def power_status(ip, psk):
    """'active' / 'standby' when the TV answers getPowerStatus, else None.
    The TV keeps answering HTTP in standby, so a TCP connect says nothing."""
    headers = {"Content-Type": "application/json"}
    if psk:
        headers["X-Auth-PSK"] = psk
    body = json.dumps(
        {"method": "getPowerStatus", "id": 50, "params": [], "version": "1.0"}
    ).encode("utf-8")
    answer = http_call("http://%s/sony/system" % ip, body, headers, "POST")
    try:
        return json.loads(answer["body"])["result"][0]["status"]
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def handle_power(ip, psk):
    """This TV ignores the IRCC toggle, accepts POWER_OFF while on, and in
    standby only wakes with Wake-on-LAN. We ask the TV for its state; when it
    cannot say, we fall back to the last thing we sent."""
    status = power_status(ip, psk)
    if status is None:
        if not tv_is_reachable(ip):
            status = "standby"  # deep standby: the TV's network is down
        else:
            status = "standby" if _last_power.get(ip) == "off" else "active"

    if status == "active":
        learn_mac(ip)
        result = send_key(ip, psk, "POWER_OFF")
        if result["ok"]:
            _last_power[ip] = "off"
        return result

    mac = learn_mac(ip)
    if not mac:
        return {"ok": False, "status": None,
                "body": "MAC da TV desconhecido: use o controle com a TV ligada uma vez"}
    wake_on_lan(mac, ip)
    _last_power[ip] = "on"
    return {"ok": True, "status": 200, "body": "Wake-on-LAN enviado (%s)" % mac}


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


def fetch_text(url, timeout):
    """GET a small text document; returns None on any failure."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.read(16384).decode("utf-8", "replace")
    except Exception:
        return None


def xml_field(text, tag):
    match = re.search(r"<%s>([^<]*)</%s>" % (tag, tag), text)
    return match.group(1).strip() if match else ""


def sony_info(ip, description, hint=""):
    """Returns device info when the UPnP description (or SSDP hint) says Sony."""
    manufacturer = xml_field(description, "manufacturer")
    if "sony" not in ("%s %s" % (manufacturer, hint)).lower():
        return None
    return {
        "ip": ip,
        "name": xml_field(description, "friendlyName") or "Sony TV",
        "model": xml_field(description, "modelName"),
    }


def ssdp_discover(local_ip):
    """Multicast M-SEARCH; returns {ip: info} for Sony devices that answer."""
    found = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(local_ip))
        sock.bind(("", 0))
        for target in SSDP_SEARCH_TARGETS:
            message = (
                "M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\n"
                'MAN: "ssdp:discover"\r\nMX: 2\r\nST: %s\r\n\r\n' % target
            )
            sock.sendto(message.encode("ascii"), SSDP_ADDRESS)

        deadline = time.time() + SSDP_LISTEN_SECONDS
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            sock.settimeout(remaining)
            try:
                data, (ip, _port) = sock.recvfrom(2048)
            except socket.timeout:
                break
            if ip in found or not is_private_ipv4(ip):
                continue

            headers = {}
            for line in data.decode("utf-8", "replace").split("\r\n")[1:]:
                if ":" in line:
                    name, value = line.split(":", 1)
                    headers[name.strip().lower()] = value.strip()
            hint = "%s %s" % (headers.get("server", ""), headers.get("st", ""))

            description = ""
            location = headers.get("location", "")
            # Only follow a description URL that points back at the responder itself
            if urllib.parse.urlparse(location).hostname == ip:
                description = fetch_text(location, 2) or ""
            info = sony_info(ip, description, hint)
            if info:
                found[ip] = info
    except OSError:
        pass  # multicast not available on this network or device
    finally:
        sock.close()
    return found


def scan_subnet(local_ip):
    """Probes every host of the local /24 for a Sony UPnP description."""
    network = ipaddress.ip_network("%s/24" % local_ip, strict=False)
    hosts = [str(host) for host in network.hosts() if str(host) != local_ip]

    def check(ip):
        text = fetch_text("http://%s%s" % (ip, SCAN_PATH), SCAN_TIMEOUT_SECONDS)
        return sony_info(ip, text) if text else None

    with concurrent.futures.ThreadPoolExecutor(SCAN_WORKERS) as pool:
        return [info for info in pool.map(check, hosts) if info]


def discover():
    local_ip = lan_ip()
    if not is_private_ipv4(local_ip):
        return {"devices": [], "localIp": local_ip}

    ssdp_result = {}
    thread = threading.Thread(target=lambda: ssdp_result.update(ssdp_discover(local_ip)))
    thread.start()
    scanned = scan_subnet(local_ip)
    thread.join()

    devices = {}
    for info in list(ssdp_result.values()) + scanned:
        devices.setdefault(info["ip"], info)
    return {"devices": list(devices.values()), "localIp": local_ip}


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
        # Started without a console (pythonw, launchd, systemd): stderr may not exist
        if sys.stderr is not None:
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
        route = urllib.parse.urlparse(self.path).path
        if route == "/":
            route = "/index.html"
        target = (ROOT / route.lstrip("/")).resolve()
        content_type = STATIC_TYPES.get(target.suffix.lower())
        # Only known file types, and only inside public/ (no path traversal)
        if content_type is None or ROOT not in target.parents or not target.is_file():
            self._send_json(404, {"ok": False, "body": "not found"})
            return
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._send_json(400, {"ok": False, "status": None, "body": "JSON inválido"})
            return

        if self.path == "/api/discover":
            self._send_json(200, discover())
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
            if key == "POWER":
                self._send_json(200, handle_power(ip, psk))
            else:
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
