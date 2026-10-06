from __future__ import annotations

import html


def render_login(host: str, error: str = "") -> str:
    err = f'<div class="err">{html.escape(error)}</div>' if error else ""
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Loopback // Admin</title>
<style>
:root{{--bg:#07090b;--panel:#101419;--line:#2b343d;--text:#f4f7f8;--muted:#8e9ba7;--yellow:#ffd600;--cyan:#6ee7ff}}
*{{box-sizing:border-box}} body{{margin:0;min-height:100vh;display:grid;place-items:center;background:radial-gradient(circle at 50% 0,#1b232a,#07090b 55%);font:14px ui-monospace,SFMono-Regular,Consolas,monospace;color:var(--text);padding:24px}}
.card{{width:min(520px,100%);background:linear-gradient(180deg,#12171c,#0b0f12);border:1px solid var(--line);border-radius:22px;padding:30px;box-shadow:0 30px 100px #000b}}
.brand{{color:var(--yellow);letter-spacing:.18em;font-size:12px}} h1{{font-size:28px;margin:16px 0 8px;letter-spacing:-.04em}} p{{color:var(--muted);line-height:1.6}}
input{{width:100%;padding:14px;border:1px solid #39444d;border-radius:10px;background:#080b0e;color:#fff;font:inherit;outline:none}} input:focus{{border-color:var(--yellow);box-shadow:0 0 0 3px #ffd6001f}}
button{{width:100%;margin-top:14px;padding:14px;border:0;border-radius:10px;background:var(--yellow);font:700 13px inherit;cursor:pointer}} .err{{padding:10px 12px;border:1px solid #633;background:#291414;border-radius:9px;color:#ffb5b5;margin:14px 0}}
.small{{font-size:11px;color:#687681;margin-top:14px}} code{{color:var(--cyan)}}
</style></head><body><main class="card"><div class="brand">◆ LOOPBACK CONTROL PLANE</div><h1>Machine administration</h1>
<p>Authenticate with this machine's Loopback token. The dashboard session is stored in an HttpOnly cookie; the token itself is not stored in the browser.</p>{err}
<form method="post" action="/admin/login"><input type="password" name="token" autocomplete="current-password" autofocus required placeholder="Loopback token"><button>OPEN DASHBOARD</button></form>
<div class="small">Host: {html.escape(host)} · retrieve locally with <code>loopback token</code></div></main></body></html>"""


def render_dashboard(host: str) -> str:
    safe_host = html.escape(host)
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Loopback // Control Plane</title>
<style>
:root{{--bg:#07090b;--panel:#0e1317;--panel2:#12181d;--line:#29323a;--text:#f3f6f7;--muted:#84929e;--yellow:#ffd600;--cyan:#62e5ff;--green:#71f6a3;--red:#ff7272}}
*{{box-sizing:border-box}} body{{margin:0;background:#07090b;color:var(--text);font:13px ui-monospace,SFMono-Regular,Consolas,monospace}}
header{{position:sticky;top:0;z-index:5;display:flex;align-items:center;justify-content:space-between;padding:18px 24px;border-bottom:1px solid var(--line);background:#090c0feF;backdrop-filter:blur(16px)}} .brand{{font-weight:800;letter-spacing:.14em;color:var(--yellow)}} .host{{color:var(--muted);font-size:11px}}
main{{max-width:1500px;margin:auto;padding:22px}} .grid{{display:grid;grid-template-columns:repeat(12,1fr);gap:14px}} .card{{grid-column:span 3;background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--line);border-radius:14px;padding:16px;min-width:0}} .wide{{grid-column:span 6}} .full{{grid-column:1/-1}}
.label{{font-size:10px;color:var(--muted);letter-spacing:.12em;text-transform:uppercase}} .big{{font-size:24px;font-weight:750;margin-top:7px}} .ok{{color:var(--green)}} .bad{{color:var(--red)}} .cyan{{color:var(--cyan)}}
table{{width:100%;border-collapse:collapse;margin-top:10px}} th,td{{padding:8px 7px;text-align:left;border-bottom:1px solid #202830;vertical-align:top}} th{{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em}} td{{overflow-wrap:anywhere}}
button,select,input,textarea{{font:inherit;border-radius:8px;border:1px solid #34404a;background:#0a0e11;color:#e9eef1;padding:8px 9px}} textarea{{width:100%;min-height:84px;resize:vertical}} button{{cursor:pointer}} button.primary{{background:var(--yellow);color:#090909;border-color:var(--yellow);font-weight:800}} button.danger{{border-color:#663434;color:#ffaaaa}} .row{{display:flex;gap:8px;align-items:center;flex-wrap:wrap}} .grow{{flex:1}} .pill{{display:inline-block;padding:3px 7px;border:1px solid #34404a;border-radius:99px;font-size:10px;color:var(--muted)}} .log{{max-height:360px;overflow:auto}} .mono{{white-space:pre-wrap}} .muted{{color:var(--muted)}} h2{{font-size:14px;margin:0 0 12px}} a{{color:var(--cyan)}} 
@media(max-width:1000px){{.card,.wide{{grid-column:span 6}}}} @media(max-width:650px){{main{{padding:12px}}.card,.wide{{grid-column:1/-1}}header{{padding:14px}}}}
</style></head>
<body><header><div><span class="brand">◆ LOOPBACK</span> <span class="host">{safe_host}</span></div><div class="row"><span id="updated" class="host"></span><a href="/admin/logout">logout</a></div></header>
<main><div class="grid">
<section class="card"><div class="label">Host</div><div id="hostname" class="big">—</div><div id="uptime" class="muted"></div></section>
<section class="card"><div class="label">Policy</div><div id="profile" class="big">—</div><div class="row" style="margin-top:10px"><select id="profileSelect"><option>standard</option><option>trusted</option><option>read-only</option><option>locked</option></select><button class="primary" onclick="saveProfile()">Apply</button></div></section>
<section class="card"><div class="label">Active work</div><div class="big"><span id="jobs">0</span> <span class="muted">jobs</span></div><div><span id="terms">0</span> terminals · <span id="approvals">0</span> approvals</div></section>
<section class="card"><div class="label">24h MCP calls</div><div id="calls" class="big">0</div><div><span id="failures">0</span> non-success</div></section>

<section class="card wide"><h2>System</h2><div id="system" class="mono muted">loading…</div></section>
<section class="card wide"><h2>Capability status</h2><div id="capabilities" class="mono muted">loading…</div></section>

<section class="card full"><h2>Policy boundaries</h2>
<div class="grid" style="padding:0">
  <div class="card wide" style="grid-column:span 6"><div class="label">Allowed roots · one per line</div><textarea id="allowedRoots"></textarea></div>
  <div class="card wide" style="grid-column:span 6"><div class="label">Denied roots · one per line</div><textarea id="deniedRoots"></textarea></div>
  <div class="card wide" style="grid-column:span 6"><div class="label">Allowed tools · * or one per line</div><textarea id="allowedTools"></textarea></div>
  <div class="card wide" style="grid-column:span 6"><div class="label">Denied tools · one per line</div><textarea id="deniedTools"></textarea></div>
</div>
<div class="row" style="margin-top:10px"><label><input id="allowBrowser" type="checkbox"> enable optional browser automation</label><label><input id="allowDesktop" type="checkbox"> enable native desktop automation</label><button class="primary" onclick="saveBoundaries()">Save boundaries</button></div>
</section>

<section class="card wide"><h2>Nodes</h2><div class="row"><input id="nodeName" placeholder="name"><input id="nodeUrl" class="grow" placeholder="https://host.example/mcp"><input id="nodeToken" type="password" placeholder="bearer token"><button onclick="addNode()">Add</button></div><div id="nodes"></div></section>

<section class="card wide"><h2>Background jobs</h2><div id="jobTable"></div></section>
<section class="card wide"><h2>Persistent terminals</h2><div id="termTable"></div></section>

<section class="card full"><h2>Pending approvals</h2><div id="approvalTable"></div></section>
<section class="card full"><h2>Recent MCP activity</h2><div id="auditTable" class="log"></div></section>
</div></main>
<script>
const j=async(u,o={{}})=>{{const r=await fetch(u,{{...o,headers:{{'Content-Type':'application/json',...(o.headers||{{}})}}}});if(!r.ok)throw new Error(await r.text());return r.json()}};
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
const table=(heads,rows)=>'<table><thead><tr>'+heads.map(x=>'<th>'+esc(x)+'</th>').join('')+'</tr></thead><tbody>'+rows.join('')+'</tbody></table>';
async function refresh(){{
 try{{
  const d=await j('/api/admin/overview');
  hostname.textContent=d.system.hostname; uptime.textContent='uptime '+Math.round(d.system.uptime_seconds/60)+'m';
  profile.textContent=d.policy.profile; profileSelect.value=d.policy.profile;
  allowedRoots.value=(d.policy.allowed_roots||[]).join('\n');
  deniedRoots.value=(d.policy.denied_roots||[]).join('\n');
  allowedTools.value=(d.policy.allowed_tools||['*']).join('\n');
  deniedTools.value=(d.policy.denied_tools||[]).join('\n');
  allowBrowser.checked=!!d.policy.allow_browser;
  allowDesktop.checked=!!d.policy.allow_desktop;
  capabilities.textContent='browser adapter: '+(d.browser.available?'available':'not installed')+' / '+(d.policy.allow_browser?'enabled':'disabled')+'\nnative desktop: '+(d.desktop.available?'available':'unavailable')+' / '+(d.policy.allow_desktop?'enabled':'disabled')+'\ndocument tools: DOCX / XLSX / PDF';
  jobs.textContent=d.jobs.filter(x=>x.running).length; terms.textContent=d.terminals.filter(x=>x.alive).length; approvals.textContent=d.approvals.length;
  calls.textContent=d.audit.total; failures.textContent=d.audit.failures;
  system.textContent='load: '+d.system.load.join('  ')+'\nmemory: '+d.system.memory_used_human+' / '+d.system.memory_total_human+'\ndisk: '+d.system.disk_used_human+' / '+d.system.disk_total_human+'\npython: '+d.system.python;
  nodes.innerHTML=table(['name','url','state',''],d.nodes.map(n=>'<tr><td>'+esc(n.name)+'</td><td>'+esc(n.url)+'</td><td><span class="pill">'+esc(n.enabled?'enabled':'disabled')+'</span></td><td><button class="danger" onclick="removeNode(\''+esc(n.name)+'\')">remove</button></td></tr>'));
  jobTable.innerHTML=table(['id','pid','command','state',''],d.jobs.map(x=>'<tr><td>'+esc(x.id)+'</td><td>'+x.pid+'</td><td>'+esc(x.command)+'</td><td>'+(x.running?'<span class="ok">running</span>':'exit '+esc(x.returncode))+'</td><td>'+(x.running?'<button class="danger" onclick="stopJob(\''+x.id+'\')">stop</button>':'')+'</td></tr>'));
  termTable.innerHTML=table(['id','pid','cwd','state',''],d.terminals.map(x=>'<tr><td>'+esc(x.id)+'</td><td>'+x.pid+'</td><td>'+esc(x.cwd)+'</td><td>'+(x.alive?'<span class="ok">alive</span>':'closed')+'</td><td>'+(x.alive?'<button class="danger" onclick="closeTerm(\''+x.id+'\')">close</button>':'')+'</td></tr>'));
  approvalTable.innerHTML=d.approvals.length?table(['id','request','expires',''],d.approvals.map(x=>'<tr><td>'+esc(x.id)+'</td><td>'+esc(x.summary)+'</td><td>'+new Date(x.expires_at*1000).toLocaleTimeString()+'</td><td><button class="primary" onclick="approve(\''+x.id+'\')">approve once</button> <button onclick="approve(\''+x.id+'\',20,3600)">approve 1h</button></td></tr>')):'<span class="muted">No pending Loopback approvals.</span>';
  auditTable.innerHTML=table(['time','tool','status','ms','target'],d.recent.map(x=>'<tr><td>'+new Date(x.ts*1000).toLocaleTimeString()+'</td><td class="cyan">'+esc(x.tool)+'</td><td>'+esc(x.status)+'</td><td>'+x.duration_ms+'</td><td>'+esc(x.target||'')+'</td></tr>'));
  updated.textContent='updated '+new Date().toLocaleTimeString();
 }}catch(e){{updated.textContent='dashboard error: '+e.message}}
}}
async function saveProfile(){{await j('/api/admin/policy',{{method:'POST',body:JSON.stringify({{profile:profileSelect.value}})}});refresh()}}
const lines=v=>v.split('\n').map(x=>x.trim()).filter(Boolean);
async function saveBoundaries(){{
 await j('/api/admin/policy',{{method:'POST',body:JSON.stringify({{
   allowed_roots:lines(allowedRoots.value),denied_roots:lines(deniedRoots.value),
   allowed_tools:lines(allowedTools.value),denied_tools:lines(deniedTools.value),
   allow_browser:allowBrowser.checked,allow_desktop:allowDesktop.checked
 }})}});
 refresh();
}}
async function approve(id,uses=1,ttl=null){{await j('/api/admin/approvals/'+id+'/approve',{{method:'POST',body:JSON.stringify({{uses,ttl_seconds:ttl}})}});refresh()}}
async function stopJob(id){{await j('/api/admin/jobs/'+id+'/stop',{{method:'POST',body:'{{}}'}});refresh()}}
async function closeTerm(id){{await j('/api/admin/terminals/'+id+'/close',{{method:'POST',body:'{{}}'}});refresh()}}
async function addNode(){{await j('/api/admin/nodes',{{method:'POST',body:JSON.stringify({{name:nodeName.value,url:nodeUrl.value,token:nodeToken.value}})}});nodeToken.value='';refresh()}}
async function removeNode(name){{await j('/api/admin/nodes/'+encodeURIComponent(name),{{method:'DELETE'}});refresh()}}
refresh();setInterval(refresh,3000);
</script></body></html>"""
