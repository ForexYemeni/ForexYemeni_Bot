"""Ultra-Luxury Royal Obsidian-Gold Glassmorphic RTL Arabic HTML Templates (v7.4.0):
  1. User Telegram Mini App (`/`) — Royal VIP Terminal with TradingView Lightweight
     Charts + Signal Overlay Price Lines + Live Market & Timeframe Pills + Live
     Risk/Lot Preview + Journal Analytics + Ultra-Luxury Royal Partner Hub (Channel
     Partner Studio + Tiered Affiliate Studio + Direct Payouts + Channel Controls) +
     Academy + Gift Codes + Floating Glass Dock
  2. Super Admin Executive Web Panel (`/admin`) — Live KPIs, 1-Tap System & News
     Auto-Broadcast Controls, 1-Tap Luxury News Broadcast, Admin Strategy Leaderboard,
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
<meta name="theme-color" content="#06080f"/>
<title>{config.BRAND} — Royal Mini App v{config.VERSION}</title>
<style>
html{{background:#06080f;color:#f1f5f9}}
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

/* Floating Frosted Bottom Navigation Dock (5 Luxury Tabs: Signals · Demo · Risk · Partner · Royal) */
.dock-wrap{{position:fixed;bottom:10px;left:10px;right:10px;z-index:50;display:flex;justify-content:center;pointer-events:none}}
.dock{{
  pointer-events:auto;width:100%;max-width:540px;
  background:rgba(12,17,29,.92);backdrop-filter:blur(22px);-webkit-backdrop-filter:blur(22px);
  border:1px solid var(--gold-line);border-radius:20px;
  display:grid;grid-template-columns:repeat(5,1fr);padding:6px;gap:3px;
  box-shadow:0 12px 38px rgba(0,0,0,.65);
}}
.tab{{
  background:none;border:none;color:var(--muted);font-family:'Cairo',sans-serif;
  font-size:10.5px;font-weight:800;cursor:pointer;padding:7px 2px;border-radius:14px;
  display:flex;flex-direction:column;align-items:center;gap:2px;transition:.18s;position:relative;
}}
.tab .ic{{font-size:17px;line-height:1}}
.tab .cnt-dot{{
  position:absolute;top:3px;left:6px;background:var(--green);color:#04120c;
  font-size:9px;font-weight:900;padding:1px 5px;border-radius:999px;font-family:var(--mono);display:none;
}}
.tab.active{{
  color:var(--gold2);
  background:linear-gradient(135deg,rgba(217,176,76,.22),rgba(217,176,76,.06));
  box-shadow:inset 0 0 0 1px rgba(217,176,76,.35);
}}
.autosig-bar{{
  margin-top:10px;display:flex;align-items:center;justify-content:space-between;gap:8px;
  padding:8px 10px;border-radius:13px;background:rgba(6,8,15,.55);border:1px solid var(--border);
}}
.autosig-btn{{
  border:none;border-radius:10px;padding:7px 12px;font-family:'Cairo',sans-serif;
  font-weight:900;font-size:11.5px;cursor:pointer;transition:.18s;white-space:nowrap;
}}
.autosig-btn.on-stop{{background:linear-gradient(135deg,#dc2626,#f04452);color:#fff;box-shadow:0 4px 14px rgba(240,68,82,.35)}}
.autosig-btn.off-start{{background:linear-gradient(135deg,#059669,#16c784);color:#04120c;box-shadow:0 4px 14px rgba(22,199,132,.35)}}
.demo-hud{{
  display:none;margin-bottom:10px;padding:11px 12px;border-radius:15px;
  background:linear-gradient(135deg,rgba(22,199,132,.14),rgba(217,176,76,.10));
  border:1px solid rgba(22,199,132,.42);
}}
.demo-hud-top{{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:6px;flex-wrap:wrap}}
.demo-hud-pnl{{font-family:var(--mono);font-size:17px;font-weight:900}}
.demo-pos-card{{
  background:rgba(6,8,15,.68);border:1px solid var(--border);border-radius:15px;
  padding:12px;margin-bottom:9px;
}}
.demo-pos-card.profit{{border-right:3px solid var(--green)}}
.demo-pos-card.loss{{border-right:3px solid var(--red)}}
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
      <div class="autosig-bar">
        <div style="font-size:12px;font-weight:800">⚡ الإشارات التلقائية: <span id="autoSigStateLbl" style="color:var(--green)">شغّالة</span></div>
        <button class="autosig-btn on-stop" id="autoSigBtn" onclick="toggleAutoSignals()">🔕 إيقاف التلقائية</button>
      </div>
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
      <div class="demo-hud" id="activeDemoHud">
        <div class="demo-hud-top">
          <div>
            <div style="font-size:11px;color:var(--gold2);font-weight:800">🎮 صفقة تجريبية حية على الشارت الآن</div>
            <div id="hudTitle" style="font-size:14px;font-weight:900;font-family:var(--mono)">—</div>
          </div>
          <div style="text-align:left">
            <div class="demo-hud-pnl" id="hudPnl">$0.00</div>
            <div id="hudMeta" style="font-size:11px;color:var(--muted);font-family:var(--mono)">متابعة لحظية...</div>
          </div>
        </div>
        <div class="btn-row" style="margin-top:6px">
          <button class="btn gold" onclick="breakevenActiveDemo()">🛡 تأمين الدخول (BE)</button>
          <button class="btn red" onclick="closeActiveDemo()">✖ إغلاق الصفقة</button>
          <button class="btn ghost" onclick="openTab('demo',document.getElementById('tab-demo'))">📂 كل التجريبي</button>
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

  <!-- PANE 2: LIVE DEMO TRADING DECK (v7.5.0 — Synced with Telegram Bot Demo Account) -->
  <div id="pane-demo" class="pane">
    <div class="hero">
      <div class="hero-top">
        <div>
          <div class="hero-title">🎮 الحساب التجريبي الحي (مربوط بحسابك في البوت)</div>
          <div class="hero-plan" id="demoBalHero">$10,000.00</div>
        </div>
        <button class="btn ghost" style="flex:0;padding:7px 12px" onclick="loadDemoOverview(true)">🔄 تحديث</button>
      </div>
      <div class="hero-metrics">
        <div class="hm"><div class="l">الرصيد العائم Equity</div><div class="v cyan" id="demoEqHero">$10,000</div></div>
        <div class="hm"><div class="l">الربح العائم</div><div class="v green" id="demoFloatHero">$0.00</div></div>
        <div class="hm"><div class="l">نسبة النجاح</div><div class="v gold" id="demoWrHero">—</div></div>
      </div>
    </div>
    <div class="card gold-edge">
      <div class="card-hdr">
        <h3>📂 صفقاتي التجريبية المفتوحة الآن</h3>
        <span class="badge b-gold" id="demoOpenBadge">0 مفتوحة</span>
      </div>
      <div id="demoOpenList"><div class="empty">لا توجد صفقات تجريبية مفتوحة — اضغط «🎮 تجريبي الآن» على أي إشارة لتفعيلها ومتابعتها على الشارت!</div></div>
    </div>
    <div class="card">
      <div class="card-hdr"><h3>📜 أحدث الصفقات التجريبية المغلقة</h3></div>
      <div id="demoClosedList"><div class="empty">لا يوجد سجل صفقات مغلقة بعد.</div></div>
    </div>
  </div>

  <!-- PANE 3: RISK & JOURNAL -->
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

  <!-- PANE 3: ULTRA-LUXURY ROYAL PARTNER HUB (v7.4.0) -->
  <div id="pane-partner" class="pane">
    <!-- Channel Partner Executive Studio (shown when user is an approved Channel Partner) -->
    <div id="chPartnerWrap" style="display:none">
      <div class="hero">
        <div class="hero-top">
          <div>
            <div class="hero-title">👑 بوابة الشريك الملكي المعتمد (Channel Partner)</div>
            <div class="hero-plan" id="pBrandTitle">—</div>
          </div>
          <span class="badge b-gold" id="pCommPill">🥇 0%</span>
        </div>
        <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px">
          <span class="badge b-status" id="pModePill">الوضع: —</span>
          <span class="badge b-buy" id="pVipPill" style="display:none">🔐 بوابة VIP مفعّلة</span>
        </div>
        <div class="hero-metrics" style="grid-template-columns:repeat(2,1fr)">
          <div class="hm"><div class="l">👥 المدعوون / التحويل</div><div class="v cyan" id="pInvited">0</div></div>
          <div class="hm"><div class="l">✅ المشتركون المحوّلون</div><div class="v green" id="pConverted">0</div></div>
          <div class="hm"><div class="l">💰 أرباح جاهزة للسحب</div><div class="v gold" id="pPending">$0.00</div></div>
          <div class="hm"><div class="l">🏦 إجمالي الأرباح / قنوات</div><div class="v green" id="pTotalCh">$0 · 0</div></div>
        </div>
        <div style="margin-top:11px;display:flex;gap:7px">
          <button class="btn gold" onclick="copyText(partnerRefLink,'✅ تم نسخ رابط الشريك الملكي')">📋 نسخ رابط الشريك</button>
          <button class="btn ghost" onclick="shareLink(partnerRefLink)">📤 مشاركة</button>
        </div>
      </div>

      <div class="card gold-edge">
        <div class="card-hdr">
          <h3>📺 قنوات الشريك المربوطة (تحكم لحظي)</h3>
          <span class="badge b-gold" id="pQuotaLbl">الحصة اليومية</span>
        </div>
        <div id="pChannelsList"><div class="empty">لا توجد قنوات مربوطة بعد — اربط قناتك من أمر /partner.</div></div>
      </div>
    </div>

    <!-- Royal Affiliate Studio (available to EVERY user & partner) -->
    <div class="card gold-edge">
      <div class="card-hdr">
        <h3>🤝 برنامج الشركاء والعمولات التراكمي</h3>
        <span class="badge b-gold" id="affTierPill">🥉 برونزي</span>
      </div>
      <div class="grid2" style="margin-bottom:10px">
        <div class="stat-tile"><div class="l">إجمالي الإحالات</div><div class="v cyan" id="afInv">0</div></div>
        <div class="stat-tile"><div class="l">الاشتراكات المحوّلة</div><div class="v green" id="afConv">0</div></div>
        <div class="stat-tile"><div class="l">الرصيد المتاح للسحب</div><div class="v gold" id="afAvail">$0.00</div></div>
        <div class="stat-tile"><div class="l">إجمالي ما تم دفعه</div><div class="v green" id="afPaid">$0.00</div></div>
      </div>
      <div style="font-size:11px;color:var(--muted);margin-bottom:4px" id="afNextTierTxt">التقدم نحو الرتبة التالية</div>
      <div class="qbar" style="margin-top:0;margin-bottom:12px"><div class="qbar-fill" id="afTierBar" style="width:12%"></div></div>
      <div class="field">
        <label>رابط الدعوة الخاص بك (عمولة فورية تلقائية)</label>
        <input id="affLinkInput" readonly value="—"/>
      </div>
      <div class="btn-row">
        <button class="btn gold" onclick="copyText(document.getElementById('affLinkInput').value,'✅ تم نسخ رابط الإحالة الخاص بك')">📋 نسخ الرابط</button>
        <button class="btn ghost" onclick="shareLink(document.getElementById('affLinkInput').value)">📤 مشاركة الرابط</button>
      </div>
    </div>

    <div class="card">
      <div class="card-hdr"><h3>💸 طلب سحب الأرباح (USDT / محفظة)</h3></div>
      <div class="grid2">
        <div class="field"><label>عنوان المحفظة (TRC20 / BEP20)</label><input id="poWallet" placeholder="T... أو 0x..."/></div>
        <div class="field"><label>المبلغ ($) — اتركه فارغاً لسحب الكل</label><input id="poAmt" type="number" step="0.5" placeholder="الرصيد المتاح"/></div>
      </div>
      <button class="btn green" style="width:100%" onclick="requestPartnerPayout()">💸 إرسال طلب السحب للإدارة</button>
    </div>
  </div>

  <!-- PANE 4: ACADEMY & GIFTS -->
  <div id="pane-more" class="pane">
    <div class="card gold-edge">
      <div class="card-hdr"><h3>🎓 أكاديمية ForexYemeni الاحترافية</h3></div>
      <div id="acadBox" style="font-size:13px;margin-bottom:8px">—</div>
      <div class="qbar"><div class="qbar-fill" id="acadFill" style="width:0%"></div></div>
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
    <button class="tab active" id="tab-signals" onclick="openTab('signals',this)"><span class="ic">📡</span><span>الإشارات</span></button>
    <button class="tab" id="tab-demo" onclick="openTab('demo',this)"><span class="cnt-dot" id="demoTabDot">0</span><span class="ic">🎮</span><span>التجريبي</span></button>
    <button class="tab" id="tab-risk" onclick="openTab('risk',this)"><span class="ic">🛡</span><span>المخاطرة</span></button>
    <button class="tab" id="tab-partner" onclick="openTab('partner',this)"><span class="ic">🤝</span><span>الشريك</span></button>
    <button class="tab" id="tab-more" onclick="openTab('more',this)"><span class="ic">👑</span><span>الملكي</span></button>
  </div>
</div>

<script>
// v7.4.4: Zero-blocking Telegram bridge — renders instantly (<50ms) even when telegram.org/unpkg/fonts are blocked by ISP!
function notifyTgNative(){{
  try{{
    if(window.Telegram && window.Telegram.WebApp){{
      const t = window.Telegram.WebApp;
      t.ready(); t.expand();
      try{{ t.setHeaderColor('#06080f'); t.setBackgroundColor('#06080f'); }}catch(_e){{}}
      return;
    }}
  }}catch(_e){{}}
  try{{
    if(window.TelegramWebviewProxy && window.TelegramWebviewProxy.postEvent){{
      window.TelegramWebviewProxy.postEvent('web_app_ready', '');
      window.TelegramWebviewProxy.postEvent('web_app_expand', '');
      window.TelegramWebviewProxy.postEvent('web_app_set_header_color', JSON.stringify({{color:'#06080f'}}));
      window.TelegramWebviewProxy.postEvent('web_app_set_background_color', JSON.stringify({{color:'#06080f'}}));
    }} else if(window.parent && window.parent !== window){{
      window.parent.postMessage(JSON.stringify({{eventType:'web_app_ready',eventData:''}}), '*');
      window.parent.postMessage(JSON.stringify({{eventType:'web_app_expand',eventData:''}}), '*');
    }}
  }}catch(_e){{}}
}}
function getInitData(){{
  try{{
    const t = window.Telegram && window.Telegram.WebApp;
    if(t && t.initData) return t.initData;
  }}catch(_e){{}}
  try{{
    const rawHash = (window.location.hash || '').replace(/^#/, '');
    if(rawHash){{
      const d = new URLSearchParams(rawHash).get('tgWebAppData');
      if(d){{ try{{ sessionStorage.setItem('fysb_tg_init', d); }}catch(_e){{}} return d; }}
    }}
  }}catch(_e){{}}
  try{{
    const d = new URLSearchParams(window.location.search || '').get('tgWebAppData');
    if(d) return d;
  }}catch(_e){{}}
  try{{ return sessionStorage.getItem('fysb_tg_init') || ''; }}catch(_e){{ return ''; }}
}}
notifyTgNative();
const INIT_DATA = getInitData();

let chart, series, priceLines = [], lastCandles = [], lastOverlaySig = null;
let curSym = 'XAUUSD', curTf = 'H1', allSignals = [], sigFilter = 'all', partnerRefLink = '';

function copyText(txt, okMsg){{
  if(!txt || txt==='—') return toast('الرابط غير متاح بعد', true);
  if(navigator.clipboard && navigator.clipboard.writeText){{
    navigator.clipboard.writeText(txt).then(()=>toast(okMsg||'✅ تم النسخ')).catch(()=>toast(txt));
  }} else {{
    toast(okMsg||'✅ تم النسخ');
  }}
}}

function shareLink(url){{
  if(!url || url==='—') return toast('الرابط غير متاح بعد', true);
  const sh = 'https://t.me/share/url?url=' + encodeURIComponent(url);
  const tgObj = window.Telegram && window.Telegram.WebApp;
  if(tgObj && tgObj.openTelegramLink) tgObj.openTelegramLink(sh);
  else window.open(sh, '_blank');
}}

async function togglePartnerChannel(chId){{
  const r = await api('/api/partner/channel-toggle', {{method:'POST', body:JSON.stringify({{channel_id:chId}})}});
  toast(r.ok ? (r.enabled ? '🟢 تم تفعيل القناة' : '⚪ تم إيقاف القناة') : (r.error||'خطأ'), !r.ok);
  if(r.ok) boot();
}}

async function requestPartnerPayout(){{
  const wallet = document.getElementById('poWallet').value.trim();
  const rawAmt = document.getElementById('poAmt').value.trim();
  if(!wallet) return toast('أدخل عنوان المحفظة أولاً', true);
  const body = {{wallet_address: wallet}};
  if(rawAmt) body.amount_usd = +rawAmt;
  const r = await api('/api/partner/payout', {{method:'POST', body:JSON.stringify(body)}});
  toast(r.ok ? `✅ تم رفع طلب سحب $${{r.amount_usd}} للمراجعة` : (r.error||'تعذر إرسال الطلب'), !r.ok);
  if(r.ok){{ document.getElementById('poAmt').value=''; boot(); }}
}}

function toast(msg, err=false){{
  const el=document.getElementById('toast');
  el.textContent=msg;
  el.style.background=err?'linear-gradient(135deg,#c92a38,#f04452)':'linear-gradient(135deg,#16c784,#10b981)';
  el.style.color=err?'#fff':'#04120c';
  el.style.display='block';
  clearTimeout(window._tt);
  window._tt=setTimeout(()=>el.style.display='none', 2800);
}}

const API_BASE = (()=>{{
  try{{
    const q = new URLSearchParams(window.location.search).get('api') || '';
    const clean = q.trim().replace(/\\/+$/, '');
    if(clean && /^https?:\\/\\//i.test(clean)) return clean;
  }}catch(_e){{}}
  return '';
}})();

async function api(path, opts={{}}){{
  const headers = Object.assign({{
    'X-Telegram-Init-Data': INIT_DATA,
    'Content-Type':'application/json',
    'ngrok-skip-browser-warning':'1',
    'X-Pinggy-No-Screen':'1'
  }}, opts.headers||{{}});
  try{{
    const r = await fetch(API_BASE + path, Object.assign({{}}, opts, {{headers}}));
    const ct = (r.headers.get('content-type') || '').toLowerCase();
    if(!ct.includes('application/json')){{
      return {{ok:false, error:'HTTP ' + r.status + ' — تعذر استلام JSON من خادم البوت'}};
    }}
    return await r.json();
  }}catch(err){{
    return {{ok:false, error:'تعذر الاتصال بخادم البوت: ' + (err && err.message ? err.message : 'Network error')}};
  }}
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

function drawFallbackCanvas(candles, overlaySig){{
  const el = document.getElementById('tvChart');
  if(!el || !candles || !candles.length) return;
  el.innerHTML = '';
  const w = Math.max(el.clientWidth || 320, 260), h = el.clientHeight || 270;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const cv = document.createElement('canvas');
  cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr);
  cv.style.width = w + 'px'; cv.style.height = h + 'px';
  el.appendChild(cv);
  const ctx = cv.getContext('2d');
  ctx.scale(dpr, dpr);
  ctx.fillStyle = '#080c14'; ctx.fillRect(0, 0, w, h);
  const slice = candles.slice(-60);
  let lo = Infinity, hi = -Infinity;
  slice.forEach(c => {{ if(c.low < lo) lo = c.low; if(c.high > hi) hi = c.high; }});
  if(overlaySig){{
    [overlaySig.entry, overlaySig.stop, overlaySig.tp1, overlaySig.tp2, overlaySig.tp3].forEach(v => {{
      if(+v > 0){{ if(+v < lo) lo = +v; if(+v > hi) hi = +v; }}
    }});
  }}
  const padY = 18, padR = 54, plotW = w - padR - 8, plotH = h - padY * 2;
  const rng = Math.max(hi - lo, 1e-6);
  const yOf = p => padY + (1 - (p - lo) / rng) * plotH;
  ctx.strokeStyle = 'rgba(36,48,74,.45)'; ctx.lineWidth = 1;
  for(let i = 0; i <= 4; i++){{
    const gy = padY + plotH * i / 4;
    ctx.beginPath(); ctx.moveTo(6, gy); ctx.lineTo(w - padR, gy); ctx.stroke();
    const gp = hi - rng * i / 4;
    ctx.fillStyle = '#94a3b8'; ctx.font = '10px monospace';
    ctx.fillText(gp.toFixed(gp < 10 ? 4 : 1), w - padR + 4, gy + 3);
  }}
  const step = plotW / Math.max(slice.length, 1);
  const bw = Math.max(2, Math.min(8, step * 0.65));
  slice.forEach((c, idx) => {{
    const cx = 8 + idx * step + step * 0.5;
    const up = c.close >= c.open;
    const col = up ? '#16c784' : '#f04452';
    ctx.strokeStyle = col; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(cx, yOf(c.high)); ctx.lineTo(cx, yOf(c.low)); ctx.stroke();
    const y1 = yOf(Math.max(c.open, c.close)), y2 = yOf(Math.min(c.open, c.close));
    ctx.fillStyle = col;
    ctx.fillRect(cx - bw / 2, y1, bw, Math.max(1.5, y2 - y1));
  }});
  if(overlaySig){{
    [[overlaySig.entry,'#f7de8c','E'],[overlaySig.stop,'#f04452','SL'],[overlaySig.tp1,'#16c784','TP1'],[overlaySig.tp2,'#2ed59b','TP2']].forEach(([px,col,lbl])=>{{
      if(+px > 0){{
        const y = yOf(+px);
        ctx.strokeStyle = col; ctx.lineWidth = 1.5; ctx.setLineDash([4,3]);
        ctx.beginPath(); ctx.moveTo(6, y); ctx.lineTo(w - padR, y); ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = col; ctx.font = 'bold 10px monospace';
        ctx.fillText(lbl, 10, Math.max(12, y - 3));
      }}
    }});
  }}
}}

function renderCandlesToChart(candles, overlaySig){{
  if(!candles || !candles.length) return;
  lastCandles = candles; lastOverlaySig = overlaySig || null;
  if(series && chart){{
    series.setData(candles);
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
    }}
    chart.timeScale().fitContent();
  }} else {{
    drawFallbackCanvas(candles, overlaySig);
  }}
}}

async function loadCandles(sym, tf='H1', overlaySig=null){{
  document.getElementById('chartSymLbl').textContent = sym + ' · ' + tf;
  const d = await api('/api/candles?symbol='+encodeURIComponent(sym)+'&tf='+encodeURIComponent(tf));
  if(d && d.candles && d.candles.length){{
    const first = d.candles[0], last = d.candles[d.candles.length-1];
    document.getElementById('chartPriceNow').textContent = Number(last.close).toFixed(last.close<10?5:2);
    const pct = first.open ? ((last.close - first.open)/first.open*100) : 0;
    const chgEl = document.getElementById('chartChg');
    chgEl.textContent = (pct>=0?'▲ +':'▼ ') + pct.toFixed(2) + '%';
    chgEl.className = 'chg-pill ' + (pct>=0?'chg-up':'chg-dn');
    if(overlaySig){{
      const note = document.getElementById('overlayNote');
      note.textContent = `👑 مستويات الإشارة مرسومة حية: ${{overlaySig.symbol}} ${{overlaySig.direction}} · دخول ${{overlaySig.entry}} · SL ${{overlaySig.stop}} · TP1 ${{overlaySig.tp1}}`;
      note.style.display='block';
    }} else {{
      document.getElementById('overlayNote').style.display='none';
    }}
    renderCandlesToChart(d.candles, overlaySig);
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
    const seqLbl = s.seq ? `#${{s.seq}} · ` : '';
    const creatorTxt = s.creator ? `<div style="font-size:10.5px;color:var(--muted);margin-top:2px">${{s.creator}}</div>` : '';
    const demoLbl = s.demo_open ? '🟢 مفعّل بالتجريبي — عرض على الشارت' : '🎮 تجريبي الآن — فتح على الشارت';
    return `
      <div class="sig-card ${{isBuy?'buy-glow':'sell-glow'}}">
        <div class="sig-top">
          <div>
            <div class="sig-sym">${{seqLbl}}${{s.symbol}} <span class="badge ${{isBuy?'b-buy':'b-sell'}}">${{isBuy?'▲ BUY':'▼ SELL'}}</span></div>
            <div class="sig-meta">⏱ ${{s.timeframe}} · 🧠 ${{s.strategy}} · ⭐ ${{s.score||0}}/100${{pipTxt}}</div>
            ${{creatorTxt}}
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
        <div style="margin-bottom:7px">
          <button class="btn gold" style="width:100%" onclick="activateDemoSignal(${{s.id}})">${{demoLbl}}</button>
        </div>
        <div class="btn-row">
          <button class="btn ghost" onclick="viewSigOnChart(${{s.id}})">📊 شارت</button>
          <button class="btn green" onclick="logJournal(${{s.id}},'TAKEN')">✅ دخلت</button>
          <button class="btn red" onclick="logJournal(${{s.id}},'SKIPPED')">❌ تجاهلت</button>
        </div>
      </div>`;
  }}).join('');
}}

let activeDemoPos = null;
let demoOverviewData = null;
let demoPollTimer = null;

function renderAutoSigToggle(isOn){{
  const lbl = document.getElementById('autoSigStateLbl');
  const btn = document.getElementById('autoSigBtn');
  if(!lbl || !btn) return;
  if(isOn){{
    lbl.textContent = 'شغّالة 🟢';
    lbl.style.color = 'var(--green)';
    btn.className = 'autosig-btn on-stop';
    btn.textContent = '🔕 إيقاف التلقائية';
  }} else {{
    lbl.textContent = 'متوقفة 🔴';
    lbl.style.color = 'var(--red)';
    btn.className = 'autosig-btn off-start';
    btn.textContent = '📡 تشغيل التلقائية';
  }}
}}

async function toggleAutoSignals(){{
  const r = await api('/api/user/auto-signals', {{method:'POST', body:'{{}}'}});
  if(r && r.ok){{
    renderAutoSigToggle(!!r.auto_signals);
    toast(r.auto_signals ? '🟢 تم تشغيل الإشارات التلقائية' : '🔴 تم إيقاف الإشارات التلقائية');
  }} else {{
    toast((r && r.error) || 'تعذر التحديث', true);
  }}
}}

function renderActiveDemoHud(){{
  const hud = document.getElementById('activeDemoHud');
  if(!hud) return;
  if(!activeDemoPos){{
    hud.style.display = 'none';
    return;
  }}
  const openList = (demoOverviewData && demoOverviewData.open) || [];
  const liveMatch = openList.find(p => Number(p.id) === Number(activeDemoPos.id));
  if(liveMatch) activeDemoPos = Object.assign({{}}, activeDemoPos, liveMatch);
  const p = activeDemoPos;
  const pnl = +(p.floating_pnl || 0);
  const isBuy = (p.direction||'BUY') === 'BUY';
  hud.style.display = 'block';
  document.getElementById('hudTitle').textContent = `${{p.symbol}} ${{isBuy?'▲ شراء':'▼ بيع'}} · ${{p.lots}} لوت @ ${{p.entry}}`;
  const pnlEl = document.getElementById('hudPnl');
  pnlEl.textContent = `${{pnl >= 0 ? '+' : ''}}$${{pnl.toFixed(2)}}`;
  pnlEl.style.color = pnl >= 0 ? 'var(--green)' : 'var(--red)';
  const curPx = p.current_price ? `السعر الحي: ${{p.current_price}} · ` : '';
  document.getElementById('hudMeta').textContent = `${{curPx}}SL ${{p.sl||'—'}} · TP ${{p.tp||'—'}}`;
}}

function renderDemoDeck(ov){{
  if(!ov || !ov.ok) return;
  demoOverviewData = ov;
  const bal = +(ov.balance || 0), eq = +(ov.equity || bal), fl = +(ov.floating || 0);
  document.getElementById('demoBalHero').textContent = `$${{bal.toLocaleString('en-US',{{minimumFractionDigits:2,maximumFractionDigits:2}})}}`;
  document.getElementById('demoEqHero').textContent = `$${{eq.toLocaleString('en-US',{{minimumFractionDigits:2,maximumFractionDigits:2}})}}`;
  const flEl = document.getElementById('demoFloatHero');
  flEl.textContent = `${{fl>=0?'+':''}}$${{fl.toFixed(2)}}`;
  flEl.style.color = fl >= 0 ? 'var(--green)' : 'var(--red)';
  document.getElementById('demoWrHero').textContent = ov.win_rate != null ? `${{Math.round(ov.win_rate)}}%` : '—';

  const openArr = ov.open || [];
  document.getElementById('demoOpenBadge').textContent = `${{openArr.length}} مفتوحة`;
  const dot = document.getElementById('demoTabDot');
  if(dot){{ dot.textContent = openArr.length; dot.style.display = openArr.length ? 'inline-block' : 'none'; }}

  const openBox = document.getElementById('demoOpenList');
  openBox.innerHTML = openArr.length ? openArr.map(p => {{
    const pnl = +(p.floating_pnl || 0);
    const isBuy = (p.direction||'BUY') === 'BUY';
    const pxTxt = p.current_price ? ` · الحي: <b>${{p.current_price}}</b>` : '';
    return `
      <div class="demo-pos-card ${{pnl>=0?'profit':'loss'}}">
        <div class="sig-top" style="margin-bottom:6px">
          <div>
            <div class="sig-sym">${{p.symbol}} <span class="badge ${{isBuy?'b-buy':'b-sell'}}">${{isBuy?'▲ شراء':'▼ بيع'}}</span></div>
            <div class="sig-meta">📦 ${{p.lots}} لوت · دخول <b>${{p.entry}}</b>${{pxTxt}}</div>
          </div>
          <div style="font-family:var(--mono);font-size:15px;font-weight:900;color:${{pnl>=0?'var(--green)':'var(--red)'}}">${{pnl>=0?'+':''}}$${{pnl.toFixed(2)}}</div>
        </div>
        <div class="sig-meta" style="margin-bottom:8px">🛑 SL: ${{p.sl||'—'}} · 🏁 TP: ${{p.tp||'—'}}</div>
        <div class="btn-row">
          <button class="btn gold" onclick="focusDemoPosOnChart(${{p.id}})">📈 عرض على الشارت</button>
          <button class="btn ghost" onclick="breakevenDemoPos(${{p.id}})">🛡 تأمين BE</button>
          <button class="btn red" onclick="closeDemoPos(${{p.id}})">✖ إغلاق</button>
        </div>
      </div>`;
  }}).join('') : '<div class="empty">لا توجد صفقات تجريبية مفتوحة — اضغط «🎮 تجريبي الآن» على أي إشارة لتفعيلها ومتابعتها لحظياً!</div>';

  const closedArr = ov.closed || [];
  const clBox = document.getElementById('demoClosedList');
  clBox.innerHTML = closedArr.length ? closedArr.map(p => {{
    const pnl = +(p.pnl || 0);
    return `<div class="news-item">
      <div><b>${{p.symbol}}</b> <span style="font-size:11px;color:var(--muted)">${{p.direction}} · ${{p.lots}} لوت · ${{p.reason||p.status}}</span></div>
      <b style="font-family:var(--mono);color:${{pnl>=0?'var(--green)':'var(--red)'}}">${{pnl>=0?'+':''}}$${{pnl.toFixed(2)}}</b>
    </div>`;
  }}).join('') : '<div class="empty">لا توجد صفقات مغلقة بعد.</div>';

  renderActiveDemoHud();
  if(openArr.length && !demoPollTimer){{
    demoPollTimer = setInterval(()=>loadDemoOverview(false), 8000);
  }} else if(!openArr.length && demoPollTimer){{
    clearInterval(demoPollTimer); demoPollTimer = null;
  }}
}}

async function loadDemoOverview(showToast=false){{
  const ov = await api('/api/demo/overview');
  if(ov && ov.ok){{
    renderDemoDeck(ov);
    if(showToast) toast('✅ تم تحديث بيانات الحساب التجريبي الحي');
  }}
}}

async function activateDemoSignal(sid){{
  toast('🎮 جارٍ تفعيل الإشارة على حسابك التجريبي...');
  const r = await api('/api/demo/open-signal', {{method:'POST', body:JSON.stringify({{signal_id:sid}})}});
  if(!r || !r.ok){{
    toast((r && r.error) || 'تعذر تفعيل الصفقة التجريبية', true);
    return;
  }}
  activeDemoPos = r.position;
  if(r.overview) renderDemoDeck(r.overview);
  const sig = r.signal;
  if(sig){{
    if(!allSignals.find(x => Number(x.id) === Number(sig.id))) allSignals.unshift(sig);
    allSignals.forEach(x => {{ if(Number(x.id) === Number(sig.id)) x.demo_open = true; }});
    renderSignals();
    openTab('signals', document.getElementById('tab-signals'));
    curSym = sig.symbol; curTf = sig.timeframe || 'H1';
    loadCandles(sig.symbol, curTf, sig);
    window.scrollTo({{top:0, behavior:'smooth'}});
  }}
  renderActiveDemoHud();
  toast(r.already_open ? '🟢 الصفقة نشطة مسبقاً — تُتابع الآن حياً على الشارت!' : '🎮 تم تفعيل الإشارة بالتجريبي وفتحها حياً على الشارت!');
}}

function focusDemoPosOnChart(pid){{
  const openArr = (demoOverviewData && demoOverviewData.open) || [];
  const p = openArr.find(x => Number(x.id) === Number(pid));
  if(!p) return;
  activeDemoPos = p;
  const linkedSig = (allSignals || []).find(s => Number(s.id) === Number(p.signal_id));
  const ovSig = linkedSig || {{
    id: p.signal_id || p.id, symbol: p.symbol, direction: p.direction,
    timeframe: curTf, entry: p.entry, stop: p.sl || 0, tp1: p.tp || 0, tp2: 0, tp3: p.tp || 0
  }};
  openTab('signals', document.getElementById('tab-signals'));
  curSym = p.symbol;
  loadCandles(p.symbol, ovSig.timeframe || 'H1', ovSig);
  renderActiveDemoHud();
  window.scrollTo({{top:0, behavior:'smooth'}});
}}

async function closeDemoPos(pid){{
  const r = await api('/api/demo/close', {{method:'POST', body:JSON.stringify({{position_id:pid}})}});
  if(r && r.ok){{
    if(activeDemoPos && Number(activeDemoPos.id) === Number(pid)) activeDemoPos = null;
    if(r.overview) renderDemoDeck(r.overview);
    toast(`✅ أُغلقت الصفقة — النتيجة: ${{(+(r.pnl||0))>=0?'+':''}}$${{(+(r.pnl||0)).toFixed(2)}}`);
  }} else toast((r && r.error) || 'تعذر الإغلاق', true);
}}

async function breakevenDemoPos(pid){{
  const r = await api('/api/demo/breakeven', {{method:'POST', body:JSON.stringify({{position_id:pid}})}});
  if(r && r.ok){{
    if(r.overview) renderDemoDeck(r.overview);
    toast('🛡 تم نقل وقف الخسارة إلى نقطة الدخول (تأمين الصفقة)');
  }} else toast((r && r.error) || 'تعذر التأمين', true);
}}

function closeActiveDemo(){{ if(activeDemoPos) closeDemoPos(activeDemoPos.id); }}
function breakevenActiveDemo(){{ if(activeDemoPos) breakevenDemoPos(activeDemoPos.id); }}

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
    renderAutoSigToggle(me.auto_signals !== false);
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

    const ac = me.academy || {{}}, af = me.affiliate || {{}}, pr = me.partner || {{}};
    document.getElementById('kAcad').textContent = `${{ac.overall_pct||0}}%`;
    document.getElementById('acadBox').innerHTML =
      `التقدم العام: <b style="color:var(--gold2)">${{ac.overall_pct||0}}%</b> · الدروس المكتملة: <b>${{ac.total_done||0}} / ${{ac.total_all||15}}</b>`;
    document.getElementById('acadFill').style.width = (ac.overall_pct||0) + '%';

    // Render Royal Affiliate Partner Studio
    const tier = af.tier || {{}};
    document.getElementById('affTierPill').textContent = `${{tier.label||'برونزي'}} · ${{tier.rate_pct||15}}%`;
    document.getElementById('afInv').textContent = af.invited || 0;
    document.getElementById('afConv').textContent = af.converted || 0;
    document.getElementById('afAvail').textContent = `$${{(+af.available_usd||0).toFixed(2)}}`;
    document.getElementById('afPaid').textContent = `$${{(+af.paid_usd||0).toFixed(2)}}`;
    document.getElementById('affLinkInput').value = af.referral_link || '—';
    const nextNeed = (af.next_tier && af.next_tier.min_converted) || 0;
    const convN = af.converted || 0;
    const tPct = nextNeed > 0 ? Math.min(100, Math.max(8, Math.round(convN / nextNeed * 100))) : 100;
    document.getElementById('afTierBar').style.width = tPct + '%';
    document.getElementById('afNextTierTxt').textContent = af.next_tier
      ? `التقدم نحو رتبة ${{af.next_tier.label}} (${{af.next_tier.rate_pct}}%): ${{convN}} / ${{nextNeed}} مشترك`
      : '👑 وصلت لأعلى رتبة عمولات ملكية!';

    // Render Executive Channel Partner Studio if registered partner
    if(pr.is_channel_partner){{
      document.getElementById('chPartnerWrap').style.display = 'block';
      partnerRefLink = pr.referral_link || '';
      const pst = pr.stats || {{}}, pq = pr.manual_quota || {{}};
      document.getElementById('pBrandTitle').textContent = pr.brand_label || pr.title || 'شريك ملكي';
      document.getElementById('pCommPill').textContent = `🥇 عمولة ${{pr.commission_pct||0}}%`;
      document.getElementById('pModePill').textContent = `الوضع: ${{pr.post_mode_ar||pr.post_mode}}`;
      document.getElementById('pVipPill').style.display = pr.perm_vip_gatekeeper ? 'inline-block' : 'none';
      document.getElementById('pInvited').textContent = `${{pst.invited||0}} (${{pst.conv_rate_pct||0}}%)`;
      document.getElementById('pConverted').textContent = pst.converted || 0;
      document.getElementById('pPending').textContent = `$${{(+pst.earned_pending||0).toFixed(2)}}`;
      document.getElementById('pTotalCh').textContent = `$${{(+pst.total_earned||0).toFixed(0)}} · ${{pst.channels_count||0}} قناة`;
      document.getElementById('pQuotaLbl').textContent = `تحليل ${{pq.analysis_used||0}}/${{pq.analysis_limit||0}} · إشارة ${{pq.signal_used||0}}/${{pq.signal_limit||0}}`;
      const chs = pr.channels || [];
      document.getElementById('pChannelsList').innerHTML = chs.length ? chs.map(c =>
        `<div class="news-item">
          <div>
            <b>${{c.enabled?'🟢':'⚪'}} ${{c.title}}</b>
            <div style="font-size:11px;color:var(--muted);font-family:var(--mono)">${{c.markets||'ALL'}} · ${{c.post_mode}}${{c.is_vip_gate?' · 🔐 VIP':''}}</div>
          </div>
          <button class="btn ${{c.enabled?'green':'ghost'}}" style="flex:0;min-width:74px;padding:6px 10px" onclick="togglePartnerChannel(${{c.id}})">${{c.enabled?'مفعّلة':'متوقفة'}}</button>
        </div>`
      ).join('') : '<div class="empty">لا توجد قنوات مربوطة بعد — اربط قناتك عبر /partner.</div>';
    }} else {{
      document.getElementById('chPartnerWrap').style.display = 'none';
    }}
  }} else {{
    renderMarketChips();
    if(me && me.error) toast('⚠️ ' + me.error, true);
  }}
  loadCandles(curSym, curTf);

  const sigs = await api('/api/signals');
  allSignals = (sigs && sigs.signals) ? sigs.signals : [];
  renderSignals();
  loadDemoOverview(false);

  // v7.5.0 Deep-link auto-activation: /?demo_sig=<sid> opens & activates signal on live chart immediately!
  const qs = new URLSearchParams(window.location.search);
  const demoSigId = Number(qs.get('demo_sig') || 0);
  if(demoSigId > 0 && !window._demoSigApplied){{
    window._demoSigApplied = true;
    activateDemoSignal(demoSigId);
    return;
  }}

  // Deep-link tab support (e.g. /?tab=partner or /?tab=demo)
  const reqTab = qs.get('tab');
  if(reqTab && document.getElementById('pane-' + reqTab) && !window._tabApplied){{
    window._tabApplied = true;
    openTab(reqTab, document.getElementById('tab-' + reqTab));
  }}
}}
function loadOptionalAssets(){{
  // Non-blocking fonts & chart CDN — NEVER delays first paint even if ISP blocks external CDNs!
  try{{
    const ln = document.createElement('link');
    ln.rel = 'stylesheet';
    ln.href = 'https://fonts.googleapis.com/css2?family=Cairo:wght@400;600;700;800;900&family=JetBrains+Mono:wght@500;700;800&display=swap';
    document.head.appendChild(ln);
  }}catch(_e){{}}
  try{{
    const ts = document.createElement('script');
    ts.async = true; ts.src = 'https://telegram.org/js/telegram-web-app.js';
    ts.onload = ()=>{{ notifyTgNative(); }};
    document.body.appendChild(ts);
  }}catch(_e){{}}
  const cdns = [
    'https://cdn.jsdelivr.net/npm/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js',
    'https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js'
  ];
  const tryChartCdn = (i)=>{{
    if(i >= cdns.length || window.LightweightCharts) return;
    const s = document.createElement('script');
    s.async = true; s.src = cdns[i];
    s.onload = ()=>{{
      const el = document.getElementById('tvChart');
      if(el) el.innerHTML = '';
      initChart();
      if(lastCandles.length) renderCandlesToChart(lastCandles, lastOverlaySig);
    }};
    s.onerror = ()=>tryChartCdn(i + 1);
    document.body.appendChild(s);
  }};
  tryChartCdn(0);
}}
boot();
setTimeout(loadOptionalAssets, 50);
</script>
</body>
</html>"""


def render_admin_panel_html() -> str:
    return f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no"/>
<meta name="theme-color" content="#06080f"/>
<title>{config.BRAND} — Super Admin Web Panel v{config.VERSION}</title>
<style>
html{{background:#06080f;color:#f1f5f9}}
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
    <h3><span>📡 مركز إدارة الإشارات الملكي (الأحدث · المنشئ · الأداء)</span><span class="pill" id="sigCountPill">0 إشارة</span></h3>
    <div id="adminSignalsBox"><div style="color:var(--muted);font-size:13px">⏳ جارٍ تحميل الإشارات...</div></div>
  </div>
  <div class="card gold-edge">
    <h3>⚡ مفاتيح التحكم السريع وبث الأخبار (1-Tap)</h3>
    <div class="toggles" id="togglesBox"></div>
    <div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap">
      <button class="btn gold" style="flex:1" onclick="sendLuxuryNewsNow()">📤 إرسال نشرة الأخبار الاقتصادية الفاخرة الآن</button>
    </div>
  </div>
  <div class="card gold-edge">
    <h3><span>📰 التقويم الاقتصادي الحقيقي (للإدارة فقط)</span><span class="pill" id="newsCountPill">0 خبر</span></h3>
    <div id="adminNewsBox"><div style="color:var(--muted)">—</div></div>
  </div>
  <div class="card">
    <h3><span>🏆 مصفوفة أداء الاستراتيجيات — 30 يوماً (للإدارة فقط)</span><span class="pill" id="lbSumPill">—</span></h3>
    <div id="adminLbBox"><div style="color:var(--muted)">—</div></div>
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
// v7.4.4: Zero-blocking Telegram Admin bridge — instant paint even when telegram.org is blocked!
function notifyTgNative(){{
  try{{
    if(window.Telegram && window.Telegram.WebApp){{
      const t = window.Telegram.WebApp;
      t.ready(); t.expand();
      try{{ t.setHeaderColor('#06080f'); t.setBackgroundColor('#06080f'); }}catch(_e){{}}
      return;
    }}
  }}catch(_e){{}}
  try{{
    if(window.TelegramWebviewProxy && window.TelegramWebviewProxy.postEvent){{
      window.TelegramWebviewProxy.postEvent('web_app_ready', '');
      window.TelegramWebviewProxy.postEvent('web_app_expand', '');
      window.TelegramWebviewProxy.postEvent('web_app_set_header_color', JSON.stringify({{color:'#06080f'}}));
      window.TelegramWebviewProxy.postEvent('web_app_set_background_color', JSON.stringify({{color:'#06080f'}}));
    }} else if(window.parent && window.parent !== window){{
      window.parent.postMessage(JSON.stringify({{eventType:'web_app_ready',eventData:''}}), '*');
      window.parent.postMessage(JSON.stringify({{eventType:'web_app_expand',eventData:''}}), '*');
    }}
  }}catch(_e){{}}
}}
function getInitData(){{
  try{{
    const t = window.Telegram && window.Telegram.WebApp;
    if(t && t.initData) return t.initData;
  }}catch(_e){{}}
  try{{
    const rawHash = (window.location.hash || '').replace(/^#/, '');
    if(rawHash){{
      const d = new URLSearchParams(rawHash).get('tgWebAppData');
      if(d){{ try{{ sessionStorage.setItem('fysb_tg_init', d); }}catch(_e){{}} return d; }}
    }}
  }}catch(_e){{}}
  try{{
    const d = new URLSearchParams(window.location.search || '').get('tgWebAppData');
    if(d) return d;
  }}catch(_e){{}}
  try{{ return sessionStorage.getItem('fysb_tg_init') || ''; }}catch(_e){{ return ''; }}
}}
notifyTgNative();
const INIT_DATA = getInitData();
function toast(m,e=false){{const el=document.getElementById('toast');el.textContent=m;el.style.background=e?'#f04452':'#16c784';el.style.color=e?'#fff':'#04120c';el.style.display='block';setTimeout(()=>el.style.display='none',2800);}}
const API_BASE=(()=>{{try{{const q=new URLSearchParams(window.location.search).get('api')||'';const c=q.trim().replace(/\\/+$/,'');if(c&&/^https?:\\/\\//i.test(c))return c;}}catch(_e){{}}return '';}})();
async function api(p,o={{}}){{const h=Object.assign({{'X-Telegram-Init-Data':INIT_DATA,'Content-Type':'application/json','ngrok-skip-browser-warning':'1','X-Pinggy-No-Screen':'1'}},o.headers||{{}});try{{const r=await fetch(API_BASE+p,Object.assign({{}},o,{{headers:h}}));const ct=(r.headers.get('content-type')||'').toLowerCase();if(!ct.includes('application/json'))return {{ok:false,status:r.status,error:'HTTP '+r.status}};const j=await r.json();j._status=r.status;return j;}}catch(err){{return {{ok:false,netErr:true,error:'تعذر الاتصال بخادم البوت ('+(API_BASE||'same-origin')+')'}};}}}}

async function toggleKey(key){{
  const r = await api('/api/admin/toggle',{{method:'POST',body:JSON.stringify({{key}})}});
  toast(r.ok ? '✅ تم تحديث الإعداد' : (r.error||'خطأ'), !r.ok);
  loadAdmin();
}}
async function sendLuxuryNewsNow(){{
  const r = await api('/api/admin/news-broadcast',{{method:'POST',body:'{{}}'}});
  toast(r.ok ? '📤 تم إرسال نشرة الأخبار الفاخرة للمستلمين المحددين' : (r.error||'خطأ'), !r.ok);
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
async function cancelAdminSig(id){{
  const r = await api('/api/admin/signal',{{method:'POST',body:JSON.stringify({{signal_id:id, op:'cancel'}})}});
  toast(r.ok ? '🚫 تم إلغاء الإشارة #' + id : (r.error||'خطأ'), !r.ok);
  loadAdmin();
}}
function openSigDemoChart(id){{
  const base = API_BASE ? ('../?api=' + encodeURIComponent(API_BASE) + '&demo_sig=' + id) : ('/?demo_sig=' + id);
  window.location.href = base;
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
    const errEl = document.getElementById('authErr');
    if(d && d.netErr){{
      errEl.innerHTML = '⚠️ <b>تعذر الاتصال بخادم البوت عبر النفق</b><br><span style="font-size:12px;color:#cbd5e1">' + (d.error||'') + '</span>';
    }} else if(d && d.error && d._status !== 401 && d._status !== 403){{
      errEl.innerHTML = '⚠️ <b>خطأ من خادم البوت:</b> ' + d.error;
    }}
    errEl.style.display='block';
    document.getElementById('adminBody').style.display='none';
    return;
  }}
  document.getElementById('authErr').style.display='none';
  document.getElementById('adminBody').style.display='block';
  document.getElementById('kUsers').textContent = d.kpis.users;
  document.getElementById('kSubs').textContent = d.kpis.active_paid_subs;
  document.getElementById('kOpen').textContent = d.kpis.open_signals;
  document.getElementById('kPay').textContent = d.pending_payments.length;

  const rSigs = d.recent_signals || [];
  document.getElementById('sigCountPill').textContent = rSigs.length + ' إشارة';
  document.getElementById('adminSignalsBox').innerHTML = rSigs.length ? rSigs.map(s => {{
    const isBuy = (s.direction||'BUY').toUpperCase() === 'BUY';
    const st = (s.status||'ACTIVE').toUpperCase();
    const isWin = ['TP1_HIT','TP2_HIT','TP3_HIT'].includes(st);
    const isOpen = ['ACTIVE','RUNNING','TP1_HIT','TP2_HIT'].includes(st) && !s.closed_at;
    const stCol = isWin ? '#16c784' : (isOpen ? '#38bdf8' : '#f04452');
    const pips = +(s.pips_result || 0);
    const pTxt = pips ? ` · <b style="color:${{pips>0?'#16c784':'#f04452'}}">${{pips>0?'+':''}}${{pips.toFixed(1)}}p</b>` : '';
    return `<div class="item">
      <div style="flex:1;min-width:190px">
        <div><b style="color:var(--gold2)">#${{s.seq||s.id}}</b> <b>${{s.symbol}}</b>
          <span class="pill" style="color:${{isBuy?'#16c784':'#f04452'}}">${{isBuy?'▲ شراء':'▼ بيع'}}</span>
          <span class="pill" style="color:${{stCol}}">${{st}}</span>${{pTxt}}
        </div>
        <div style="font-size:11px;color:var(--muted);margin-top:3px">
          ⏱ ${{s.timeframe}} · 🧠 ${{s.strategy}} (${{s.score||0}}/100) · ${{s.creator||'🤖 تلقائي'}} · 📬 ${{s.deliveries||0}} · 🎮 ${{s.demo_count||0}}
        </div>
      </div>
      <div style="display:flex;gap:6px">
        <button class="btn on" onclick="openSigDemoChart(${{s.id}})">🎮 تجريبي</button>
        ${{isOpen ? `<button class="btn danger" onclick="cancelAdminSig(${{s.id}})">🚫</button>` : ''}}
      </div>
    </div>`;
  }}).join('') : '<div style="color:var(--muted);font-size:13px">لا توجد إشارات مسجلة بعد.</div>';

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
    news_guard_enabled:'📰 درع الأخبار', news_auto_broadcast:'📢 بث الأخبار التلقائي',
    news_broadcast_users:'👥 بث للمستخدمين', news_broadcast_partners:'🤝 بث للشركاء',
    news_broadcast_channels:'📺 بث للقنوات', charts_enabled:'📊 الشارتات المحلية',
    academy_enabled:'🎓 الأكاديمية', crypto_api_enabled:'🔗 منصات الكريبتو'
  }};
  document.getElementById('togglesBox').innerHTML = Object.entries(labels).map(([k,lbl])=>{{
    const on = !!d.toggles[k];
    const cls = k==='global_kill_switch' ? (on?'danger':'off') : (on?'on':'off');
    return `<button class="btn ${{cls}}" onclick="toggleKey('${{k}}')">${{lbl}}: ${{on?'🟢 مفعّل':'⚪ متوقف'}}</button>`;
  }}).join('');

  // Admin-Only News Calendar & Honest Feed Status
  const nev = d.news_events || [];
  document.getElementById('newsCountPill').textContent = nev.length + ' خبر قوي';
  if(nev.length){{
    document.getElementById('adminNewsBox').innerHTML = nev.slice(0,8).map(e=>
      `<div class="item">
        <span><span class="pill" style="background:rgba(240,68,82,.18);border-color:rgba(240,68,82,.4);color:#f04452">${{e.currency}}</span> <b>${{e.title}}</b></span>
        <span style="font-family:var(--mono);font-size:11px;color:var(--muted)">${{e.time_utc||''}}</span>
      </div>`
    ).join('');
  }} else if(d.news_feed_error){{
    document.getElementById('adminNewsBox').innerHTML = `<div style="color:#f04452;font-size:13px">⚠️ تعذر جلب التقويم الاقتصادي حالياً: ${{d.news_feed_error}}</div>`;
  }} else {{
    document.getElementById('adminNewsBox').innerHTML = '<div style="color:var(--muted);font-size:13px">🟢 لا توجد أخبار عالية التأثير خلال 48 ساعة.</div>';
  }}

  // Admin-Only Strategy Leaderboard (fixed real list)
  const lb = d.leaderboard || {{}};
  const lbRows = lb.strategies || [];
  document.getElementById('lbSumPill').textContent = `${{lb.total_trades||0}} صفقة · نجاح ${{lb.overall_win_rate||0}}%`;
  const medals = ['🥇','🥈','🥉'];
  document.getElementById('adminLbBox').innerHTML = lbRows.length ? lbRows.slice(0,8).map((r,i)=>
    `<div class="item">
      <span>${{medals[i]||'🔹'}} <b>${{r.name||r.strategy||r.code}}</b> <span class="pill">${{r.code}}</span></span>
      <span style="font-family:var(--mono);font-size:12px">${{r.win_rate}}% · <b style="color:${{r.net_pips>=0?'var(--green)':'var(--red)'}}">${{r.net_pips>0?'+':''}}${{r.net_pips}}p</b> (${{r.trades}})</span>
    </div>`
  ).join('') : '<div style="color:var(--muted);font-size:13px">لا توجد صفقات مغلقة كافية في الفترة المختارة بعد.</div>';

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
setTimeout(()=>{{
  try{{
    const ln = document.createElement('link');
    ln.rel = 'stylesheet';
    ln.href = 'https://fonts.googleapis.com/css2?family=Cairo:wght@400;600;700;800;900&family=JetBrains+Mono:wght@500;700;800&display=swap';
    document.head.appendChild(ln);
  }}catch(_e){{}}
  try{{
    const ts = document.createElement('script');
    ts.async = true; ts.src = 'https://telegram.org/js/telegram-web-app.js';
    ts.onload = ()=>{{ notifyTgNative(); }};
    document.body.appendChild(ts);
  }}catch(_e){{}}
}}, 50);
</script>
</body>
</html>"""
