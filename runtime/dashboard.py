from __future__ import annotations

import html


def login_html(host: str, error: str = "") -> str:
    safe_host = html.escape(host)
    safe_error = html.escape(error)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Loopback // Admin</title>
<style>
:root{{--bg:#060809;--panel:#0e1317;--line:#273139;--text:#f4f7f8;--muted:#83919b;--yellow:#ffd600;--cyan:#70e6ff}}
*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 50% -20%,#26313a 0,#0a0d10 42%,#050607 75%);color:var(--text);font:14px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;min-height:100vh;display:grid;place-items:center;padding:24px}}
.card{{width:min(560px,100%);background:linear-gradient(180deg,#12181d,#0b0f12);border:1px solid var(--line);border-radius:22px;padding:32px;box-shadow:0 26px 90px #000b}}
.brand{{color:var(--yellow);letter-spacing:.16em;font-size:12px}}h1{{font-size:30px;margin:16px 0 8px;letter-spacing:-.04em}}p{{color:var(--muted);line-height:1.6}}
.meta{{border:1px solid var(--line);background:#07090b;border-radius:12px;padding:12px 14px;color:var(--cyan);margin:18px 0}}
label{{display:block;margin:20px 0 8px;font-size:12px;letter-spacing:.08em;text-transform:uppercase}}input{{width:100%;border:1px solid #34414b;background:#07090b;color:white;padding:14px;border-radius:10px;font:inherit}}button{{width:100%;border:0;border-radius:10px;background:var(--yellow);color:#080808;padding:14px;font-weight:800;margin-top:14px;cursor:pointer}}.error{{color:#ff8a8a}}
</style></head><body><main class="card"><div class="brand">◆ LOOPBACK CONTROL PLANE</div><h1>Admin console</h1>
<p>Authenticate with this machine's Loopback token. The browser receives an HttpOnly admin session; the token is not stored in localStorage.</p>
<div class="meta">{safe_host}</div>{f'<p class="error">{safe_error}</p>' if safe_error else ''}
<form method="post" action="/admin/login"><label>Loopback token</label><input type="password" name="token" autocomplete="current-password" autofocus required>
<button type="submit">Open dashboard</button></form></main></body></html>"""


def dashboard_html(host: str) -> str:
    safe_host = html.escape(host)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Loopback // Control Plane</title>
<style>
:root{{--bg:#07090b;--panel:#0d1216;--panel2:#111820;--line:#26323b;--text:#eef4f6;--muted:#82919c;--yellow:#ffd600;--cyan:#69e6ff;--green:#79f2b0;--red:#ff7b8b;--orange:#ffb86b}}
*{{box-sizing:border-box}}body{{margin:0;background:#07090b;color:var(--text);font:13px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}}
header{{position:sticky;top:0;z-index:10;background:#07090bf2;backdrop-filter:blur(12px);border-bottom:1px solid var(--line);padding:16px 22px;display:flex;gap:18px;align-items:center;justify-content:space-between}}
.brand{{font-weight:800;letter-spacing:.12em;color:var(--yellow)}}.host{{color:var(--muted);font-size:11px}}main{{max-width:1500px;margin:auto;padding:22px}}
.grid{{display:grid;grid-template-columns:repeat(12,1fr);gap:14px}}.card{{grid-column:span 4;background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--line);border-radius:16px;padding:17px;min-width:0}}.wide{{grid-column:span 8}}.full{{grid-column:1/-1}}
h2{{font-size:12px;text-transform:uppercase;letter-spacing:.11em;margin:0 0 14px;color:#bac7ce}}.stat{{font-size:30px;font-weight:800;letter-spacing:-.05em}}.muted{{color:var(--muted)}}.ok{{color:var(--green)}}.bad{{color:var(--red)}}.warn{{color:var(--orange)}}
table{{width:100%;border-collapse:collapse;font-size:12px}}th,td{{text-align:left;padding:8px 7px;border-bottom:1px solid #1e282f;vertical-align:top}}th{{color:#91a0a9;font-weight:500}}code{{color:var(--cyan);word-break:break-all}}
button,select,input,textarea{{font:inherit;border:1px solid #34414a;background:#090d10;color:var(--text);border-radius:8px;padding:8px 10px}}textarea{{width:100%;min-height:86px;resize:vertical}}button{{cursor:pointer}}button.primary{{background:var(--yellow);color:#070707;border-color:var(--yellow);font-weight:800}}button.danger{{border-color:#6f3038;color:#ff9aa5}}.toolbar{{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:12px}}.pill{{border:1px solid var(--line);border-radius:999px;padding:4px 8px;font-size:11px}}.scroll{{max-height:340px;overflow:auto}}pre{{white-space:pre-wrap;word-break:break-word;margin:0;color:#c8d2d8;font-size:11px}}.toggle{{display:flex;gap:8px;align-items:center;margin:6px 0}}.toggle input{{width:auto}}
@media(max-width:1000px){{.card,.wide{{grid-column:1/-1}}}}
</style></head><body>
<header><div><div class="brand">◆ LOOPBACK CONTROL PLANE</div><div class="host">{safe_host}</div></div>
<div class="toolbar"><span id="profileBadge" class="pill">profile …</span><button onclick="refreshAll()">Refresh</button><form method="post" action="/admin/logout"><button>Logout</button></form></div></header>
<main><div class="grid">
<section class="card"><h2>Host</h2><div id="hostStat" class="stat">…</div><div id="hostDetail" class="muted"></div></section>
<section class="card"><h2>Resources</h2><div id="resourceStat" class="stat">…</div><div id="resourceDetail" class="muted"></div></section>
<section class="card"><h2>Runtime</h2><div id="runtimeStat" class="stat">…</div><div id="runtimeDetail" class="muted"></div></section>

<section class="card wide"><h2>Jobs & terminals</h2><div id="jobs" class="scroll"></div></section>
<section class="card"><h2>Policy</h2><div class="toolbar"><select id="profile"><option>read-only</option><option>standard</option><option>trusted</option></select><button class="primary" onclick="saveProfile()">Apply</button></div><div id="tools"></div><h2 style="margin-top:18px">Filesystem</h2><label class="muted">Allowed paths (one per line)</label><textarea id="allowPaths"></textarea><label class="muted">Denied paths (one per line)</label><textarea id="denyPaths"></textarea><button style="margin-top:8px" onclick="savePaths()">Save paths</button></section>

<section class="card wide"><h2>Processes</h2><div class="toolbar"><input id="procFilter" placeholder="Filter processes" oninput="renderProcesses()"><button onclick="loadProcesses()">Reload</button></div><div id="processes" class="scroll"></div></section>
<section class="card"><h2>Nodes</h2><div id="nodes"></div><div class="toolbar" style="margin-top:12px"><input id="nodeName" placeholder="name"><input id="nodeTarget" placeholder="SSH target"><button onclick="addNode()">Add</button></div><pre id="nodeHealth" class="muted"></pre></section>

<section class="card"><h2>Pending approvals</h2><div id="approvals"></div></section>
<section class="card"><h2>Connected clients</h2><div id="clients" class="scroll"></div></section>
<section class="card"><h2>Job output</h2><pre id="jobOutput" class="scroll muted">Select a running or completed job.</pre></section>
<section class="card full"><h2>Audit log</h2><div id="audit" class="scroll"></div></section>
<section class="card full"><h2>Security & connectivity</h2><div id="security"></div></section>
</div></main>
<script>
let procRows=[];
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
async function api(path,opts={{}}){{const r=await fetch(path,{{credentials:'same-origin',...opts,headers:{{'Content-Type':'application/json',...(opts.headers||{{}})}}}});if(!r.ok)throw new Error(await r.text());return r.json()}}
function bytes(n){{if(!n&&n!==0)return'—';const u=['B','KB','MB','GB','TB'];let i=0;while(n>=1024&&i<u.length-1){{n/=1024;i++}}return n.toFixed(i?1:0)+' '+u[i]}}
async function loadOverview(){{const d=await api('/api/overview');document.querySelector('#hostStat').textContent=d.hostname;document.querySelector('#hostDetail').innerHTML='user '+esc(d.user)+' · '+esc(d.platform);const m=d.metrics||{{}};document.querySelector('#resourceStat').textContent=m.available?Math.round(m.cpu_percent)+'% CPU':'n/a';document.querySelector('#resourceDetail').innerHTML=m.available?('RAM '+m.memory.percent+'% · disk '+m.disk_home.percent+'% · '+m.process_count+' processes'):'psutil unavailable';document.querySelector('#runtimeStat').textContent=d.jobs.running+' jobs';document.querySelector('#runtimeDetail').textContent=d.terminals.running+' terminals · '+d.clients+' clients';document.querySelector('#security').innerHTML='<table><tr><th>Ingress</th><td>'+esc(d.ingress)+'</td><th>MCP</th><td><code>'+esc(d.mcp_url)+'</code></td></tr><tr><th>Auth</th><td>Bearer + OAuth/PKCE</td><th>Origin</th><td>127.0.0.1:'+esc(d.port)+'</td></tr></table>'}}
async function loadPolicy(){{const p=await api('/api/policy');document.querySelector('#profile').value=p.profile;document.querySelector('#profileBadge').textContent='profile '+p.profile;document.querySelector('#tools').innerHTML=Object.entries(p.effective_tools).map(([k,v])=>'<label class="toggle"><input type="checkbox" '+(v?'checked':'')+' onchange="setTool(\''+esc(k)+'\',this.checked)"> '+esc(k)+'</label>').join('');document.querySelector('#allowPaths').value=(p.filesystem.allow||[]).join('\n');document.querySelector('#denyPaths').value=(p.filesystem.deny||[]).join('\n')}}
async function saveProfile(){{await api('/api/policy/profile',{{method:'POST',body:JSON.stringify({{profile:document.querySelector('#profile').value}})}});await loadPolicy()}}
async function savePaths(){{const allow=document.querySelector('#allowPaths').value.split('\n').map(x=>x.trim()).filter(Boolean),deny=document.querySelector('#denyPaths').value.split('\n').map(x=>x.trim()).filter(Boolean);await api('/api/policy/paths',{{method:'POST',body:JSON.stringify({{allow,deny}})}});await loadPolicy()}}
async function setTool(tool,enabled){{await api('/api/policy/tool',{{method:'POST',body:JSON.stringify({{tool,enabled}})}});await loadPolicy()}}
async function loadJobs(){{const d=await api('/api/jobs');const rows=[...d.jobs.map(x=>({{kind:'job',...x}})),...d.terminals.map(x=>({{kind:'terminal',...x}}))];document.querySelector('#jobs').innerHTML='<table><tr><th>Type</th><th>ID</th><th>PID</th><th>Status</th><th>Command/CWD</th><th></th></tr>'+rows.map(x=>'<tr><td>'+x.kind+'</td><td><code>'+esc(x.id)+'</code></td><td>'+esc(x.pid)+'</td><td class="'+(x.running?'ok':'muted')+'">'+(x.running?'running':'done')+'</td><td>'+esc(x.command||x.cwd||'')+'</td><td>'+(x.kind==='job'?'<button onclick="showJob(\''+x.id+'\')">Output</button> ':'')+(x.running?'<button class="danger" onclick="stopItem(\''+x.kind+'\',\''+x.id+'\')">Stop</button>':'')+'</td></tr>').join('')+'</table>'}}
async function showJob(id){{const d=await api('/api/jobs/'+encodeURIComponent(id)+'/output?offset=0&max_bytes=131072');document.querySelector('#jobOutput').textContent=d.content||'(no output)'}}
async function stopItem(kind,id){{await api('/api/'+(kind==='job'?'jobs':'terminals')+'/'+encodeURIComponent(id)+'/stop',{{method:'POST',body:'{{}}'}});await loadJobs()}}
async function loadProcesses(){{const d=await api('/api/processes?limit=300');procRows=d.processes;renderProcesses()}}
function renderProcesses(){{const q=document.querySelector('#procFilter').value.toLowerCase();const rows=procRows.filter(x=>(x.name+' '+x.cmdline+' '+x.pid).toLowerCase().includes(q));document.querySelector('#processes').innerHTML='<table><tr><th>PID</th><th>Name</th><th>CPU</th><th>RAM</th><th>Command</th><th></th></tr>'+rows.map(x=>'<tr><td>'+x.pid+'</td><td>'+esc(x.name)+'</td><td>'+esc(x.cpu_percent)+'</td><td>'+esc(x.memory_percent)+'%</td><td><code>'+esc(x.cmdline).slice(0,160)+'</code></td><td><button class="danger" onclick="killProc('+x.pid+')">TERM</button></td></tr>').join('')+'</table>'}}
async function killProc(pid){{if(!confirm('Send SIGTERM to PID '+pid+'?'))return;await api('/api/processes/'+pid+'/kill',{{method:'POST',body:JSON.stringify({{signal:15}})}});await loadProcesses()}}
async function loadNodes(){{const d=await api('/api/nodes');document.querySelector('#nodes').innerHTML=d.nodes.map(n=>'<div class="toolbar"><span class="pill">'+esc(n.name)+'</span><span class="muted">'+esc(n.transport)+(n.target?' · '+esc(n.target):'')+'</span><button onclick="healthNode(\''+esc(n.name)+'\')">Health</button>'+(n.name!=='local'?'<button class="danger" onclick="removeNode(\''+esc(n.name)+'\')">Remove</button>':'')+'</div>').join('')}}
async function healthNode(name){{const d=await api('/api/nodes/'+encodeURIComponent(name)+'/health');document.querySelector('#nodeHealth').textContent=name+': '+(d.ok?'healthy':'unreachable')+' · '+d.latency_ms+' ms'+(d.error?' · '+d.error:'')}}
async function addNode(){{const name=document.querySelector('#nodeName').value.trim(),target=document.querySelector('#nodeTarget').value.trim();if(!name||!target)return;await api('/api/nodes',{{method:'POST',body:JSON.stringify({{name,transport:'ssh',target}})}});document.querySelector('#nodeName').value='';document.querySelector('#nodeTarget').value='';await loadNodes()}}
async function removeNode(name){{await api('/api/nodes/'+encodeURIComponent(name),{{method:'DELETE'}});await loadNodes()}}
async function loadApprovals(){{const d=await api('/api/approvals');document.querySelector('#approvals').innerHTML=d.approvals.length?d.approvals.map(a=>'<div style="margin-bottom:12px"><code>'+esc(a.command)+'</code><br><button class="primary" onclick="approve(\''+a.id+'\')">Approve once</button></div>').join(''):'<span class="muted">No pending approvals</span>'}}
async function approve(id){{await api('/api/approvals/'+encodeURIComponent(id)+'/approve',{{method:'POST',body:'{{}}'}});await loadApprovals()}}
async function loadAudit(){{const d=await api('/api/audit?limit=200');document.querySelector('#audit').innerHTML='<table><tr><th>Time</th><th>Event</th><th>Tool</th><th>Status</th><th>Detail</th></tr>'+d.events.slice().reverse().map(x=>'<tr><td>'+new Date(x.ts*1000).toLocaleString()+'</td><td>'+esc(x.event)+'</td><td>'+esc(x.tool||'')+'</td><td class="'+(x.status==='ok'?'ok':'warn')+'">'+esc(x.status)+'</td><td><pre>'+esc(JSON.stringify(x.detail))+'</pre></td></tr>').join('')+'</table>'}}
async function loadClients(){{const d=await api('/api/clients');document.querySelector('#clients').innerHTML=d.clients.length?'<table><tr><th>Client</th><th>Requests</th><th>Last seen</th></tr>'+d.clients.map(x=>'<tr><td><code>'+esc(x.user_agent).slice(0,70)+'</code></td><td>'+x.requests+'</td><td>'+new Date(x.last_seen*1000).toLocaleTimeString()+'</td></tr>').join('')+'</table>':'<span class="muted">No authenticated MCP requests observed since startup.</span>'}}
async function refreshAll(){{for(const f of [loadOverview,loadPolicy,loadJobs,loadProcesses,loadNodes,loadApprovals,loadClients,loadAudit]){{try{{await f()}}catch(e){{console.error(e)}}}}}}
refreshAll();setInterval(()=>{{loadOverview();loadJobs();loadApprovals()}},5000);
</script></body></html>"""
