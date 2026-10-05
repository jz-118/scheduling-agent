PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
  <title>智能排班助手</title>
  <style>
    :root{--ink:#17201b;--muted:#64706a;--line:#d9dfdb;--bg:#f5f7f5;--panel:#fff;--green:#126b45;--red:#aa2d2d;--amber:#946200}
    *{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 system-ui,"Microsoft YaHei",sans-serif;letter-spacing:0}
    header{height:58px;background:#173d2c;color:#fff;display:flex;align-items:center;padding:0 28px;gap:12px}header strong{font-size:18px}header span{color:#cde2d6}
    main{max-width:1440px;margin:auto;padding:22px;display:grid;grid-template-columns:360px 1fr;gap:18px}.panel{background:var(--panel);border:1px solid var(--line);border-radius:6px}
    aside{padding:18px;height:max-content}h1{font-size:18px;margin:0 0 16px}label{display:block;color:var(--muted);font-size:12px;margin:12px 0 5px}
    input,textarea{width:100%;border:1px solid #bac4be;border-radius:4px;padding:9px 10px;background:#fff;font:inherit}textarea{min-height:120px;resize:vertical}
    button{border:0;border-radius:4px;padding:10px 14px;font-weight:600;cursor:pointer}button.primary{background:var(--green);color:#fff;width:100%;margin-top:14px}button:disabled{opacity:.55;cursor:wait}
    .examples{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}.examples button{padding:6px 8px;background:#edf2ef;color:#284b3a;font-size:12px}
    section{min-width:0}.status{padding:14px 18px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:center}.badge{padding:3px 8px;border-radius:12px;background:#e8f4ed;color:var(--green);font-size:12px}.badge.fail{background:#fbeaea;color:var(--red)}
    .content{padding:16px;overflow:auto}.empty{color:var(--muted);padding:70px 20px;text-align:center}table{border-collapse:collapse;width:100%;min-width:780px}th,td{text-align:left;border-bottom:1px solid var(--line);padding:10px;vertical-align:top}th{background:#f2f5f3;color:#536159;font-size:12px}.shift{min-width:260px}.ids{display:flex;flex-wrap:wrap;gap:4px}.id{padding:2px 6px;border:1px solid #cdd8d2;border-radius:3px;background:#fafcfb}
    .audit{display:grid;grid-template-columns:repeat(3,minmax(190px,1fr));gap:8px;margin-top:18px}.rule{border:1px solid var(--line);border-left:4px solid var(--green);padding:10px;border-radius:4px}.rule.fail{border-left-color:var(--red)}.rule strong{display:block}.rule small{color:var(--muted)}
    details{margin-top:18px;border-top:1px solid var(--line);padding-top:14px}summary{cursor:pointer;font-weight:700}.reasons{columns:2;margin-top:10px}.reason{break-inside:avoid;padding:6px 0;color:#435049}.reason strong{color:var(--ink)}
    .error{color:var(--red);white-space:pre-wrap}.meta{color:var(--muted);font-size:12px}
    @media(max-width:850px){main{grid-template-columns:1fr;padding:12px}.audit{grid-template-columns:1fr}header{padding:0 14px}}
  </style>
</head>
<body><header><strong>智能排班助手</strong><span>规则驱动 · CP-SAT 求解</span></header>
<main><aside class="panel"><h1>生成排班</h1><label>排班 ID</label><input id="sid" value="DEMO-WEEK"><label>周起始日期</label><input id="week" type="date"><label>自然语言需求</label><textarea id="query">请生成本周排班，尽量照顾员工偏好</textarea><label><input id="force" type="checkbox" style="width:auto"> 强制重新求解</label><button class="primary" id="run">生成并校验</button><label>示例</label><div class="examples"><button data-q="E07 周五晚班不能排，请重新排班">禁排约束</button><button data-q="尽量让 E09 周六上早班">偏好约束</button><button data-q="E07 周五晚班必须安排">必须安排</button></div></aside>
<section class="panel"><div class="status"><div><strong id="title">排班结果</strong><div class="meta" id="meta">等待生成</div></div><span class="badge" id="badge">就绪</span></div><div class="content" id="out"><div class="empty">输入一句排班需求后生成。结果会由 R-01 至 R-09 逐条校验。</div></div></section></main>
<script>
const $=id=>document.getElementById(id);const days=['周一','周二','周三','周四','周五','周六','周日'];
const monday=()=>{let d=new Date(),n=d.getDay()||7;d.setDate(d.getDate()-n+1);return d.toISOString().slice(0,10)};$('week').value=monday();
document.querySelectorAll('[data-q]').forEach(b=>b.onclick=()=>{$('query').value=b.dataset.q});
function cell(rows,day,shift){return rows.filter(x=>x.day===day&&x.shift===shift).map(x=>`<span class="id">${x.employee_id}</span>`).join('')}
function render(data){if(!['approved','already_generated'].includes(data.status)){ $('badge').className='badge fail';$('badge').textContent=data.status||'失败';$('out').innerHTML=`<div class="error">${data.user_message||data.reason||(data.violations||[]).join('\n')||'无法生成排班'}</div>`;return }
 let s=data.solutions[0],rows=s.assignments,a=s.compliance,agent=data.planner||{};$('badge').className='badge';$('badge').textContent=a.compliant?'全部合规':'存在冲突';$('meta').textContent=`版本 ${data.version||'-'} · ${data.replayed?'复用已有结果':'新生成'} · ${agent.mode==='llm'?'LLM Agent '+(agent.model||''): '规则解析降级模式'} · 目标分 ${s.objective_value}`;
 let html='<table><thead><tr><th>日期</th><th>早班 09:00–17:00</th><th>晚班 13:00–21:00</th></tr></thead><tbody>';days.forEach(d=>html+=`<tr><th>${d}</th><td class="shift"><div class="ids">${cell(rows,d,'早班')}</div></td><td class="shift"><div class="ids">${cell(rows,d,'晚班')}</div></td></tr>`);html+='</tbody></table><details><summary>为什么这样安排</summary><div class="meta">'+s.explanation.summary+'</div><div class="reasons">';s.explanation.shifts.forEach(x=>html+=`<div class="reason"><strong>${x.day}${x.shift}</strong>：${x.reason}${x.matched_preferences.length?`；匹配偏好 ${x.matched_preferences.join(', ')}`:''}</div>`);html+='</div></details><div class="audit">';a.rules.forEach(r=>html+=`<div class="rule ${r.status==='fail'?'fail':''}"><strong>${r.rule_id} · ${r.status==='pass'?'通过':'不通过'}</strong><small>${r.description}</small>${r.violations.length?`<div class="error">${r.violations.join('<br>')}</div>`:''}</div>`);$('out').innerHTML=html+'</div>'}
$('run').onclick=async()=>{let b=$('run');b.disabled=true;b.textContent='求解中…';$('badge').textContent='运行中';try{let r=await fetch('/schedule/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({schedule_id:$('sid').value,week_start:$('week').value,query:$('query').value,force:$('force').checked})});let d=await r.json();render(d)}catch(e){render({status:'error',reason:String(e)})}finally{b.disabled=false;b.textContent='生成并校验'}};
</script></body></html>"""
