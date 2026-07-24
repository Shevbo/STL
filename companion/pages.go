package main

// Two local pages the proxy serves without STL: the one-time pairing form and
// the offline notice. Everything else (the real panel) comes from STL, so its
// look can change with a normal frontend deploy and no exe rebuild.

import (
	"fmt"
	"html"
)

const pageCSS = `
 :root{--ink:#1a1815;--muted:#4a453d;--faint:#6b655b;--rule:rgba(26,24,21,.22);--down:#8d1a1a}
 *{box-sizing:border-box}
 html,body{margin:0;background:transparent;color:var(--ink);
  font:9px/1.5 Consolas,"Cascadia Mono",monospace;-webkit-font-smoothing:antialiased;
  overflow:hidden;user-select:none}
 body{padding:10px 11px}
 h1{font:400 10px/1 Bahnschrift,"Segoe UI Semilight",sans-serif;letter-spacing:.18em;
  text-transform:uppercase;color:var(--muted);margin:0 0 8px}
 p{margin:0 0 7px;color:var(--muted)}
 b{font-weight:400;color:var(--ink)}
 .f{display:flex;gap:5px;align-items:center}
 input{flex:1;font:400 11px/1 Consolas,monospace;letter-spacing:.22em;text-transform:uppercase;
  padding:4px 6px;border:1px solid var(--rule);background:rgba(255,255,255,.35);color:var(--ink);
  outline:none}
 input:focus{border-color:var(--ink)}
 button{font:400 9px/1 Bahnschrift,sans-serif;letter-spacing:.12em;text-transform:uppercase;
  padding:5px 9px;border:1px solid var(--rule);background:rgba(255,255,255,.35);
  color:var(--ink);cursor:pointer}
 button:hover{border-color:var(--ink)}
 .err{color:var(--down);margin-top:6px;min-height:12px}
 .ft{color:var(--faint);margin-top:8px}
`

func pairingHTML(account string) string {
	return fmt.Sprintf(`<!doctype html><html lang="ru"><meta charset="utf-8">
<title>STL Companion</title><style>%s</style>
<h1>STL · сопряжение</h1>
<p>Учётная запись: <b>%s</b></p>
<p>В STL, на странице вотчера, нажмите «Добавить компаньона» для этой учётной
записи и введите выданный код. Спрашивается один раз.</p>
<div class="f">
  <input id="code" maxlength="8" autocomplete="off" spellcheck="false" placeholder="КОД">
  <button id="go">Связать</button>
</div>
<div class="err" id="err"></div>
<p class="ft">Код живёт 15 минут и срабатывает один раз.</p>
<script>
const err=document.getElementById('err'),code=document.getElementById('code');
code.focus();
async function pair(){
  err.textContent='';
  const v=code.value.trim();
  if(v.length<4){err.textContent='Введите код';return}
  try{
    const r=await fetch('/pair',{method:'POST',body:JSON.stringify({code:v})});
    const d=await r.json();
    if(!r.ok||d.error){err.textContent=d.error||('HTTP '+r.status);return}
    location.href='/';
  }catch(e){err.textContent=String(e)}
}
document.getElementById('go').onclick=pair;
code.addEventListener('keydown',(e)=>{if(e.key==='Enter')pair()});
</script></html>`, pageCSS, html.EscapeString(account))
}

func offlineHTML(err error) string {
	return fmt.Sprintf(`<!doctype html><html lang="ru"><meta charset="utf-8">
<title>STL Companion</title><style>%s</style>
<h1>STL · нет связи</h1>
<p>Панель не загрузилась с сервера.</p>
<p class="err">%s</p>
<button onclick="location.reload()">Повторить</button>
</html>`, pageCSS, html.EscapeString(err.Error()))
}
