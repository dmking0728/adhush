"""A small setup page served by the Pi: see the camera, draw the ad-badge boxes,
pause AdHush, and test muting. Open http://<pi-address>:8080 on your phone."""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from adhush.app import App

log = logging.getLogger(__name__)

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AdHush</title>
<style>
:root{--bg:#0f1115;--card:#181b22;--fg:#e8e9ec;--muted:#9aa0aa;--accent:#5b8cff;--bad:#ff5b6b;--ok:#3ccf7a}
*{box-sizing:border-box}body{margin:0;font:15px/1.45 system-ui,sans-serif;background:var(--bg);color:var(--fg)}
main{max-width:980px;margin:0 auto;padding:16px}
h1{font-size:22px;margin:4px 0 12px}h1 span{color:var(--muted);font-weight:400;font-size:14px}
.card{background:var(--card);border-radius:12px;padding:14px;margin-bottom:14px}
.row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
button{background:#262b36;color:var(--fg);border:0;border-radius:8px;padding:9px 14px;font:inherit;cursor:pointer}
button.primary{background:var(--accent);color:#fff}button:hover{filter:brightness(1.15)}
.pill{padding:4px 10px;border-radius:999px;background:#262b36;font-size:13px}
.pill.ad{background:var(--bad);color:#fff}.pill.on{background:var(--ok);color:#06210f}
#wrap{position:relative;width:100%;touch-action:none;user-select:none}
#wrap img{width:100%;display:block;border-radius:8px}
#wrap canvas{position:absolute;inset:0;width:100%;height:100%;cursor:crosshair}
table{width:100%;border-collapse:collapse;font-size:13px}td,th{padding:5px 6px;text-align:left;border-bottom:1px solid #262b36;vertical-align:top}
code{color:#c9d4ff;word-break:break-word}.hint{color:var(--muted);font-size:13px}
</style></head><body><main>
<h1>AdHush <span>mutes the ads, keeps the show</span></h1>
<div class="card row">
  <span id="adpill" class="pill">…</span><span id="mutepill" class="pill">…</span>
  <span id="backend" class="pill"></span><span id="speed" class="pill"></span>
  <span style="flex:1"></span>
  <button id="toggle">Pause</button>
  <button onclick="post('/api/test-mute',{muted:true})">Test mute</button>
  <button onclick="post('/api/test-mute',{muted:false})">Test unmute</button>
</div>
<div class="card">
  <div id="wrap"><img id="snap" alt="camera"><canvas id="cv"></canvas></div>
  <p class="hint">Wait for an ad, then drag a box around the "Ad" badge. Tight boxes are faster and
  more accurate. Add one per app if their badges sit in different places, then press Save.</p>
  <div class="row"><button class="primary" onclick="saveRegions()">Save boxes</button>
  <button onclick="regions=[];draw()">Clear all</button>
  <button onclick="freeze=!freeze;this.textContent=freeze?'Unfreeze picture':'Freeze picture'">Freeze picture</button>
  <a href="/raw.jpg" download="adhush-frame.jpg"><button>Download frame</button></a>
  <span id="saved" class="hint"></span></div>
</div>
<div class="card"><table><thead><tr><th>Box</th><th>What OCR read</th><th>Match</th><th></th></tr></thead>
<tbody id="rows"></tbody></table></div>
<div class="card"><b>Recent</b><div id="events" class="hint"></div></div>
</main><script>
let regions=[], freeze=false, dragging=null, status={};
const img=document.getElementById('snap'), cv=document.getElementById('cv'), ctx=cv.getContext('2d');
async function post(u,b){const r=await fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})});if(!r.ok)alert(await r.text());return r}
function refreshImg(){if(!freeze&&!dragging)img.src='/snapshot.jpg?t='+Date.now()}
async function refresh(){
  try{status=await (await fetch('/api/status')).json()}catch(e){return}
  if(!dragging&&!window._edited){regions=status.regions.map(r=>({...r}))}
  const ad=document.getElementById('adpill');ad.textContent=status.is_ad?'AD ON SCREEN':'No ad';ad.className='pill'+(status.is_ad?' ad':'');
  const m=document.getElementById('mutepill');m.textContent=!status.enabled?'Paused':status.suppressed?'Standing down (safety)':status.muted_by_adhush?'Muted by AdHush':'Watching';m.className='pill'+(status.enabled&&!status.suppressed?' on':'');
  document.getElementById('backend').textContent='TV: '+status.backend;
  document.getElementById('speed').textContent=status.check_seconds!=null?('check '+status.check_seconds+'s'):'';
  document.getElementById('toggle').textContent=status.enabled?'Pause':'Resume';
  const byName={};(status.region_results||[]).forEach(r=>byName[r.name]=r);
  document.getElementById('rows').innerHTML=regions.map((r,i)=>{const x=byName[r.name]||{};
    return `<tr><td>${esc(r.name)}</td><td><code>${x.skipped?'(blank)':esc(x.text||'')}</code></td><td>${x.matched?'<b style="color:var(--bad)">AD</b>':''}</td><td><button onclick="regions.splice(${i},1);window._edited=true;draw()">Delete</button></td></tr>`}).join('');
  document.getElementById('events').innerHTML=(status.events||[]).slice(0,10).map(e=>new Date(e.t*1000).toLocaleTimeString()+' — '+e.event).join('<br>')||'Nothing yet';
  draw();
}
function esc(s){return String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function draw(){cv.width=cv.clientWidth;cv.height=cv.clientHeight;ctx.clearRect(0,0,cv.width,cv.height);
  ctx.lineWidth=2;ctx.font='13px system-ui';
  const all=regions.concat(dragging?[dragging]:[]);
  for(const r of all){const [x,y,w,h]=r.box;ctx.strokeStyle=r===dragging?'#5b8cff':'#ffd84d';ctx.strokeRect(x*cv.width,y*cv.height,w*cv.width,h*cv.height);ctx.fillStyle=ctx.strokeStyle;ctx.fillText(r.name||'',x*cv.width+4,y*cv.height-4)}}
function pt(e){const b=cv.getBoundingClientRect();const p=e.touches?e.touches[0]:e;return [Math.min(1,Math.max(0,(p.clientX-b.left)/b.width)),Math.min(1,Math.max(0,(p.clientY-b.top)/b.height))]}
let start=null;
function down(e){e.preventDefault();start=pt(e);dragging={name:'',box:[start[0],start[1],0,0]}}
function move(e){if(!dragging)return;e.preventDefault();const p=pt(e);dragging.box=[Math.min(start[0],p[0]),Math.min(start[1],p[1]),Math.abs(p[0]-start[0]),Math.abs(p[1]-start[1])];draw()}
function up(){if(!dragging)return;const d=dragging;dragging=null;
  if(d.box[2]>0.01&&d.box[3]>0.01){const n=prompt('Name this box (e.g. hulu, hbo, peacock):','box-'+(regions.length+1));if(n!==null){d.name=n||('box-'+(regions.length+1));regions.push(d);window._edited=true}}draw()}
cv.addEventListener('mousedown',down);window.addEventListener('mousemove',move);window.addEventListener('mouseup',up);
cv.addEventListener('touchstart',down,{passive:false});cv.addEventListener('touchmove',move,{passive:false});cv.addEventListener('touchend',up);
async function saveRegions(){const r=await post('/api/regions',{regions});if(r.ok){window._edited=false;document.getElementById('saved').textContent='Saved.';setTimeout(()=>document.getElementById('saved').textContent='',2500);refresh()}}
document.getElementById('toggle').onclick=async()=>{await post('/api/enabled',{enabled:!status.enabled});refresh()};
img.onload=draw;window.onresize=draw;
refresh();refreshImg();setInterval(refresh,2000);setInterval(refreshImg,2500);
</script></body></html>"""


def make_handler(app: "App"):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quiet: the page polls every 2s
            log.debug("web: " + fmt, *args)

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200) -> None:
            self._send(code, json.dumps(obj).encode(), "application/json")

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/":
                self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            elif path == "/api/status":
                self._json(app.status())
            elif path in ("/snapshot.jpg", "/raw.jpg"):
                data = app.snapshot_jpeg() if path == "/snapshot.jpg" else app.raw_jpeg()
                if data is None:
                    self._send(503, b"no frame yet", "text/plain")
                else:
                    self._send(200, data, "image/jpeg")
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            try:
                length = min(int(self.headers.get("Content-Length") or 0), 64_000)
                body = json.loads(self.rfile.read(length) or b"{}")
                if self.path == "/api/regions":
                    app.set_regions(body.get("regions"))
                elif self.path == "/api/enabled":
                    app.set_enabled(bool(body.get("enabled")))
                elif self.path == "/api/test-mute":
                    app.test_mute(bool(body.get("muted")))
                else:
                    return self._send(404, b"not found", "text/plain")
                self._json({"ok": True})
            except Exception as e:
                log.warning("web request failed: %s", e)
                self._send(400, str(e).encode(), "text/plain")

    return Handler


def start_web(app: "App", host: str, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(app))
    threading.Thread(target=server.serve_forever, daemon=True, name="web").start()
    log.info("Setup page: http://%s:%d", "<this-device-ip>" if host == "0.0.0.0" else host, port)
    return server
