"""Ultra-Luxury Dark-Gold RTL Arabic HTML Templates for:
  1. User Telegram Mini App (`/`) — TradingView Lightweight Charts + Live Signals +
     1-Tap Lot Calculator + Risk Profile + Journal + Leaderboard + News + Gifts + Affiliates
  2. Admin Web Dashboard (`/admin`) — Live KPIs, 1-Tap System Toggles, Pending Payments,
     Pending Affiliate Payouts, Instant Gift Code Generator & Provider Health Monitor
"""
from __future__ import annotations

from fysb import config


def render_user_miniapp_html() -> str:
    return f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no"/>
<title>{config.BRAND} — Royal Mini App v{config.VERSION}</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<script src="https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"></script>
<style>
:root {{
  --bg:#0b0e14; --panel:#121722; --card:#171d2c; --border:#252f45;
  --gold:#e0b84c; --gold2:#f5d57a; --green:#16c784; --red:#ea3943;
  --blue:#3b82f6; --text:#eef2ff; --muted:#94a3b8;
}}
*{{box-sizing:border-box;margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Tahoma,sans-serif}}
body{{background:var(--bg);color:var(--text);padding-bottom:78px;min-height:100vh}}
.topbar{{background:linear-gradient(135deg,#121827,#1c1910);border-bottom:1px solid rgba(224,184,76,.35);padding:14px 16px;display:flex;justify-content:space-between;align-items:center;position:sticky;top:0;z-index:20}}
.brand{{font-weight:800;font-size:16px;color:var(--gold);letter-spacing:.3px}}
.pill{{background:rgba(224,184,76,.14);border:1px solid rgba(224,184,76,.4);color:var(--gold2);padding:4px 10px;border-radius:999px;font-size:12px;font-weight:700}}
.wrap{{padding:14px;max-width:760px;margin:0 auto}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px}}
.kpi{{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:12px}}
.kpi .lbl{{color:var(--muted);font-size:12px;margin-bottom:4px}}
.kpi .val{{font-size:18px;font-weight:800;color:var(--text)}}
.kpi .val.gold{{color:var(--gold)}} .kpi .val.green{{color:var(--green)}} .kpi .val.red{{color:var(--red)}}
.card{{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:14px;margin-bottom:12px}}
.card h3{{font-size:15px;color:var(--gold2);margin-bottom:10px;display:flex;justify-content:space-between;align-items:center}}
#tvChart{{width:100%;height:270px;border-radius:12px;overflow:hidden;border:1px solid var(--border);background:#0d111a}}
.sig-row{{border:1px solid var(--border);border-radius:12px;padding:11px;margin-bottom:9px;background:var(--panel)}}
.sig-head{{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px}}
.badge{{padding:3px 9px;border-radius:8px;font-size:11px;font-weight:800}}
.b-buy{{background:rgba(22,199,132,.18);color:var(--green);border:1px solid rgba(22,199,132,.4)}}
.b-sell{{background:rgba(234,57,67,.18);color:var(--red);border:1px solid rgba(234,57,67,.4)}}
.lvls{{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;font-size:11px;margin:8px 0}}
.lvl{{background:#0d121d;padding:6px;border-radius:8px;text-align:center}}
.lvl span{{display:block;color:var(--muted);font-size:10px}}
.btn-row{{display:flex;gap:8px;margin-top:8px;flex-wrap:wrap}}
.btn{{flex:1;border:none;border-radius:10px;padding:9px 10px;font-weight:700;font-size:12px;cursor:pointer;color:#fff;background:var(--blue)}}
.btn.gold{{background:linear-gradient(135deg,#c99a2e,#e5c158);color:#0b0e14}}
.btn.green{{background:#129e68}} .btn.red{{background:#c92d36}} .btn.ghost{{background:#1e2638;color:var(--text)}}
input,select{{width:100%;background:#0d121d;border:1px solid var(--border);border-radius:10px;padding:10px;color:var(--text);margin-bottom:8px;font-size:14px}}
.tabs{{position:fixed;bottom:0;left:0;right:0;background:#101521;border-top:1px solid var(--border);display:flex;justify-content:space-around;padding:8px 4px;z-index:30}}
.tab{{background:none;border:none;color:var(--muted);font-size:11px;font-weight:700;cursor:pointer;padding:6px 8px;border-radius:10px;display:flex;flex-direction:column;align-items:center;gap:2px}}
.tab.active{{color:var(--gold);background:rgba(224,184,76,.12)}}
.pane{{display:none}} .pane.active{{display:block}}
.toast{{position:fixed;top:64px;left:50%;transform:translateX(-50%);background:#16c784;color:#08120d;padding:10px 16px;border-radius:12px;font-weight:800;font-size:13px;display:none;z-index:99}}
</style>
</head>
<body>
<div class="topbar">
  <div class="brand">👑 {config.BRAND}</div>
  <div class="pill" id="planPill">v{config.VERSION}</div>
</div>
<div class="toast" id="toast"></div>

<div class="wrap">
  <div id="pane-signals" class="pane active">
    <div class="grid2">
      <div class="kpi"><div class="lbl">الباقة الحالية</div><div class="val gold" id="kPlan">—</div></div>
      <div class="kpi"><div class="lbl">الحصة اليومية</div><div class="val green" id="kQuota">—</div></div>
    </div>
    <div class="card">
      <h3><span>📊 الشارت التفاعلي المباشر</span>
        <select id="symPick" style="width:auto;margin:0;padding:4px 8px;font-size:12px">
          <option value="XAUUSD">XAUUSD</option><option value="BTCUSD">BTCUSD</option>
          <option value="EURUSD">EURUSD</option><option value="ETHUSD">ETHUSD</option>
        </select>
      </h3>
      <div id="tvChart"></div>
    </div>
    <div class="card">
      <h3>📡 الإشارات الحية والمغلقة</h3>
      <div id="signalsList">جارٍ التحميل...</div>
    </div>
  </div>

  <div id="pane-risk" class="pane">
    <div class="card">
      <h3>🛡 ملف المخاطرة وحارس السحب اليومي</h3>
      <label style="font-size:12px;color:var(--muted)">رصيد الحساب ($)</label>
      <input id="rBal" type="number" value="1000"/>
      <label style="font-size:12px;color:var(--muted)">نسبة المخاطرة لكل صفقة (%)</label>
      <input id="rRisk" type="number" step="0.1" value="1"/>
      <label style="font-size:12px;color:var(--muted)">الرافعة المالية</label>
      <input id="rLev" type="number" value="100"/>
      <label style="font-size:12px;color:var(--muted)">حد السحب اليومي الأقصى (%)</label>
      <input id="rDd" type="number" step="0.5" value="3"/>
      <button class="btn gold" onclick="saveRisk()">💾 حفظ ملف المخاطرة</button>
    </div>
    <div class="card">
      <h3>📓 ملخص مفكرة التداول (30 يوماً)</h3>
      <div id="journalBox">—</div>
    </div>
  </div>

  <div id="pane-intel" class="pane">
    <div class="card"><h3>🏆 مصفوفة الاستراتيجيات (30 يوماً)</h3><div id="lbBox">—</div></div>
    <div class="card"><h3>📰 التقويم الاقتصادي ودرع الأخبار</h3><div id="newsBox">—</div></div>
  </div>

  <div id="pane-more" class="pane">
    <div class="card">
      <h3>🎓 الأكاديمية · 🎁 كود هدية · 🤝 الشركاء</h3>
      <div id="growthBox" style="margin-bottom:12px">—</div>
      <input id="giftCodeInput" placeholder="أدخل كود الهدية (مثال: FY-ABCD-1234)"/>
      <button class="btn green" onclick="redeemGift()">🎁 تفعيل كود الهدية الآن</button>
    </div>
  </div>
</div>

<div class="tabs">
  <button class="tab active" onclick="openTab('signals',this)">📡<span>الإشارات</span></button>
  <button class="tab" onclick="openTab('risk',this)">🛡<span>المخاطرة</span></button>
  <button class="tab" onclick="openTab('intel',this)">🏆<span>الاستراتيجيات</span></button>
  <button class="tab" onclick="openTab('more',this)">👑<span>المزيد</span></button>
</div>

<script>
const tg = window.Telegram && window.Telegram.WebApp;
if (tg) {{ tg.ready(); tg.expand(); }}
const INIT_DATA = (tg && tg.initData) || '';

function toast(msg, err=false){{
  const el=document.getElementById('toast');
  el.textContent=msg; el.style.background=err?'#ea3943':'#16c784'; el.style.color=err?'#fff':'#08120d';
  el.style.display='block'; setTimeout(()=>el.style.display='none', 2800);
}}

async function api(path, opts={{}}){{
  const headers = Object.assign({{'X-Telegram-Init-Data': INIT_DATA, 'Content-Type':'application/json'}}, opts.headers||{{}});
  const r = await fetch(path, Object.assign({{}}, opts, {{headers}}));
  return r.json();
}}

function openTab(name, btn){{
  document.querySelectorAll('.pane').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.getElementById('pane-'+name).classList.add('active');
  if(btn) btn.classList.add('active');
}}

let chart, series;
function initChart(){{
  const el=document.getElementById('tvChart');
  if(!window.LightweightCharts || !el) return;
  chart = LightweightCharts.createChart(el, {{
    layout:{{background:{{color:'#0d111a'}},textColor:'#94a3b8'}},
    grid:{{vertLines:{{color:'#1c2436'}},horzLines:{{color:'#1c2436'}}}},
    timeScale:{{timeVisible:true,secondsVisible:false}}
  }});
  series = chart.addCandlestickSeries({{upColor:'#16c784',downColor:'#ea3943',borderVisible:false,wickUpColor:'#16c784',wickDownColor:'#ea3943'}});
}}

async function loadCandles(sym){{
  if(!series) return;
  const d = await api('/api/candles?symbol='+encodeURIComponent(sym)+'&tf=H1');
  if(d && d.candles && d.candles.length){{ series.setData(d.candles); chart.timeScale().fitContent(); }}
}}

async function logJournal(sid, action){{
  const r = await api('/api/journal', {{method:'POST', body:JSON.stringify({{signal_id:sid, action}})}});
  toast(r.ok ? '✅ تم تسجيل قرارك في مفكرة التداول' : (r.error||'خطأ'), !r.ok);
}}

async function saveRisk(){{
  const r = await api('/api/risk', {{method:'POST', body:JSON.stringify({{
    balance:+document.getElementById('rBal').value,
    risk_pct:+document.getElementById('rRisk').value,
    leverage:+document.getElementById('rLev').value,
    max_daily_dd_pct:+document.getElementById('rDd').value,
  }})}});
  toast(r.ok ? '✅ تم حفظ ملف المخاطرة الملكي' : (r.error||'خطأ'), !r.ok);
}}

async function redeemGift(){{
  const code = document.getElementById('giftCodeInput').value.trim();
  if(!code) return toast('أدخل الكود أولاً', true);
  const r = await api('/api/redeem', {{method:'POST', body:JSON.stringify({{code}})}});
  toast(r.ok ? '🎁 تم تفعيل الهدية بنجاح!' : (r.error||'كود غير صالح'), !r.ok);
  if(r.ok) boot();
}}

async function boot(){{
  initChart();
  document.getElementById('symPick').onchange = e => loadCandles(e.target.value);
  loadCandles('XAUUSD');
  const me = await api('/api/me');
  if(me && me.ok){{
    document.getElementById('kPlan').textContent = me.plan.code;
    document.getElementById('planPill').textContent = me.plan.code;
    document.getElementById('kQuota').textContent = me.quota.used + ' / ' + me.quota.limit;
    if(me.risk){{
      document.getElementById('rBal').value = me.risk.balance;
      document.getElementById('rRisk').value = me.risk.risk_pct;
      document.getElementById('rLev').value = me.risk.leverage;
      document.getElementById('rDd').value = me.risk.max_daily_dd_pct;
    }}
    const j = me.journal || {{}};
    document.getElementById('journalBox').innerHTML =
      `صفقات دخلتها: <b>${{j.taken_count||0}}</b> · نسبة النجاح: <b>${{j.win_rate||0}}%</b> · الصافي: <b>${{j.net_pips||0}} pips</b> (${{j.net_usd||0}}$)`;
    const ac = me.academy || {{}}; const af = me.affiliate || {{}};
    document.getElementById('growthBox').innerHTML =
      `🎓 الأكاديمية: <b>${{ac.overall_pct||0}}%</b> (${{ac.total_done||0}}/${{ac.total_all||15}})<br>` +
      `🤝 رتبة الشركاء: <b>${{(af.tier&&af.tier.label)||'برونزي'}}</b> (${{(af.tier&&af.tier.rate_pct)||15}}%) · المتاح للسحب: <b>${{af.available_usd||0}}$</b>`;
  }}
  const sigs = await api('/api/signals');
  const box = document.getElementById('signalsList');
  if(sigs && sigs.signals && sigs.signals.length){{
    box.innerHTML = sigs.signals.map(s => `
      <div class="sig-row">
        <div class="sig-head">
          <b>${{s.symbol}} · ${{s.timeframe}} · ${{s.strategy}}</b>
          <span class="badge ${{s.direction==='BUY'?'b-buy':'b-sell'}}">${{s.direction}} · ${{s.score}}/100</span>
        </div>
        <div class="lvls">
          <div class="lvl"><span>دخول</span><b>${{s.entry}}</b></div>
          <div class="lvl"><span>SL</span><b style="color:var(--red)">${{s.stop}}</b></div>
          <div class="lvl"><span>TP1</span><b style="color:var(--green)">${{s.tp1}}</b></div>
          <div class="lvl"><span>TP3</span><b style="color:var(--gold)">${{s.tp3}}</b></div>
        </div>
        <div class="btn-row">
          <button class="btn green" onclick="logJournal(${{s.id}},'TAKEN')">✅ دخلت</button>
          <button class="btn red" onclick="logJournal(${{s.id}},'SKIPPED')">❌ تجاهلت</button>
          <button class="btn ghost" onclick="document.getElementById('symPick').value='${{s.symbol}}';loadCandles('${{s.symbol}}')">📊 شارت</button>
        </div>
      </div>`).join('');
  }} else {{
    box.innerHTML = '<div style="color:var(--muted)">لا توجد إشارات متاحة حالياً.</div>';
  }}
  const lb = await api('/api/leaderboard?period=30d');
  if(lb && lb.rows){{
    document.getElementById('lbBox').innerHTML = lb.rows.slice(0,8).map(r=>
      `<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--border)">
        <b>${{r.strategy}}</b><span>${{r.win_rate}}% · <b>${{r.net_pips>0?'+':''}}${{r.net_pips}}p</b> (${{r.trades}})</span>
      </div>`).join('') || 'لا توجد بيانات كافية بعد.';
  }}
  const nw = await api('/api/news');
  if(nw && nw.events){{
    document.getElementById('newsBox').innerHTML = nw.events.slice(0,6).map(e=>
      `<div style="padding:6px 0;border-bottom:1px solid var(--border)">🔴 <b>${{e.currency}}</b> — ${{e.title}} <span style="color:var(--muted)">(${{e.time_utc||''}})</span></div>`
    ).join('') || '🟢 لا توجد أخبار حمراء قريبة.';
  }}
}}
boot();
</script>
</body>
</html>"""


def render_admin_panel_html() -> str:
    return f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no"/>
<title>{config.BRAND} — Super Admin Web Panel v{config.VERSION}</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
:root{{--bg:#090c12;--card:#141a28;--border:#24304a;--gold:#e0b84c;--green:#16c784;--red:#ea3943;--blue:#3b82f6;--text:#eef2ff;--muted:#94a3b8}}
*{{box-sizing:border-box;margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Tahoma,sans-serif}}
body{{background:var(--bg);color:var(--text);padding:14px;max-width:860px;margin:0 auto}}
.hdr{{display:flex;justify-content:space-between;align-items:center;padding-bottom:12px;border-bottom:1px solid rgba(224,184,76,.35);margin-bottom:14px}}
.hdr h1{{font-size:17px;color:var(--gold)}}
.grid4{{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-bottom:14px}}
@media(min-width:640px){{.grid4{{grid-template-columns:repeat(4,1fr)}}}}
.kpi{{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:12px}}
.kpi .lbl{{color:var(--muted);font-size:12px}} .kpi .val{{font-size:20px;font-weight:800;margin-top:4px;color:var(--gold)}}
.card{{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:14px;margin-bottom:12px}}
.card h3{{font-size:15px;color:var(--gold);margin-bottom:10px}}
.toggles{{display:grid;grid-template-columns:repeat(2,1fr);gap:8px}}
.btn{{border:none;border-radius:10px;padding:10px 12px;font-weight:700;font-size:12px;cursor:pointer;color:#fff;background:var(--blue)}}
.btn.on{{background:#129e68}} .btn.off{{background:#334155}} .btn.danger{{background:#c92d36}} .btn.gold{{background:linear-gradient(135deg,#c99a2e,#e5c158);color:#090c12}}
.item{{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:9px 0;border-bottom:1px solid var(--border);font-size:13px}}
input,select{{background:#0c111b;border:1px solid var(--border);color:var(--text);padding:9px;border-radius:10px;font-size:13px}}
.toast{{position:fixed;top:14px;left:50%;transform:translateX(-50%);background:#16c784;color:#08120d;padding:10px 16px;border-radius:10px;font-weight:800;display:none;z-index:99}}
</style>
</head>
<body>
<div class="toast" id="toast"></div>
<div class="hdr">
  <h1>👑 لوحة الإدارة الملكية — {config.BRAND}</h1>
  <button class="btn gold" onclick="loadAdmin()">🔄 تحديث</button>
</div>
<div id="authErr" style="display:none;background:#451218;border:1px solid #ea3943;padding:14px;border-radius:12px;margin-bottom:12px">
  ⛔ وصول مرفوض — هذه اللوحة مخصصة لـ <b>ADMIN_ID</b> فقط عبر توقيع Telegram WebApp HMAC.
</div>
<div id="adminBody">
  <div class="grid4">
    <div class="kpi"><div class="lbl">المستخدمون</div><div class="val" id="kUsers">—</div></div>
    <div class="kpi"><div class="lbl">المشتركون المدفوعون</div><div class="val" id="kSubs">—</div></div>
    <div class="kpi"><div class="lbl">إشارات مفتوحة</div><div class="val" id="kOpen">—</div></div>
    <div class="kpi"><div class="lbl">طلبات دفع معلقة</div><div class="val" id="kPay">—</div></div>
  </div>
  <div class="card">
    <h3>⚡ مفاتيح التحكم السريع (1-Tap)</h3>
    <div class="toggles" id="togglesBox"></div>
  </div>
  <div class="card">
    <h3>🎁 إصدار كود هدية فوري (أي باقة · أي مدة)</h3>
    <div style="display:flex;gap:8px;flex-wrap:wrap">
      <select id="gPlan"><option value="PRO">PRO</option><option value="VIP" selected>VIP</option><option value="ROYAL">ROYAL</option><option value="ACADEMY">ACADEMY</option></select>
      <input id="gDays" type="number" value="30" style="width:90px" placeholder="أيام"/>
      <input id="gUses" type="number" value="1" style="width:80px" placeholder="عدد"/>
      <button class="btn gold" onclick="genGift()">🎟 إصدار كود</button>
    </div>
    <div id="giftOut" style="margin-top:8px;font-weight:700;color:var(--green)"></div>
  </div>
  <div class="card"><h3>💳 طلبات الدفع المعلقة</h3><div id="payBox">—</div></div>
  <div class="card"><h3>💸 طلبات سحب الشركاء المعلقة</h3><div id="payoutBox">—</div></div>
</div>
<script>
const tg = window.Telegram && window.Telegram.WebApp;
if (tg) {{ tg.ready(); tg.expand(); }}
const INIT_DATA = (tg && tg.initData) || '';
function toast(m,e=false){{const el=document.getElementById('toast');el.textContent=m;el.style.background=e?'#ea3943':'#16c784';el.style.color=e?'#fff':'#08120d';el.style.display='block';setTimeout(()=>el.style.display='none',2800);}}
async function api(p,o={{}}){{const h=Object.assign({{'X-Telegram-Init-Data':INIT_DATA,'Content-Type':'application/json'}},o.headers||{{}});const r=await fetch(p,Object.assign({{}},o,{{headers:h}}));return r.json();}}

async function toggleKey(key){{
  const r = await api('/api/admin/toggle',{{method:'POST',body:JSON.stringify({{key}})}});
  toast(r.ok ? '✅ تم تحديث الإعداد' : (r.error||'خطأ'), !r.ok);
  loadAdmin();
}}
async function actPay(id, decision){{
  const r = await api('/api/admin/payment',{{method:'POST',body:JSON.stringify({{payment_id:id, decision}})}});
  toast(r.ok ? '✅ تم تنفيذ القرار على الدفعة' : (r.error||'خطأ'), !r.ok);
  loadAdmin();
}}
async function actPayout(id, decision){{
  const r = await api('/api/admin/payout',{{method:'POST',body:JSON.stringify({{payout_id:id, decision}})}});
  toast(r.ok ? '✅ تم تحديث طلب السحب' : (r.error||'خطأ'), !r.ok);
  loadAdmin();
}}
async function genGift(){{
  const r = await api('/api/admin/gift',{{method:'POST',body:JSON.stringify({{
    target_plan:document.getElementById('gPlan').value,
    duration_days:+document.getElementById('gDays').value,
    max_uses:+document.getElementById('gUses').value
  }})}});
  if(r.ok){{ document.getElementById('giftOut').textContent = '🎟 الكود الجديد: ' + r.code; toast('🎁 تم إصدار الكود'); }}
  else toast(r.error||'خطأ', true);
}}

async function loadAdmin(){{
  const d = await api('/api/admin/overview');
  if(!d || !d.ok){{
    document.getElementById('authErr').style.display='block';
    document.getElementById('adminBody').style.display='none';
    return;
  }}
  document.getElementById('kUsers').textContent = d.kpis.users;
  document.getElementById('kSubs').textContent = d.kpis.active_paid_subs;
  document.getElementById('kOpen').textContent = d.kpis.open_signals;
  document.getElementById('kPay').textContent = d.pending_payments.length;

  const labels = {{
    engine_enabled:'🧠 المحرك الآلي', global_kill_switch:'🛑 مفتاح الطوارئ',
    news_guard_enabled:'📰 درع الأخبار', charts_enabled:'📊 الشارتات المحلية',
    academy_enabled:'🎓 الأكاديمية', crypto_api_enabled:'🔗 منصات الكريبتو'
  }};
  document.getElementById('togglesBox').innerHTML = Object.entries(labels).map(([k,lbl])=>{{
    const on = !!d.toggles[k];
    const cls = k==='global_kill_switch' ? (on?'danger':'off') : (on?'on':'off');
    return `<button class="btn ${{cls}}" onclick="toggleKey('${{k}}')">${{lbl}}: ${{on?'🟢 مفعّل':'⚪ متوقف'}}</button>`;
  }}).join('');

  document.getElementById('payBox').innerHTML = d.pending_payments.length ? d.pending_payments.map(p=>
    `<div class="item"><span>#${{p.id}} · UID ${{p.user_id}} · <b>${{p.plan_code}}</b> · ${{p.amount}}$</span>
     <span><button class="btn on" onclick="actPay(${{p.id}},'approve')">✅</button>
     <button class="btn danger" onclick="actPay(${{p.id}},'reject')">❌</button></span></div>`
  ).join('') : '<div style="color:var(--muted)">لا توجد طلبات دفع معلقة.</div>';

  document.getElementById('payoutBox').innerHTML = d.pending_payouts.length ? d.pending_payouts.map(p=>
    `<div class="item"><span>#${{p.id}} · UID ${{p.user_id}} · <b>${{p.amount_usd}}$</b> · ${{p.wallet_address}}</span>
     <span><button class="btn on" onclick="actPayout(${{p.id}},'approve')">✅</button>
     <button class="btn danger" onclick="actPayout(${{p.id}},'reject')">❌</button></span></div>`
  ).join('') : '<div style="color:var(--muted)">لا توجد طلبات سحب معلقة.</div>';
}}
loadAdmin();
</script>
</body>
</html>"""
