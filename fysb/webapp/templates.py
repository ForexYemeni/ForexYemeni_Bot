"""Ultra-Luxury Royal Obsidian-Gold Glassmorphic RTL Arabic HTML Templates (v7.3.0):
  1. User Telegram Mini App (`/`) — Royal VIP Terminal with TradingView Lightweight
     Charts + Signal Overlay Price Lines + Live Market & Timeframe Pills + Live
     Risk/Lot Preview + Journal Analytics + Strategy Leaderboard + News Shield +
     Academy + Gift Codes + Affiliates + Floating Glass Dock
  2. Super Admin Executive Web Panel (`/admin`) — Live KPIs, 1-Tap System Controls,
     Market-Filtered Chart-Only Channels Studio, Pending Payments, Affiliate Payouts,
     & Instant Gift Code Generator
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
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
<link href="https://fonts.googleapis.com/css2?family=Cairo:wght@400;600;700;800;900&family=JetBrains+Mono:wght@500;700;800&display=swap" rel="stylesheet"/>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<script src="https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"></script>
<style>
:root {{
  --bg:#06080f;
  --bg2:#0a0e18;
  --glass:rgba(16,22,36,.78);
  --glass2:rgba(21,28,46,.88);
  --border:rgba(148,163,184,.14);
  --gold:#d9b04c;
  --gold2:#f7de8c;
  --gold-dim:rgba(217,176,76,.16);
  --gold-line:rgba(217,176,76,.34);
  --green:#16c784;
  --green-dim:rgba(22,199,132,.15);
  --red:#f04452;
  --red-dim:rgba(240,68,82,.15);
  --cyan:#38bdf8;
  --text:#f1f5f9;
  --muted:#94a3b8;
  --mono:'JetBrains Mono',ui-monospace,SFMono-Regular,Menlo,monospace;
}}
*{{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}}
body{{
  font-family:'Cairo',-apple-system,BlinkMacSystemFont,'Segoe UI',Tahoma,sans-serif;
  background:
    radial-gradient(ellipse 80% 45% at 50% -8%, rgba(217,176,76,.15), transparent 65%),
    radial-gradient(ellipse 60% 40% at 95% 20%, rgba(22,199,132,.08), transparent 60%),
    radial-gradient(ellipse 60% 40% at 5% 85%, rgba(56,189,248,.06), transparent 60%),
    var(--bg);
  color:var(--text);
  padding-bottom:92px;
  min-height:100vh;
  overflow-x:hidden;
}}
.topbar{{
  position:sticky;top:0;z-index:40;
  background:rgba(7,10,18,.82);
  backdrop-filter:blur(18px);-webkit-backdrop-filter:blur(18px);
  border-bottom:1px solid var(--gold-line);
  padding:12px 16px;
  display:flex;justify-content:space-between;align-items:center;
}}
.brand-box{{display:flex;align-items:center;gap:10px}}
.crown-orb{{
  width:36px;height:36px;border-radius:12px;
  background:linear-gradient(135deg,#b88920,#f7de8c,#c99628);
  display:flex;align-items:center;justify-content:center;
  font-size:18px;box-shadow:0 4px 18px rgba(217,176,76,.35);
}}
.brand-name{{font-weight:900;font-size:15px;letter-spacing:.4px;background:linear-gradient(90deg,#f7de8c,#d9b04c);-webkit-background-clip:text;-webkit-text-fill-color:transparent}}
.brand-sub{{font-size:10px;color:var(--muted);display:flex;align-items:center;gap:5px}}
.live-dot{{width:7px;height:7px;border-radius:50%;background:var(--green);box-shadow:0 0 8px var(--green);display:inline-block;animation:pulse 1.8s infinite}}
@keyframes pulse{{0%,100%{{opacity:1;transform:scale(1)}}50%{{opacity:.45;transform:scale(.82)}}}}
.top-right{{display:flex;align-items:center;gap:8px}}
.plan-pill{{
  background:linear-gradient(135deg,rgba(217,176,76,.22),rgba(217,176,76,.07));
  border:1px solid var(--gold-line);color:var(--gold2);
  padding:4px 12px;border-radius:999px;font-size:11px;font-weight:800;letter-spacing:.4px;
}}
.icon-btn{{
  width:34px;height:34px;border-radius:10px;border:1px solid var(--border);
  background:var(--glass);color:var(--text);cursor:pointer;font-size:14px;
  display:flex;align-items:center;justify-content:center;transition:.18s;
}}
.icon-btn:active{{transform:scale(.92)}}

.wrap{{padding:14px;max-width:780px;margin:0 auto}}

/* VIP Hero Membership Card */
.hero{{
  position:relative;overflow:hidden;
  background:linear-gradient(135deg,rgba(24,29,48,.92),rgba(28,23,12,.92));
  border:1px solid var(--gold-line);
  border-radius:20px;padding:16px;margin-bottom:14px;
  box-shadow:0 14px 40px rgba(0,0,0,.45), inset 0 1px 0 rgba(247,222,140,.18);
}}
.hero::after{{
  content:'';position:absolute;top:-40px;left:-40px;width:150px;height:150px;
  background:radial-gradient(circle,rgba(217,176,76,.22),transparent 70%);pointer-events:none;
}}
.hero-top{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:12px}}
.hero-title{{font-size:12px;color:var(--muted);font-weight:700}}
.hero-plan{{font-size:22px;font-weight:900;background:linear-gradient(90deg,#fff3c4,#d9b04c);-webkit-background-clip:text;-webkit-text-fill-color:transparent}}
.hero-metrics{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}}
.hm{{background:rgba(6,8,15,.55);border:1px solid rgba(255,255,255,.06);border-radius:12px;padding:9px 10px}}
.hm .l{{font-size:10px;color:var(--muted);margin-bottom:2px}}
.hm .v{{font-family:var(--mono);font-size:14px;font-weight:800}}
.v.gold{{color:var(--gold2)}} .v.green{{color:var(--green)}} .v.red{{color:var(--red)}} .v.cyan{{color:var(--cyan)}}
.qbar{{height:5px;border-radius:999px;background:rgba(255,255,255,.08);margin-top:10px;overflow:hidden}}
.qbar-fill{{height:100%;border-radius:999px;background:linear-gradient(90deg,#c99628,#f7de8c,#16c784);width:12%;transition:width .5s ease}}

/* Glass Cards */
.card{{
  background:var(--glass);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);
  border:1px solid var(--border);border-radius:20px;padding:15px;margin-bottom:14px;
  box-shadow:0 10px 30px rgba(0,0,0,.32);
}}
.card.gold-edge{{border-color:var(--gold-line)}}
.card-hdr{{display:flex;justify-content:space-between;align-items:center;margin-bottom:11px;gap:8px;flex-wrap:wrap}}
.card-hdr h3{{font-size:15px;font-weight:800;color:var(--gold2);display:flex;align-items:center;gap:6px}}
.chip-row{{display:flex;gap:6px;overflow-x:auto;padding-bottom:6px;scrollbar-width:none}}
.chip-row::-webkit-scrollbar{{display:none}}
.chip{{
  flex:0 0 auto;border:1px solid var(--border);background:rgba(10,14,24,.75);
  color:var(--muted);padding:5px 12px;border-radius:999px;font-size:12px;font-weight:700;
  cursor:pointer;transition:.16s;font-family:var(--mono);
}}
.chip.active{{
  background:linear-gradient(135deg,rgba(217,176,76,.26),rgba(217,176,76,.08));
  border-color:var(--gold);color:var(--gold2);box-shadow:0 0 12px rgba(217,176,76,.18);
}}
.tf-bar{{display:flex;gap:5px;align-items:center}}
.tf-btn{{
  border:1px solid var(--border);background:rgba(10,14,24,.8);color:var(--muted);
  padding:4px 9px;border-radius:8px;font-size:11px;font-weight:800;font-family:var(--mono);cursor:pointer;
}}
.tf-btn.active{{background:var(--gold);color:#06080f;border-color:var(--gold)}}
.chart-meta{{display:flex;justify-content:space-between;align-items:center;margin:6px 0 8px;font-family:var(--mono);font-size:12px}}
.price-now{{font-size:18px;font-weight:800;color:var(--text)}}
.chg-pill{{padding:3px 9px;border-radius:8px;font-size:11px;font-weight:800}}
.chg-up{{background:var(--green-dim);color:var(--green)}}
.chg-dn{{background:var(--red-dim);color:var(--red)}}
#tvChart{{width:100%;height:285px;border-radius:14px;overflow:hidden;border:1px solid rgba(217,176,76,.2);background:#080c14}}
.overlay-note{{font-size:11px;color:var(--gold2);margin-top:7px;display:none;background:rgba(217,176,76,.08);border:1px solid var(--gold-line);padding:6px 10px;border-radius:10px}}

/* Luxury Signal Cards */
.sig-card{{
  position:relative;overflow:hidden;
  background:linear-gradient(160deg,rgba(19,25,40,.94),rgba(11,15,25,.94));
  border:1px solid var(--border);border-radius:16px;padding:13px;margin-bottom:11px;
  transition:transform .16s,border-color .16s;
}}
.sig-card.buy-glow{{border-right:3px solid var(--green)}}
.sig-card.sell-glow{{border-right:3px solid var(--red)}}
.sig-top{{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:9px}}
.sig-sym{{font-weight:900;font-size:15px;display:flex;align-items:center;gap:7px}}
.sig-meta{{font-size:11px;color:var(--muted);font-weight:600}}
.badge{{padding:3px 10px;border-radius:999px;font-size:11px;font-weight:800;font-family:var(--mono);letter-spacing:.3px}}
.b-buy{{background:var(--green-dim);color:var(--green);border:1px solid rgba(22,199,132,.4)}}
.b-sell{{background:var(--red-dim);color:var(--red);border:1px solid rgba(240,68,82,.4)}}
.b-gold{{background:var(--gold-dim);color:var(--gold2);border:1px solid var(--gold-line)}}
.b-status{{background:rgba(56,189,248,.14);color:var(--cyan);border:1px solid rgba(56,189,248,.35)}}

.lvls{{display:grid;grid-template-columns:repeat(5,1fr);gap:5px;margin:9px 0}}
@media(max-width:460px){{.lvls{{grid-template-columns:repeat(3,1fr)}}}}
.lvl{{background:rgba(6,8,15,.72);border:1px solid rgba(255,255,255,.05);padding:7px 5px;border-radius:10px;text-align:center}}
.lvl span{{display:block;color:var(--muted);font-size:9.5px;margin-bottom:2px}}
.lvl b{{font-family:var(--mono);font-size:11.5px;font-weight:800}}

.sig-foot{{display:flex;justify-content:space-between;align-items:center;font-size:11px;color:var(--muted);margin-bottom:8px}}
.btn-row{{display:flex;gap:7px;flex-wrap:wrap}}
.btn{{
  flex:1;min-width:84px;border:none;border-radius:11px;padding:9px 10px;
  font-family:'Cairo',sans-serif;font-weight:800;font-size:12px;cursor:pointer;
  color:#fff;background:#2563eb;transition:.16s;
}}
.btn:active{{transform:scale(.96)}}
.btn.gold{{background:linear-gradient(135deg,#c29024,#f7de8c);color:#06080f;box-shadow:0 4px 16px rgba(217,176,76,.28)}}
.btn.green{{background:linear-gradient(135deg,#0f9d66,#16c784);color:#04120c}}
.btn.red{{background:linear-gradient(135deg,#c92a38,#f04452)}}
.btn.ghost{{background:rgba(30,41,59,.8);border:1px solid var(--border);color:var(--text)}}

/* Inputs & Risk Preview */
.field{{margin-bottom:10px}}
.field label{{display:block;font-size:12px;color:var(--muted);margin-bottom:4px;font-weight:700}}
input,select{{
  width:100%;background:rgba(6,8,15,.85);border:1px solid var(--border);border-radius:12px;
  padding:11px 12px;color:var(--text);font-size:14px;font-family:var(--mono);outline:none;transition:.16s;
}}
input:focus,select:focus{{border-color:var(--gold);box-shadow:0 0 0 3px rgba(217,176,76,.12)}}
.risk-preview{{
  display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:10px 0 12px;
  background:rgba(217,176,76,.06);border:1px solid var(--gold-line);border-radius:14px;padding:10px;
}}
.rp-box .l{{font-size:11px;color:var(--muted)}}
.rp-box .v{{font-family:var(--mono);font-size:16px;font-weight:900;color:var(--gold2)}}

.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:9px}}
.stat-tile{{background:rgba(6,8,15,.65);border:1px solid var(--border);border-radius:14px;padding:11px;text-align:center}}
.stat-tile .l{{font-size:11px;color:var(--muted)}}
.stat-tile .v{{font-family:var(--mono);font-size:17px;font-weight:900;margin-top:3px}}

/* Leaderboard & News */
.lb-row{{padding:10px 0;border-bottom:1px solid var(--border)}}
.lb-row:last-child{{border-bottom:none}}
.lb-top{{display:flex;justify-content:space-between;align-items:center;margin-bottom:5px;font-size:13px}}
.lb-bar{{height:5px;border-radius:999px;background:rgba(255,255,255,.07);overflow:hidden}}
.lb-fill{{height:100%;border-radius:999px;background:linear-gradient(90deg,#16c784,#f7de8c)}}
.news-item{{display:flex;justify-content:space-between;align-items:center;gap:10px;padding:10px 0;border-bottom:1px solid var(--border);font-size:13px}}
.news-item:last-child{{border-bottom:none}}

/* Floating Frosted Bottom Navigation Dock */
.dock-wrap{{position:fixed;bottom:10px;left:12px;right:12px;z-index:50;display:flex;justify-content:center;pointer-events:none}}
.dock{{
  pointer-events:auto;width:100%;max-width:520px;
  background:rgba(12,17,29,.88);backdrop-filter:blur(22px);-webkit-backdrop-filter:blur(22px);
  border:1px solid var(--gold-line);border-radius:20px;
  display:grid;grid-template-columns:repeat(4,1fr);padding:6px;gap:4px;
  box-shadow:0 12px 38px rgba(0,0,0,.6);
}}
.tab{{
  background:none;border:none;color:var(--muted);font-family:'Cairo',sans-serif;
  font-size:11px;font-weight:800;cursor:pointer;padding:7px 4px;border-radius:14px;
  display:flex;flex-direction:column;align-items:center;gap:2px;transition:.18s;
}}
.tab .ic{{font-size:17px;line-height:1}}
.tab.active{{
  color:var(--gold2);
  background:linear-gradient(135deg,rgba(217,176,76,.22),rgba(217,176,76,.06));
  box-shadow:inset 0 0 0 1px rgba(217,176,76,.35);
}}
.pane{{display:none;animation:fadeUp .22s ease}}
.pane.active{{display:block}}
@keyframes fadeUp{{from{{opacity:0;transform:translateY(6px)}}to{{opacity:1;transform:none}}}}
.toast{{
  position:fixed;top:68px;left:50%;transform:translateX(-50%);
  background:linear-gradient(135deg,#16c784,#10b981);color:#04120c;
  padding:10px 18px;border-radius:14px;font-weight:900;font-size:13px;
  display:none;z-index:99;box-shadow:0 10px 30px rgba(0,0,0,.5);white-space:nowrap;
}}
.empty{{color:var(--muted);text-align:center;padding:18px 8px;font-size:13px}}
</style>
</head>
<body>
<div class="topbar">
  <div class="brand-box">
    <div class="crown-orb">👑</div>
    <div>
      <div class="brand-name">{config.BRAND} ROYAL</div>
      <div class="brand-sub"><span class="live-dot"></span> متصل بالسوق اللحظي · v{config.VERSION}</div>
    </div>
  </div>
  <div class="top-right">
    <div class="plan-pill" id="planPill">ROYAL</div>
    <button class="icon-btn" onclick="boot()" title="تحديث">🔄</button>
  </div>
</div>
<div class="toast" id="toast"></div>

<div class="wrap">
  <!-- PANE 1: SIGNALS & LIVE CHART -->
  <div id="pane-signals" class="pane active">
    <div class="hero">
      <div class="hero-top">
        <div>
          <div class="hero-title">العضوية الملكية النشطة</div>
          <div class="hero-plan" id="kPlan">—</div>
        </div>
        <span class="badge b-gold" id="kWinPill">🏆 0%</span>
      </div>
      <div class="hero-metrics">
        <div class="hm"><div class="l">الحصة اليومية</div><div class="v green" id="kQuota">—</div></div>
        <div class="hm"><div class="l">صافي النقاط (30ي)</div><div class="v gold" id="kPips">—</div></div>
        <div class="hm"><div class="l">الأكاديمية</div><div class="v cyan" id="kAcad">—</div></div>
      </div>
      <div class="qbar"><div class="qbar-fill" id="qBarFill"></div></div>
    </div>

    <div class="card gold-edge">
      <div class="card-hdr">
        <h3>📊 الشارت المؤسسي المباشر</h3>
        <div class="tf-bar" id="tfBar">
          <button class="tf-btn" onclick="pickTf('M5',this)">M5</button>
          <button class="tf-btn" onclick="pickTf('M15',this)">M15</button>
          <button class="tf-btn active" onclick="pickTf('H1',this)">H1</button>
          <button class="tf-btn" onclick="pickTf('H4',this)">H4</button>
        </div>
      </div>
      <div class="chip-row" id="mktChips"></div>
      <div class="chart-meta">
        <div><span id="chartSymLbl" style="color:var(--gold2);font-weight:800">XAUUSD</span> · <span class="price-now" id="chartPriceNow">—</span></div>
        <span class="chg-pill chg-up" id="chartChg">—</span>
      </div>
      <div id="tvChart"></div>
      <div class="overlay-note" id="overlayNote"></div>
    </div>

    <div class="card">
      <div class="card-hdr">
        <h3>📡 الإشارات الملكية الحية</h3>
        <div class="tf-bar">
          <button class="tf-btn active" onclick="filterSigs('all',this)">الكل</button>
          <button class="tf-btn" onclick="filterSigs('open',this)">مفتوحة</button>
          <button class="tf-btn" onclick="filterSigs('win',this)">رابحة</button>
        </div>
      </div>
      <div id="signalsList"><div class="empty">⏳ جارٍ تحميل الإشارات الحية...</div></div>
    </div>
  </div>

  <!-- PANE 2: RISK & JOURNAL -->
  <div id="pane-risk" class="pane">
    <div class="card gold-edge">
      <div class="card-hdr"><h3>🛡 استوديو إدارة المخاطر وحارس السحب</h3></div>
      <div class="grid2">
        <div class="field"><label>رصيد الحساب ($)</label><input id="rBal" type="number" value="1000" oninput="previewRisk()"/></div>
        <div class="field"><label>المخاطرة لكل صفقة (%)</label><input id="rRisk" type="number" step="0.1" value="1" oninput="previewRisk()"/></div>
        <div class="field"><label>الرافعة المالية</label><input id="rLev" type="number" value="100"/></div>
        <div class="field"><label>حد السحب اليومي (%)</label><input id="rDd" type="number" step="0.5" value="3" oninput="previewRisk()"/></div>
      </div>
      <div class="risk-preview">
        <div class="rp-box"><div class="l">قيمة المخاطرة للصفقة</div><div class="v" id="rpTrade">$10.00</div></div>
        <div class="rp-box"><div class="l">سقف السحب اليومي</div><div class="v" id="rpDaily">$30.00</div></div>
      </div>
      <button class="btn gold" style="width:100%" onclick="saveRisk()">💾 حفظ ملف المخاطرة الملكي</button>
    </div>
    <div class="card">
      <div class="card-hdr"><h3>📓 أداء مفكرة التداول (30 يوماً)</h3></div>
      <div class="grid2" id="journalGrid">
        <div class="stat-tile"><div class="l">صفقات منفّذة</div><div class="v gold" id="jTaken">0</div></div>
        <div class="stat-tile"><div class="l">نسبة النجاح</div><div class="v green" id="jWin">0%</div></div>
        <div class="stat-tile"><div class="l">صافي النقاط</div><div class="v cyan" id="jPips">0</div></div>
        <div class="stat-tile"><div class="l">صافي الربح ($)</div><div class="v green" id="jUsd">$0</div></div>
      </div>
    </div>
  </div>

  <!-- PANE 3: INTELLIGENCE & NEWS -->
  <div id="pane-intel" class="pane">
    <div class="card gold-edge">
      <div class="card-hdr"><h3>🏆 مصفوفة الاستراتيجيات (30 يوماً)</h3></div>
      <div id="lbBox"><div class="empty">⏳ جارٍ التحميل...</div></div>
    </div>
    <div class="card">
      <div class="card-hdr"><h3>📰 التقويم الاقتصادي ودرع الأخبار</h3></div>
      <div id="newsBox"><div class="empty">⏳ جارٍ التحميل...</div></div>
    </div>
  </div>

  <!-- PANE 4: ACADEMY, GIFTS & AFFILIATES -->
  <div id="pane-more" class="pane">
    <div class="card gold-edge">
      <div class="card-hdr"><h3>🎓 أكاديمية ForexYemeni الاحترافية</h3></div>
      <div id="acadBox" style="font-size:13px;margin-bottom:8px">—</div>
      <div class="qbar"><div class="qbar-fill" id="acadFill" style="width:0%"></div></div>
    </div>
    <div class="card">
      <div class="card-hdr"><h3>🤝 برنامج الشركاء والعمولات</h3></div>
      <div id="affBox" style="font-size:13px">—</div>
    </div>
    <div class="card">
      <div class="card-hdr"><h3>🎁 تفعيل كود هدية VIP</h3></div>
      <div class="field"><input id="giftCodeInput" placeholder="أدخل الكود (مثال: FY-ABCD-1234)"/></div>
      <button class="btn gold" style="width:100%" onclick="redeemGift()">🎁 تفعيل كود الهدية الآن</button>
    </div>
  </div>
</div>

<div class="dock-wrap">
  <div class="dock">
    <button class="tab active" onclick="openTab('signals',this)"><span class="ic">📡</span><span>الإشارات</span></button>
    <button class="tab" onclick="openTab('risk',this)"><span class="ic">🛡</span><span>المخاطرة</span></button>
    <button class="tab" onclick="openTab('intel',this)"><span class="ic">🏆</span><span>الذكاء</span></button>
    <button class="tab" onclick="openTab('more',this)"><span class="ic">👑</span><span>الملكي</span></button>
  </div>
</div>

<script>
const tg = window.Telegram && window.Telegram.WebApp;
if (tg) {{ tg.ready(); tg.expand(); try{{ tg.setHeaderColor('#06080f'); tg.setBackgroundColor('#06080f'); }}catch(_e){{}} }}
const INIT_DATA = (tg && tg.initData) || '';

let chart, series, priceLines = [];
let curSym = 'XAUUSD', curTf = 'H1', allSignals = [], sigFilter = 'all';

function toast(msg, err=false){{
  const el=document.getElementById('toast');
  el.textContent=msg;
  el.style.background=err?'linear-gradient(135deg,#c92a38,#f04452)':'linear-gradient(135deg,#16c784,#10b981)';
  el.style.color=err?'#fff':'#04120c';
  el.style.display='block';
  clearTimeout(window._tt);
  window._tt=setTimeout(()=>el.style.display='none', 2800);
}}

async function api(path, opts={{}}){{
  const headers = Object.assign({{
    'X-Telegram-Init-Data': INIT_DATA,
    'Content-Type':'application/json',
    'ngrok-skip-browser-warning':'1'
  }}, opts.headers||{{}});
  const r = await fetch(path, Object.assign({{}}, opts, {{headers}}));
  return r.json();
}}

function openTab(name, btn){{
  document.querySelectorAll('.pane').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.getElementById('pane-'+name).classList.add('active');
  if(btn) btn.classList.add('active');
  if(name==='signals' && chart) setTimeout(()=>chart.timeScale().fitContent(), 80);
}}

function previewRisk(){{
  const bal = +document.getElementById('rBal').value || 0;
  const r = +document.getElementById('rRisk').value || 0;
  const dd = +document.getElementById('rDd').value || 0;
  document.getElementById('rpTrade').textContent = '$' + (bal*r/100).toFixed(2);
  document.getElementById('rpDaily').textContent = '$' + (bal*dd/100).toFixed(2);
}}

function initChart(){{
  const el=document.getElementById('tvChart');
  if(!window.LightweightCharts || !el || chart) return;
  chart = LightweightCharts.createChart(el, {{
    layout:{{background:{{color:'#080c14'}},textColor:'#94a3b8'}},
    grid:{{vertLines:{{color:'rgba(36,48,74,.45)'}},horzLines:{{color:'rgba(36,48,74,.45)'}}}},
    crosshair:{{mode:1}},
    rightPriceScale:{{borderColor:'rgba(217,176,76,.22)'}},
    timeScale:{{borderColor:'rgba(217,176,76,.22)',timeVisible:true,secondsVisible:false}}
  }});
  series = chart.addCandlestickSeries({{
    upColor:'#16c784',downColor:'#f04452',borderVisible:false,
    wickUpColor:'#16c784',wickDownColor:'#f04452'
  }});
  new ResizeObserver(()=>{{ if(chart && el.clientWidth>0) chart.applyOptions({{width:el.clientWidth}}); }}).observe(el);
}}

function clearPriceLines(){{
  if(!series) return;
  priceLines.forEach(l=>{{ try{{ series.removePriceLine(l); }}catch(_e){{}} }});
  priceLines = [];
  document.getElementById('overlayNote').style.display='none';
}}

function renderMarketChips(markets){{
  const list = (markets && markets.length) ? markets : ['XAUUSD','BTCUSD','EURUSD','ETHUSD','GBPUSD'];
  document.getElementById('mktChips').innerHTML = list.map(m =>
    `<button class="chip ${{m===curSym?'active':''}}" onclick="pickSym('${{m}}')">${{m}}</button>`
  ).join('');
}}

function pickSym(sym){{
  curSym = sym;
  document.querySelectorAll('#mktChips .chip').forEach(c=>c.classList.toggle('active', c.textContent===sym));
  clearPriceLines();
  loadCandles(curSym, curTf);
}}

function pickTf(tf, btn){{
  curTf = tf;
  document.querySelectorAll('#tfBar .tf-btn').forEach(b=>b.classList.remove('active'));
  if(btn) btn.classList.add('active');
  loadCandles(curSym, curTf);
}}

async function loadCandles(sym, tf='H1', overlaySig=null){{
  if(!series) return;
  document.getElementById('chartSymLbl').textContent = sym + ' · ' + tf;
  const d = await api('/api/candles?symbol='+encodeURIComponent(sym)+'&tf='+encodeURIComponent(tf));
  if(d && d.candles && d.candles.length){{
    series.setData(d.candles);
    const first = d.candles[0], last = d.candles[d.candles.length-1];
    document.getElementById('chartPriceNow').textContent = Number(last.close).toFixed(last.close<10?5:2);
    const pct = first.open ? ((last.close - first.open)/first.open*100) : 0;
    const chgEl = document.getElementById('chartChg');
    chgEl.textContent = (pct>=0?'▲ +':'▼ ') + pct.toFixed(2) + '%';
    chgEl.className = 'chg-pill ' + (pct>=0?'chg-up':'chg-dn');
    clearPriceLines();
    if(overlaySig){{
      const lvls = [
        [overlaySig.entry, '#f7de8c', 'ENTRY', 0],
        [overlaySig.stop,  '#f04452', 'SL', 2],
        [overlaySig.tp1,   '#16c784', 'TP1', 2],
        [overlaySig.tp2,   '#2ed59b', 'TP2', 2],
        [overlaySig.tp3,   '#d9b04c', 'TP3', 2],
      ];
      lvls.forEach(([px, col, title, style])=>{{
        if(+px > 0) priceLines.push(series.createPriceLine({{price:+px,color:col,lineWidth:2,lineStyle:style,axisLabelVisible:true,title}}));
      }});
      const note = document.getElementById('overlayNote');
      note.textContent = `👑 مستويات الإشارة مرسومة حية: ${{overlaySig.symbol}} ${{overlaySig.direction}} · دخول ${{overlaySig.entry}} · SL ${{overlaySig.stop}} · TP1 ${{overlaySig.tp1}}`;
      note.style.display='block';
    }}
    chart.timeScale().fitContent();
  }}
}}

function viewSigOnChart(sid){{
  const s = allSignals.find(x=>x.id===sid);
  if(!s) return;
  curSym = s.symbol; curTf = s.timeframe || 'H1';
  document.querySelectorAll('#mktChips .chip').forEach(c=>c.classList.toggle('active', c.textContent===curSym));
  loadCandles(curSym, curTf, s);
  window.scrollTo({{top:0, behavior:'smooth'}});
  toast('📊 تم رسم مستويات الصفقة على الشارت');
}}

function filterSigs(mode, btn){{
  sigFilter = mode;
  btn.parentElement.querySelectorAll('.tf-btn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  renderSignals();
}}

function renderSignals(){{
  const box = document.getElementById('signalsList');
  let list = allSignals || [];
  if(sigFilter==='open') list = list.filter(s=>['ACTIVE','RUNNING','TP1_HIT','TP2_HIT'].includes((s.status||'').toUpperCase()) && !s.closed_at);
  if(sigFilter==='win') list = list.filter(s=>['TP1_HIT','TP2_HIT','TP3_HIT'].includes((s.status||'').toUpperCase()) || (+s.pips_result||0)>0);
  if(!list.length){{
    box.innerHTML = '<div class="empty">لا توجد إشارات مطابقة لهذا الفلتر حالياً.</div>';
    return;
  }}
  box.innerHTML = list.map(s => {{
    const isBuy = (s.direction||'BUY')==='BUY';
    const st = (s.status||'ACTIVE').toUpperCase();
    const pips = +s.pips_result || 0;
    const pipTxt = pips !== 0 ? ` · <b style="color:${{pips>0?'var(--green)':'var(--red)'}}">${{pips>0?'+':''}}${{pips.toFixed(1)}}p</b>` : '';
    return `
      <div class="sig-card ${{isBuy?'buy-glow':'sell-glow'}}">
        <div class="sig-top">
          <div>
            <div class="sig-sym">${{s.symbol}} <span class="badge ${{isBuy?'b-buy':'b-sell'}}">${{isBuy?'▲ BUY':'▼ SELL'}}</span></div>
            <div class="sig-meta">⏱ ${{s.timeframe}} · 🧠 ${{s.strategy}} · ⭐ ${{s.score||0}}/100${{pipTxt}}</div>
          </div>
          <span class="badge b-status">${{st}}</span>
        </div>
        <div class="lvls">
          <div class="lvl"><span>دخول</span><b style="color:var(--gold2)">${{s.entry}}</b></div>
          <div class="lvl"><span>وقف SL</span><b style="color:var(--red)">${{s.stop}}</b></div>
          <div class="lvl"><span>TP1</span><b style="color:var(--green)">${{s.tp1}}</b></div>
          <div class="lvl"><span>TP2</span><b style="color:var(--green)">${{s.tp2}}</b></div>
          <div class="lvl"><span>TP3</span><b style="color:var(--gold2)">${{s.tp3}}</b></div>
        </div>
        <div class="btn-row">
          <button class="btn green" onclick="logJournal(${{s.id}},'TAKEN')">✅ دخلت</button>
          <button class="btn red" onclick="logJournal(${{s.id}},'SKIPPED')">❌ تجاهلت</button>
          <button class="btn ghost" onclick="viewSigOnChart(${{s.id}})">📊 شارت + مستويات</button>
        </div>
      </div>`;
  }}).join('');
}}

async function logJournal(sid, action){{
  const r = await api('/api/journal', {{method:'POST', body:JSON.stringify({{signal_id:sid, action}})}});
  toast(r.ok ? '✅ تم تسجيل قرارك في مفكرة التداول الملكية' : (r.error||'خطأ'), !r.ok);
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
  toast(r.ok ? '🎁 تم تفعيل الهدية الملكية بنجاح!' : (r.error||'كود غير صالح'), !r.ok);
  if(r.ok) boot();
}}

async function boot(){{
  initChart();
  const me = await api('/api/me');
  if(me && me.ok){{
    const pCode = (me.plan && me.plan.code) || 'FREE';
    document.getElementById('kPlan').textContent = (me.plan && me.plan.title) || pCode;
    document.getElementById('planPill').textContent = '👑 ' + pCode;
    const used = (me.quota && me.quota.used) || 0, lim = (me.quota && me.quota.limit) || 0;
    document.getElementById('kQuota').textContent = lim > 0 ? `${{used}} / ${{lim}}` : `${{used}} / ∞`;
    const pctQ = lim > 0 ? Math.min(100, Math.max(8, Math.round(used/lim*100))) : 100;
    document.getElementById('qBarFill').style.width = pctQ + '%';
    renderMarketChips(me.markets);
    if(me.risk){{
      document.getElementById('rBal').value = me.risk.balance;
      document.getElementById('rRisk').value = me.risk.risk_pct;
      document.getElementById('rLev').value = me.risk.leverage;
      document.getElementById('rDd').value = me.risk.max_daily_dd_pct;
      previewRisk();
    }}
    const j = me.journal || {{}};
    const wr = j.win_rate || 0;
    document.getElementById('kWinPill').textContent = `🏆 نجاح ${{wr}}%`;
    document.getElementById('kPips').textContent = `${{(j.net_pips||0)>0?'+':''}}${{j.net_pips||0}}p`;
    document.getElementById('jTaken').textContent = j.taken_count || 0;
    document.getElementById('jWin').textContent = wr + '%';
    document.getElementById('jPips').textContent = `${{(j.net_pips||0)>0?'+':''}}${{j.net_pips||0}}`;
    document.getElementById('jUsd').textContent = `$${{j.net_usd||0}}`;

    const ac = me.academy || {{}}, af = me.affiliate || {{}};
    document.getElementById('kAcad').textContent = `${{ac.overall_pct||0}}%`;
    document.getElementById('acadBox').innerHTML =
      `التقدم العام: <b style="color:var(--gold2)">${{ac.overall_pct||0}}%</b> · الدروس المكتملة: <b>${{ac.total_done||0}} / ${{ac.total_all||15}}</b>`;
    document.getElementById('acadFill').style.width = (ac.overall_pct||0) + '%';
    document.getElementById('affBox').innerHTML =
      `الرتبة: <b style="color:var(--gold2)">${{(af.tier&&af.tier.label)||'برونزي'}}</b> (${{(af.tier&&af.tier.rate_pct)||15}}%) · المتاح للسحب: <b style="color:var(--green)">$${{af.available_usd||0}}</b>`;
  }} else {{
    renderMarketChips();
  }}
  loadCandles(curSym, curTf);

  const sigs = await api('/api/signals');
  allSignals = (sigs && sigs.signals) ? sigs.signals : [];
  renderSignals();

  const lb = await api('/api/leaderboard?period=30d');
  if(lb && lb.rows && lb.rows.length){{
    const medals = ['🥇','🥈','🥉'];
    document.getElementById('lbBox').innerHTML = lb.rows.slice(0,8).map((r,i)=>
      `<div class="lb-row">
        <div class="lb-top">
          <b>${{medals[i]||'🔹'}} ${{r.strategy}}</b>
          <span style="font-family:var(--mono)">${{r.win_rate}}% · <b style="color:${{r.net_pips>=0?'var(--green)':'var(--red)'}}">${{r.net_pips>0?'+':''}}${{r.net_pips}}p</b> (${{r.trades}})</span>
        </div>
        <div class="lb-bar"><div class="lb-fill" style="width:${{Math.min(100,Math.max(4,r.win_rate||0))}}%"></div></div>
      </div>`
    ).join('');
  }} else {{
    document.getElementById('lbBox').innerHTML = '<div class="empty">لا توجد بيانات استراتيجيات كافية بعد.</div>';
  }}

  const nw = await api('/api/news');
  if(nw && nw.events && nw.events.length){{
    document.getElementById('newsBox').innerHTML = nw.events.slice(0,8).map(e=>
      `<div class="news-item">
        <div><span class="badge b-sell" style="margin-left:6px">${{e.currency}}</span><b>${{e.title}}</b></div>
        <span style="font-family:var(--mono);font-size:11px;color:var(--muted)">${{e.time_utc||''}}</span>
      </div>`
    ).join('');
  }} else {{
    document.getElementById('newsBox').innerHTML = '<div class="empty">🟢 الأجواء الاقتصادية هادئة — لا توجد أخبار حمراء قريبة.</div>';
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
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
<link href="https://fonts.googleapis.com/css2?family=Cairo:wght@400;600;700;800;900&family=JetBrains+Mono:wght@500;700;800&display=swap" rel="stylesheet"/>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
:root{{
  --bg:#06080f;--glass:rgba(16,22,36,.82);--border:rgba(148,163,184,.16);
  --gold:#d9b04c;--gold2:#f7de8c;--gold-line:rgba(217,176,76,.35);
  --green:#16c784;--red:#f04452;--cyan:#38bdf8;--text:#f1f5f9;--muted:#94a3b8;
  --mono:'JetBrains Mono',ui-monospace,SFMono-Regular,Menlo,monospace;
}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{
  font-family:'Cairo',-apple-system,BlinkMacSystemFont,'Segoe UI',Tahoma,sans-serif;
  background:
    radial-gradient(ellipse 80% 45% at 50% -10%, rgba(217,176,76,.16), transparent 65%),
    radial-gradient(ellipse 50% 35% at 95% 25%, rgba(22,199,132,.07), transparent 60%),
    var(--bg);
  color:var(--text);padding:14px 14px 40px;max-width:900px;margin:0 auto;min-height:100vh;
}}
.hdr{{
  background:rgba(10,14,24,.86);backdrop-filter:blur(16px);
  border:1px solid var(--gold-line);border-radius:18px;
  display:flex;justify-content:space-between;align-items:center;
  padding:13px 16px;margin-bottom:14px;box-shadow:0 10px 30px rgba(0,0,0,.4);
}}
.hdr h1{{font-size:16px;font-weight:900;background:linear-gradient(90deg,#f7de8c,#d9b04c);-webkit-background-clip:text;-webkit-text-fill-color:transparent}}
.hdr .sub{{font-size:11px;color:var(--muted)}}
.grid4{{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-bottom:14px}}
@media(min-width:640px){{.grid4{{grid-template-columns:repeat(4,1fr)}}}}
.kpi{{
  background:var(--glass);border:1px solid var(--border);border-top:2px solid var(--gold);
  border-radius:16px;padding:13px;box-shadow:0 8px 24px rgba(0,0,0,.32);
}}
.kpi .lbl{{color:var(--muted);font-size:12px;font-weight:700}}
.kpi .val{{font-family:var(--mono);font-size:22px;font-weight:900;margin-top:4px;color:var(--gold2)}}
.card{{
  background:var(--glass);backdrop-filter:blur(16px);border:1px solid var(--border);
  border-radius:18px;padding:15px;margin-bottom:13px;box-shadow:0 10px 28px rgba(0,0,0,.3);
}}
.card.gold-edge{{border-color:var(--gold-line)}}
.card h3{{font-size:15px;font-weight:800;color:var(--gold2);margin-bottom:11px;display:flex;justify-content:space-between;align-items:center}}
.toggles{{display:grid;grid-template-columns:repeat(2,1fr);gap:8px}}
@media(min-width:640px){{.toggles{{grid-template-columns:repeat(3,1fr)}}}}
.btn{{
  border:none;border-radius:11px;padding:10px 12px;font-family:'Cairo',sans-serif;
  font-weight:800;font-size:12px;cursor:pointer;color:#fff;background:#2563eb;transition:.15s;
}}
.btn:active{{transform:scale(.96)}}
.btn.on{{background:linear-gradient(135deg,#0f9d66,#16c784);color:#04120c}}
.btn.off{{background:rgba(30,41,59,.85);border:1px solid var(--border);color:var(--muted)}}
.btn.danger{{background:linear-gradient(135deg,#c92a38,#f04452)}}
.btn.gold{{background:linear-gradient(135deg,#c29024,#f7de8c);color:#06080f}}
.item{{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:10px 0;border-bottom:1px solid var(--border);font-size:13px;flex-wrap:wrap}}
.item:last-child{{border-bottom:none}}
.row-form{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px}}
input,select{{
  background:rgba(6,8,15,.88);border:1px solid var(--border);color:var(--text);
  padding:9px 11px;border-radius:11px;font-size:13px;font-family:var(--mono);outline:none;
}}
input:focus,select:focus{{border-color:var(--gold)}}
.pill{{background:rgba(217,176,76,.16);border:1px solid var(--gold-line);color:var(--gold2);padding:2px 9px;border-radius:999px;font-size:11px;font-weight:800;font-family:var(--mono)}}
.toast{{position:fixed;top:14px;left:50%;transform:translateX(-50%);background:#16c784;color:#04120c;padding:10px 18px;border-radius:12px;font-weight:900;display:none;z-index:99;box-shadow:0 8px 24px rgba(0,0,0,.5)}}
</style>
</head>
<body>
<div class="toast" id="toast"></div>
<div class="hdr">
  <div>
    <h1>👑 لوحة الإدارة الملكية — {config.BRAND}</h1>
    <div class="sub">مركز القيادة التنفيذي · v{config.VERSION}</div>
  </div>
  <button class="btn gold" onclick="loadAdmin()">🔄 تحديث حي</button>
</div>
<div id="authErr" style="display:none;background:rgba(127,29,29,.45);border:1px solid #f04452;padding:15px;border-radius:14px;margin-bottom:12px">
  ⛔ وصول مرفوض — هذه اللوحة مخصصة لـ <b>ADMIN_ID</b> فقط عبر توقيع Telegram WebApp HMAC.
</div>
<div id="adminBody">
  <div class="grid4">
    <div class="kpi"><div class="lbl">إجمالي المستخدمين</div><div class="val" id="kUsers">—</div></div>
    <div class="kpi"><div class="lbl">المشتركون المدفوعون</div><div class="val" style="color:var(--green)" id="kSubs">—</div></div>
    <div class="kpi"><div class="lbl">صفقات مفتوحة الآن</div><div class="val" style="color:var(--cyan)" id="kOpen">—</div></div>
    <div class="kpi"><div class="lbl">طلبات دفع معلقة</div><div class="val" style="color:var(--red)" id="kPay">—</div></div>
  </div>
  <div class="card gold-edge">
    <h3>⚡ مفاتيح التحكم السريع (1-Tap)</h3>
    <div class="toggles" id="togglesBox"></div>
  </div>
  <div class="card gold-edge">
    <h3><span>📊 قنوات الشارت فقط (حسب السوق)</span><span class="pill" id="schedBadge">⏱ يدوي</span></h3>
    <div class="row-form">
      <input id="ccChatId" placeholder="@MyGoldCharts أو -100..." style="flex:1;min-width:150px"/>
      <input id="ccTitle" placeholder="اسم القناة (اختياري)" style="flex:1;min-width:120px"/>
      <select id="ccMkt"><option value="ALL">🌐 جميع الأسواق (ALL)</option></select>
      <button class="btn gold" onclick="addChartCh()">➕ إضافة قناة</button>
    </div>
    <div id="chartChBox"><div style="color:var(--muted)">—</div></div>
  </div>
  <div class="card">
    <h3>🎁 إصدار كود هدية فوري (أي باقة · أي مدة)</h3>
    <div class="row-form">
      <select id="gPlan"><option value="PRO">PRO</option><option value="VIP" selected>VIP</option><option value="ROYAL">ROYAL</option><option value="ACADEMY">ACADEMY</option></select>
      <input id="gDays" type="number" value="30" style="width:90px" placeholder="أيام"/>
      <input id="gUses" type="number" value="1" style="width:80px" placeholder="عدد"/>
      <button class="btn gold" onclick="genGift()">🎟 إصدار كود</button>
    </div>
    <div id="giftOut" style="margin-top:6px;font-weight:800;font-family:var(--mono);color:var(--green)"></div>
  </div>
  <div class="card"><h3>💳 طلبات الدفع المعلقة</h3><div id="payBox">—</div></div>
  <div class="card"><h3>💸 طلبات سحب الشركاء المعلقة</h3><div id="payoutBox">—</div></div>
</div>
<script>
const tg = window.Telegram && window.Telegram.WebApp;
if (tg) {{ tg.ready(); tg.expand(); try{{ tg.setHeaderColor('#06080f'); tg.setBackgroundColor('#06080f'); }}catch(_e){{}} }}
const INIT_DATA = (tg && tg.initData) || '';
function toast(m,e=false){{const el=document.getElementById('toast');el.textContent=m;el.style.background=e?'#f04452':'#16c784';el.style.color=e?'#fff':'#04120c';el.style.display='block';setTimeout(()=>el.style.display='none',2800);}}
async function api(p,o={{}}){{const h=Object.assign({{'X-Telegram-Init-Data':INIT_DATA,'Content-Type':'application/json','ngrok-skip-browser-warning':'1'}},o.headers||{{}});const r=await fetch(p,Object.assign({{}},o,{{headers:h}}));return r.json();}}

async function toggleKey(key){{
  const r = await api('/api/admin/toggle',{{method:'POST',body:JSON.stringify({{key}})}});
  toast(r.ok ? '✅ تم تحديث الإعداد' : (r.error||'خطأ'), !r.ok);
  loadAdmin();
}}
async function addChartCh(){{
  const chat_id = document.getElementById('ccChatId').value.trim();
  if(!chat_id) return toast('أدخل معرف القناة أولاً', true);
  const r = await api('/api/admin/chart-channel',{{method:'POST',body:JSON.stringify({{
    op:'add', chat_id, title:document.getElementById('ccTitle').value.trim(), markets:document.getElementById('ccMkt').value
  }})}});
  toast(r.ok ? '✅ تمت إضافة قناة الشارت بنجاح' : (r.error||'خطأ'), !r.ok);
  if(r.ok){{ document.getElementById('ccChatId').value=''; document.getElementById('ccTitle').value=''; loadAdmin(); }}
}}
async function chartChOp(op, id){{
  const r = await api('/api/admin/chart-channel',{{method:'POST',body:JSON.stringify({{op, id}})}});
  toast(r.ok ? '✅ تم تحديث قناة الشارت' : (r.error||'خطأ'), !r.ok);
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

  const sm = d.chart_schedule_minutes || 0;
  document.getElementById('schedBadge').textContent = sm > 0 ? `⏱ كل ${{sm}} دقيقة` : '⏹ يدوي';

  const mktSel = document.getElementById('ccMkt');
  if(d.markets && d.markets.length && mktSel.options.length <= 1){{
    d.markets.forEach(m => {{ const o = document.createElement('option'); o.value = m; o.textContent = m; mktSel.appendChild(o); }});
  }}

  const cchs = d.chart_channels || [];
  document.getElementById('chartChBox').innerHTML = cchs.length ? cchs.map(c =>
    `<div class="item">
      <span>${{c.enabled?'🟢':'⚪'}} <b>${{c.title||c.chat_id}}</b> <span class="pill">${{c.markets||'ALL'}}</span> <span style="color:var(--muted);font-family:var(--mono);font-size:11px">${{c.chat_id}}</span></span>
      <span style="display:flex;gap:6px">
        <button class="btn ${{c.enabled?'on':'off'}}" onclick="chartChOp('toggle',${{c.id}})">${{c.enabled?'مفعّلة':'متوقفة'}}</button>
        <button class="btn danger" onclick="chartChOp('delete',${{c.id}})">🗑</button>
      </span>
    </div>`
  ).join('') : '<div style="color:var(--muted);font-size:13px">لا توجد قنوات شارت مخصصة بعد — أضف قناة واختر السوق أعلاه.</div>';

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
     <span style="display:flex;gap:6px"><button class="btn on" onclick="actPay(${{p.id}},'approve')">✅</button>
     <button class="btn danger" onclick="actPay(${{p.id}},'reject')">❌</button></span></div>`
  ).join('') : '<div style="color:var(--muted);font-size:13px">لا توجد طلبات دفع معلقة.</div>';

  document.getElementById('payoutBox').innerHTML = d.pending_payouts.length ? d.pending_payouts.map(p=>
    `<div class="item"><span>#${{p.id}} · UID ${{p.user_id}} · <b>${{p.amount_usd}}$</b> · ${{p.wallet_address}}</span>
     <span style="display:flex;gap:6px"><button class="btn on" onclick="actPayout(${{p.id}},'approve')">✅</button>
     <button class="btn danger" onclick="actPayout(${{p.id}},'reject')">❌</button></span></div>`
  ).join('') : '<div style="color:var(--muted);font-size:13px">لا توجد طلبات سحب معلقة.</div>';
}}
loadAdmin();
</script>
</body>
</html>"""
