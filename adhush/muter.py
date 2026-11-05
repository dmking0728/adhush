"""Ways to mute the TV.

Samsung TVs expose two network controls:

* UPnP RenderingControl on port 9197. It can *set* mute on/off and *read*
  the current mute state, which is exactly what we want. Many Samsung models
  have it; some newer ones don't.
* The remote-control websocket API on port 8002 (what phone remote apps use).
  It can only press the MUTE key, which toggles. The TV asks you to "Allow"
  AdHush the first time; the token is saved so it only asks once.

The "samsung" backend uses UPnP when it answers and the remote API otherwise.
"""

from __future__ import annotations

import logging
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Protocol

log = logging.getLogger(__name__)


class Muter(Protocol):
    name: str

    def set_mute(self, muted: bool) -> None: ...

    def get_mute(self) -> bool | None: ...  # None = unknown


class DryRunMuter:
    name = "dry_run"

    def __init__(self) -> None:
        self.muted = False

    def set_mute(self, muted: bool) -> None:
        log.info("[dry run] would %s the TV", "MUTE" if muted else "UNMUTE")
        self.muted = muted

    def get_mute(self) -> bool | None:
        return self.muted


_SOAP = """<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
<s:Body><u:{action} xmlns:u="urn:schemas-upnp-org:service:RenderingControl:1">
<InstanceID>0</InstanceID><Channel>Master</Channel>{args}
</u:{action}></s:Body></s:Envelope>"""


class SamsungUpnpMuter:
    name = "samsung_upnp"

    def __init__(self, host: str, port: int = 9197, timeout: float = 3):
        self.url = f"http://{host}:{port}/upnp/control/RenderingControl1"
        self.timeout = timeout

    def _call(self, action: str, args: str = "") -> str:
        body = _SOAP.format(action=action, args=args).encode()
        req = urllib.request.Request(
            self.url,
            data=body,
            headers={
                "Content-Type": 'text/xml; charset="utf-8"',
                "SOAPAction": f'"urn:schemas-upnp-org:service:RenderingControl:1#{action}"',
            },
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return resp.read().decode("utf-8", "replace")

    def get_mute(self) -> bool | None:
        m = re.search(r"<CurrentMute>\s*(\d)\s*</CurrentMute>", self._call("GetMute"))
        if not m:
            raise RuntimeError("TV did not report its mute state")
        return m.group(1) == "1"

    def set_mute(self, muted: bool) -> None:
        self._call("SetMute", f"<DesiredMute>{1 if muted else 0}</DesiredMute>")


class SamsungWsMuter:
    """Presses the MUTE key. Since that toggles, we track the state ourselves
    (or read it over UPnP when the TV supports that)."""

    name = "samsung_ws"

    def __init__(self, host: str, token_file: str, probe: SamsungUpnpMuter | None = None):
        self.host = host
        self.token_file = str(Path(token_file).expanduser())
        self.probe = probe
        self.assumed = False
        self._tv = None

    def _client(self):
        if self._tv is None:
            from samsungtvws import SamsungTVWS  # pip install samsungtvws

            Path(self.token_file).parent.mkdir(parents=True, exist_ok=True)
            # The first connection waits while the TV shows "Allow AdHush?",
            # so give a person time to reach the remote.
            paired = Path(self.token_file).exists()
            if not paired:
                log.info("Pairing: choose Allow on the TV within 30 seconds")
            self._tv = SamsungTVWS(
                host=self.host,
                port=8002,
                token_file=self.token_file,
                name="AdHush",
                timeout=5 if paired else 30,
            )
        return self._tv

    def _press_mute(self) -> None:
        try:
            self._client().send_key("KEY_MUTE")
        except Exception:
            self._tv = None  # connection went stale (TV slept); retry once
            self._client().send_key("KEY_MUTE")

    def get_mute(self) -> bool | None:
        if self.probe:
            try:
                return self.probe.get_mute()
            except Exception:
                pass
        return self.assumed

    def set_mute(self, muted: bool) -> None:
        if self.get_mute() == muted:
            self.assumed = muted
            return
        self._press_mute()
        self.assumed = muted


class SamsungAutoMuter:
    name = "samsung"

    def __init__(self, host: str, token_file: str):
        self.upnp = SamsungUpnpMuter(host)
        self.ws = SamsungWsMuter(host, token_file, probe=self.upnp)
        # Many Tizen TVs answer GetMute but reject SetMute with HTTP 500.
        # Once the TV says no, stop asking and use the MUTE key.
        self.upnp_set_supported = True

    def get_mute(self) -> bool | None:
        return self.ws.get_mute()  # UPnP state if available, else tracked

    def set_mute(self, muted: bool) -> None:
        if self.upnp_set_supported:
            try:
                self.upnp.set_mute(muted)
                self.ws.assumed = muted
                return
            except urllib.error.HTTPError as e:
                log.info("TV rejected UPnP SetMute (HTTP %s); using the MUTE key from now on", e.code)
                self.upnp_set_supported = False
            except Exception as e:
                log.debug("UPnP mute failed (%s); using remote-control API", e)
        self.ws.set_mute(muted)


def build_muter(cfg: dict[str, Any]) -> Muter:
    backend = cfg.get("backend", "dry_run")
    if backend == "dry_run":
        return DryRunMuter()
    host = cfg.get("host")
    if not host:
        raise ValueError("tv.host must be set to your TV's IP address")
    token_file = cfg.get("token_file", "~/.config/adhush/tv-token.txt")
    if backend == "samsung":
        return SamsungAutoMuter(host, token_file)
    if backend == "samsung_upnp":
        return SamsungUpnpMuter(host)
    if backend == "samsung_ws":
        return SamsungWsMuter(host, token_file)
    raise ValueError(f"unknown tv.backend: {backend}")
