"""Demo (paper) trading engine — realistic simulation on REAL market prices.

Fully separated from live trading:
  * Virtual balances only; nothing touches a broker.
  * Entry/exit prices come from the real-time provider chain with spread +
    slippage modeled conservatively, never fabricated profits.
  * Equity = balance + floating P&L (live prices); Drawdown tracked vs peak.

User-owned demo settings (per plan: user controls his own paper account):
  * balance (user-resettable), risk_pct per trade, lot mode (auto-by-risk /
    fixed), auto_trade flag — when ON, every delivered signal is executed
    automatically on his demo account and tracked to TP/SL with luxury
    Telegram updates incl. the half-TP1 "secure profit" partial close.
"""
from fysb import config, db, instruments
from fysb.engines import market_data
from fysb.config import log

DEMO_DEFAULT_BALANCE = 10000.0
SLIPPAGE_POINTS_DEFAULT = 1  # in points, applied against the trader
PARTIAL_FRACTION = 0.5       # close 50% at half-TP1 (secure profit rule)
HALF_TP1_TRIGGER = 0.5       # progress ratio entry->TP1


def get_or_create_account(user_id: int) -> dict:
    with db.conn() as c:
        r = c.execute("SELECT * FROM demo_accounts WHERE user_id=?", (user_id,)).fetchone()
        if r:
            return dict(r)
        bal = db.get_setting_float("demo_start_balance", DEMO_DEFAULT_BALANCE)
        cur = c.execute("INSERT INTO demo_accounts(user_id,balance,peak_equity,created_at) VALUES(?,?,?,?)",
                        (user_id, bal, bal, config.now()))
        return {"id": cur.lastrowid, "user_id": user_id, "balance": bal, "peak_equity": bal,
                "currency": "USD", "leverage": 100, "created_at": config.now(),
                "risk_pct": 1.0, "lot_mode": "auto", "fixed_lot": 0.01, "auto_trade": 0, "partial_tp_be": 1}


def demo_settings(user_id: int) -> dict:
    acc = get_or_create_account(user_id)
    ptb = acc.get("partial_tp_be")
    return {"balance": acc["balance"], "risk_pct": float(acc.get("risk_pct") or 1.0),
            "lot_mode": (acc.get("lot_mode") or "auto"), "fixed_lot": float(acc.get("fixed_lot") or 0.01),
            "auto_trade": bool(acc.get("auto_trade") or 0),
            "partial_tp_be": True if ptb is None else bool(ptb)}


def set_balance(user_id: int, amount: float) -> dict:
    """User/admin sets the demo starting balance (resets open positions).

    v6.8.0 — 100% auto-execution protocol: linking a demo account with a real
    balance is an explicit intent to TRADE it. The account is therefore armed
    for automatic execution immediately (auto_trade=1), and the lot stays in
    risk-based auto mode unless the user explicitly chose a fixed one. No
    third configuration step is required — balance in, robot on.
    """
    acc = get_or_create_account(user_id)
    with db.tx() as c:
        c.execute("DELETE FROM demo_positions WHERE account_id=?", (acc["id"],))
        c.execute("UPDATE demo_accounts SET balance=?, peak_equity=? WHERE id=?",
                  (amount, amount, acc["id"]))
        # arm the robot (never downgrade an explicit earlier opt-in)
        c.execute("UPDATE demo_accounts SET auto_trade=1 WHERE id=? AND auto_trade=0", (acc["id"],))
    return {"balance": amount}


def set_risk_pct(user_id: int, pct: float) -> None:
    """Set risk % — and arm auto-execution (same 100%-auto protocol)."""
    acc = get_or_create_account(user_id)
    pct = max(0.1, min(5.0, pct))
    with db.conn() as c:
        c.execute("UPDATE demo_accounts SET risk_pct=? WHERE id=?", (pct, acc["id"]))
        c.execute("UPDATE demo_accounts SET auto_trade=1 WHERE id=? AND auto_trade=0", (acc["id"],))


def set_lot_mode(user_id: int, mode: str, fixed_lot: float | None = None) -> None:
    acc = get_or_create_account(user_id)
    mode = "fixed" if mode == "fixed" else "auto"
    lot = max(0.01, min(10.0, fixed_lot)) if fixed_lot else max(0.01, float(acc.get("fixed_lot") or 0.01))
    with db.conn() as c:
        c.execute("UPDATE demo_accounts SET lot_mode=?, fixed_lot=? WHERE id=?", (mode, lot, acc["id"]))


def set_auto_trade(user_id: int, enabled: bool) -> None:
    acc = get_or_create_account(user_id)
    with db.conn() as c:
        c.execute("UPDATE demo_accounts SET auto_trade=? WHERE id=?", (1 if enabled else 0, acc["id"]))


def set_partial_tp_be(user_id: int, enabled: bool) -> None:
    """Toggle Smart 50% Partial TP1 + Automatic Breakeven protection."""
    acc = get_or_create_account(user_id)
    with db.conn() as c:
        c.execute("UPDATE demo_accounts SET partial_tp_be=? WHERE id=?", (1 if enabled else 0, acc["id"]))


def auto_copy_signal_for_subscribers(sig: dict, api_key: str = "") -> list[dict]:
    """v7.6.0 Smart Demo Auto-Copy: open a demo position for every non-blocked user
    who enabled `auto_trade = 1` and doesn't already hold this signal."""
    if not sig or not sig.get("id"):
        return []
    from fysb.engines.accounting import is_private, is_closed
    if is_private(sig) or is_closed(sig) or sig.get("status") != "RUNNING" or db.kill_switch_on():
        return []
    sid = int(sig["id"])
    opened: list[dict] = []
    with db.conn() as c:
        rows = c.execute(
            """SELECT da.user_id, da.id AS account_id
               FROM demo_accounts da
               LEFT JOIN users u ON u.user_id = da.user_id
               WHERE da.auto_trade = 1
                 AND COALESCE(u.is_blocked, 0) = 0""",
        ).fetchall()
    for r in rows:
        uid = int(r["user_id"])
        with db.conn() as c:
            delivered = c.execute("SELECT 1 FROM signal_deliveries WHERE signal_id=? AND user_id=?", (sid, uid)).fetchone()
        if not delivered:
            continue
        try:
            with db.conn() as c:
                dup = c.execute(
                    "SELECT id FROM demo_positions WHERE user_id=? AND signal_id=? AND status='OPEN'",
                    (uid, sid),
                ).fetchone()
            if dup:
                continue
            res = open_signal_interactive(uid, sid, api_key=api_key)
            if res.get("ok") and not res.get("already_open"):
                pos_d = res.get("position") or {}
                opened.append({
                    "user_id": uid,
                    "position_id": int(res.get("position_id") or pos_d.get("id") or 0),
                    "lots": float(pos_d.get("lots") or 0.01),
                    "symbol": str(sig.get("symbol") or ""),
                    "direction": str(sig.get("direction") or ""),
                    "signal_id": sid,
                })
        except Exception as exc:
            log.warning("smart demo auto-copy skipped uid=%s sig=%s err=%s", uid, sid, exc)
    return opened


def share_card_payload_for_position(user_id: int, position_id: int, bot_username: str = "") -> dict | None:
    """Build a render_pnl_share_card payload for any Demo position (open or closed)."""
    with db.conn() as c:
        pos = c.execute(
            "SELECT p.*, a.balance AS acc_bal FROM demo_positions p "
            "JOIN demo_accounts a ON a.id=p.account_id "
            "WHERE p.id=? AND p.user_id=?",
            (int(position_id), int(user_id)),
        ).fetchone()
        if not pos:
            return None
        pos = dict(pos)
        row = c.execute("SELECT * FROM market_catalog WHERE symbol=?", (pos["symbol"],)).fetchone()
        sig = c.execute("SELECT * FROM signals WHERE id=?", (int(pos.get("signal_id") or 0),)).fetchone() if pos.get("signal_id") else None
    entry = float(pos.get("entry") or 0.0)
    exit_px = float(pos.get("exit_price") or 0.0)
    if exit_px <= 0:
        exit_px = market_data.cached_price(pos["symbol"], max_age_seconds=300.0) or entry
    pnl_usd = float(pos.get("pnl") or 0.0)
    if pos.get("status") == "OPEN" and exit_px > 0:
        pnl_usd = instruments.pnl(pos["symbol"], pos["direction"], float(pos.get("lots") or 0.01), entry, exit_px, dict(row) if row else None)
    if exit_px > 0:
        raw_pips = instruments.pips(pos["symbol"], entry, exit_px, dict(row) if row else None)
        is_gain = (exit_px >= entry) if pos["direction"] == "BUY" else (exit_px <= entry)
        pips = raw_pips if is_gain else -raw_pips
    else:
        pips = 0.0
    bal = max(1.0, float(pos.get("acc_bal") or DEMO_DEFAULT_BALANCE))
    pnl_pct = (pnl_usd / bal) * 100.0
    bn = (bot_username or config.BRAND or "ForexYemeniBot").lstrip("@")
    return {
        "kind": "demo",
        "symbol": pos["symbol"],
        "direction": pos["direction"],
        "strategy": (dict(sig).get("strategy") if sig else "DEMO PRO"),
        "timeframe": (dict(sig).get("timeframe") if sig else "LIVE"),
        "entry": entry,
        "exit_price": exit_px,
        "tp1": float(pos.get("tp") or 0.0),
        "sl": float(pos.get("sl") or 0.0),
        "tp_label": str(pos.get("status") or "OPEN").upper(),
        "pips": round(pips, 1),
        "pnl_usd": round(pnl_usd, 2),
        "pnl_pct": round(pnl_pct, 2),
        "ref_link": f"t.me/{bn}?start=ref_{user_id}",
        "ref_label": f"DEMO TRADER #{user_id} • FOREXYEMENI ROYAL",
        "timestamp": str(pos.get("closed_at") or pos.get("opened_at") or config.now()),
    }


def reset_account(user_id: int) -> dict:
    bal = db.get_setting_float("demo_start_balance", DEMO_DEFAULT_BALANCE)
    return set_balance(user_id, bal)


def compute_lots(user_id: int, symbol: str, entry: float, sl: float, row=None) -> tuple[float, float, float]:
    """(lots, risk_money, risk_pct) according to the user's demo settings.

    auto mode  -> risk-based sizing: risk% of balance between entry and SL.
    fixed mode -> the user's fixed lot; risk_money is the computed $ risk.
    """
    acc = get_or_create_account(user_id)
    risk_pct = float(acc.get("risk_pct") or 1.0)
    balance = max(1.0, float(acc["balance"] or DEMO_DEFAULT_BALANCE))
    if (acc.get("lot_mode") or "auto") == "fixed":
        lots = float(acc.get("fixed_lot") or 0.01)
        risk_money = abs(instruments.pnl(symbol, "BUY", lots, entry, sl, row)) if sl else 0.0
        return round(lots, 2), risk_money, risk_pct
    lots = instruments.position_size(balance, risk_pct, entry, sl, symbol, row)
    lots = max(0.01, min(lots, 10.0))
    risk_money = balance * risk_pct / 100.0
    return round(lots, 2), risk_money, risk_pct


def _spread_slippage(symbol: str, direction: str, row=None) -> float:
    """Price adjustment applied against the trader (spread + slippage)."""
    s = instruments.spec(symbol, row)
    slippage_points = db.get_setting_float("demo_slippage_points", SLIPPAGE_POINTS_DEFAULT)
    return s.spread_pips * s.pip_size + slippage_points * s.point_size


def open_position(user_id: int, symbol: str, direction: str, lots: float,
                  sl: float = 0.0, tp: float = 0.0, api_key: str = "",
                  signal_id: int = 0) -> dict:
    """Open a simulated position using the CURRENT real price + costs."""
    acc = get_or_create_account(user_id)
    symbol = instruments.normalize(symbol)
    direction = direction.upper()
    if direction not in ("BUY", "SELL"):
        return {"ok": False, "error": "invalid direction"}
    if lots <= 0 or lots > 10:
        return {"ok": False, "error": "lot size out of allowed range (0.01-10)"}
    if int(signal_id or 0) > 0:
        with db.conn() as c:
            dup = c.execute(
                "SELECT id FROM demo_positions WHERE account_id=? AND signal_id=?",
                (acc["id"], int(signal_id)),
            ).fetchone()
            if dup:
                return {"ok": False, "error": "position already opened for this signal"}
    with db.conn() as c:
        row = c.execute("SELECT * FROM market_catalog WHERE symbol=?", (symbol,)).fetchone()
        if not row or not (row["enabled"] and row["verified"]):
            return {"ok": False, "error": "instrument not enabled/verified with the provider"}
    try:
        quote = market_data.fetch_price(row["provider_symbol"], api_key)
    except RuntimeError as e:
        return {"ok": False, "error": f"market data unavailable: {e}"}
    price = quote["price"]
    cost = _spread_slippage(symbol, direction, row)
    entry = price + cost if direction == "BUY" else price - cost
    if sl:
        if direction == "BUY" and sl >= entry:
            return {"ok": False, "error": "SL must be below entry for BUY"}
        if direction == "SELL" and sl <= entry:
            return {"ok": False, "error": "SL must be above entry for SELL"}
    if tp:
        if direction == "BUY" and tp <= entry:
            return {"ok": False, "error": "TP must be above entry for BUY"}
        if direction == "SELL" and tp >= entry:
            return {"ok": False, "error": "TP must be below entry for SELL"}
    from fysb.engines import risk
    bal = max(1.0, float(acc["balance"] or DEMO_DEFAULT_BALANCE))
    if sl:
        est_loss = abs(instruments.pnl(symbol, direction, lots, entry, sl, row))
        actual_risk_pct = max(0.01, (est_loss / bal) * 100.0)
    else:
        actual_risk_pct = 0.5
    gate = risk.check_demo_trade(user_id, actual_risk_pct)
    if not gate.allowed:
        return {"ok": False, "error": gate.reason}
    notional = instruments.notional_usd(symbol, lots, entry, row)
    if notional > bal * 50:
        return {"ok": False, "error": "notional exceeds demo leverage cap"}
    with db.tx() as c:
        cur = c.execute("""INSERT INTO demo_positions(account_id,user_id,symbol,direction,lots,initial_lots,entry,sl,tp,
                           status,signal_id,opened_at)
                           VALUES(?,?,?,?,?,?,?,?,?, 'OPEN', ?, ?)""",
                        (acc["id"], user_id, symbol, direction, lots, lots, entry, sl, tp, signal_id, config.now()))
        pid = cur.lastrowid
    log.info("DEMO OPEN | user=%s | %s %s %s lots @ %s | signal=%s", user_id, symbol, direction, lots, entry, signal_id)
    return {"ok": True, "position_id": pid, "entry": entry, "spread_cost": cost,
            "price_time": quote["fetched_at"], "lots": lots}


def open_from_signal(user_id: int, sig: dict, api_key: str = "") -> dict:
    """Auto-execute a delivered signal on the user's demo account with HIS
    risk/lot settings. Returns an execution summary for the luxury card."""
    symbol = instruments.normalize(sig["symbol"])
    row = None
    with db.conn() as c:
        r = c.execute("SELECT * FROM market_catalog WHERE symbol=?", (symbol,)).fetchone()
        if r:
            row = dict(r)
    entry_ref = float(sig["entry"])
    sl = float(sig["stop"] or 0)
    lots, risk_money, risk_pct = compute_lots(user_id, symbol, entry_ref, sl, row)
    res = open_position(user_id, symbol, sig["direction"], lots,
                        sl=sl, tp=float(sig["tp3"] or sig["tp1"] or 0), api_key=api_key, signal_id=int(sig["id"]))
    if not res.get("ok"):
        return res
    plan = {}
    for i, tp in enumerate((sig["tp1"], sig["tp2"], sig["tp3"]), start=1):
        if tp:
            plan[f"tp{i}"] = abs(instruments.pnl(symbol, sig["direction"], lots, entry_ref, float(tp), row))
        else:
            plan[f"tp{i}"] = 0.0
    res.update({"lots": lots, "risk_money": risk_money, "risk_pct": risk_pct,
                "plan": plan, "symbol": symbol, "direction": sig["direction"],
                "entry_ref": entry_ref, "tp1": sig["tp1"], "tp2": sig["tp2"], "tp3": sig["tp3"]})
    return res


def open_signal_interactive(user_id: int, signal_id: int, api_key: str = "",
                            lots_override: float | None = None) -> dict:
    """v7.5.0: Idempotent one-tap 'تجريبي الآن' activator shared by Telegram Bot & Mini App.

    * If the user already has an OPEN demo position for `signal_id`, returns it cleanly
      (`already_open=True`) so opening the chart focuses the live trade without duplicating.
    * Otherwise opens a real demo position on the user's unified demo account (using real
      live price when available, falling back to signal entry price if provider is briefly offline).
    """
    sid = int(signal_id or 0)
    if sid <= 0:
        return {"ok": False, "error": "معرف الإشارة غير صالح"}
    acc = get_or_create_account(user_id)
    with db.conn() as c:
        sig_row = c.execute("SELECT * FROM signals WHERE id=?", (sid,)).fetchone()
        if not sig_row:
            return {"ok": False, "error": "الإشارة غير موجودة"}
        sig = dict(sig_row)
        from fysb.engines.accounting import is_private
        if is_private(sig) and int(sig.get("private_user_id") or sig.get("created_by") or 0) != int(user_id):
            return {"ok": False, "error": "هذه إشارة خاصة بمستخدم آخر"}
        existing = c.execute(
            "SELECT * FROM demo_positions WHERE account_id=? AND signal_id=? AND status='OPEN' ORDER BY id DESC LIMIT 1",
            (acc["id"], sid),
        ).fetchone()
        if existing:
            return {
                "ok": True,
                "already_open": True,
                "position": dict(existing),
                "position_id": int(existing["id"]),
                "signal": sig,
            }
        mrow = c.execute("SELECT * FROM market_catalog WHERE symbol=?", (instruments.normalize(sig["symbol"]),)).fetchone()
        row = dict(mrow) if mrow else None

    symbol = instruments.normalize(sig["symbol"])
    direction = str(sig["direction"] or "BUY").upper()
    entry_ref = float(sig["entry"])
    sl = float(sig.get("stop") or 0.0)
    tp = float(sig.get("tp3") or sig.get("tp1") or 0.0)
    if lots_override and float(lots_override) > 0:
        lots = max(0.01, min(10.0, round(float(lots_override), 2)))
    else:
        lots, _, _ = compute_lots(user_id, symbol, entry_ref, sl, row)

    # Try real live market execution first
    res = open_position(user_id, symbol, direction, lots, sl=sl, tp=tp, api_key=api_key, signal_id=sid)
    if not res.get("ok"):
        # v8.0.0 (EXEC-5): NEVER bypass SL/TP directional geometry or risk/leverage caps!
        # Only fall back to entry_ref when the live price feed is briefly offline OR when
        # re-opening a signal whose previous demo trade already closed — while STILL enforcing
        # strict SL/TP side validation and demo risk/leverage limits!
        err_low = str(res.get("error") or "").lower()
        if any(k in err_low for k in ("sl must", "tp must")):
            return {
                "ok": False,
                "error": "⚠️ السعر الحي تجاوز بالفعل وقف الخسارة أو الهدف لهذه الإشارة — لا يمكن فتحها بوقف/هدف معكوس.",
            }
        if any(k in err_low for k in ("already opened", "unavailable", "not enabled")):
            fill_px = entry_ref
            try:
                prov = (row or {}).get("provider_symbol") or symbol
                q = market_data.fetch_price(prov, api_key)
                if q and float(q.get("price") or 0) > 0:
                    fill_px = float(q["price"])
            except Exception:
                pass
            # Enforce strict SL/TP geometry on fill_px
            if sl > 0:
                if (direction == "BUY" and sl >= fill_px) or (direction == "SELL" and sl <= fill_px):
                    return {
                        "ok": False,
                        "error": "⚠️ السعر الحي تجاوز منطقة وقف الخسارة لهذه الإشارة — تم منع فتح صفقة مكسورة.",
                    }
            if tp > 0:
                if (direction == "BUY" and tp <= fill_px) or (direction == "SELL" and tp >= fill_px):
                    return {
                        "ok": False,
                        "error": "⚠️ السعر الحي وصل بالفعل لمنطقة الهدف — فات وقت الدخول التجريبي.",
                    }
            from fysb.engines import risk as _risk
            bal = max(1.0, float(acc["balance"] or DEMO_DEFAULT_BALANCE))
            est_loss = abs(instruments.pnl(symbol, direction, lots, fill_px, sl, row)) if sl > 0 else 0.0
            actual_risk_pct = max(0.01, (est_loss / bal) * 100.0) if est_loss > 0 else 0.5
            gate = _risk.check_demo_trade(user_id, actual_risk_pct)
            if not gate.allowed:
                return {"ok": False, "error": gate.reason}
            notional = instruments.notional_usd(symbol, lots, fill_px, row)
            if notional > bal * 50:
                return {"ok": False, "error": "حجم الصفقة يتجاوز سقف الرافعة التجريبية"}
            with db.tx() as c:
                cur = c.execute(
                    """INSERT INTO demo_positions(account_id,user_id,symbol,direction,lots,initial_lots,entry,sl,tp,
                       status,signal_id,opened_at)
                       VALUES(?,?,?,?,?,?,?,?,?, 'OPEN', ?, ?)""",
                    (acc["id"], user_id, symbol, direction, lots, lots, fill_px, sl, tp, sid, config.now()),
                )
                pid = int(cur.lastrowid)
            res = {"ok": True, "position_id": pid, "entry": fill_px, "lots": lots}
        else:
            return res

    with db.conn() as c:
        pos = c.execute("SELECT * FROM demo_positions WHERE id=?", (int(res["position_id"]),)).fetchone()
    return {
        "ok": True,
        "already_open": False,
        "position": dict(pos) if pos else {"id": res["position_id"], "symbol": symbol, "direction": direction, "entry": res.get("entry", entry_ref), "lots": lots, "sl": sl, "tp": tp, "signal_id": sid, "status": "OPEN"},
        "position_id": int(res["position_id"]),
        "signal": sig,
    }


def _partial_close(user_id: int, position_id: int, fraction: float, exit_price: float,
                   reason: str) -> dict:
    """Close a fraction of an OPEN position at the given price, keep the rest
    running, move the stop to breakeven (documented secure-profit rule)."""
    with db.conn() as c:
        pos = c.execute("SELECT * FROM demo_positions WHERE id=? AND user_id=? AND status='OPEN'",
                        (position_id, user_id)).fetchone()
        row = c.execute("SELECT * FROM market_catalog WHERE symbol=?", (pos["symbol"],)).fetchone() if pos else None
    if not pos:
        return {"ok": False, "error": "position not found"}
    pos = dict(pos)
    close_lots = round(float(pos["lots"]) * fraction, 2)
    if close_lots < 0.01 or pos["lots"] - close_lots < 0.01:
        # too small to split — close the whole position instead
        return close_at_price(user_id, position_id, exit_price, reason)
    pnl = instruments.pnl(pos["symbol"], pos["direction"], close_lots, pos["entry"], exit_price, row)
    with db.tx(immediate=True) as c:
        again = c.execute("SELECT status, lots FROM demo_positions WHERE id=?", (position_id,)).fetchone()
        if not again or again["status"] != "OPEN":
            return {"ok": False, "error": "position already closed"}
        new_sl = pos["entry"] if not pos["sl"] else (
            max(pos["entry"], pos["sl"]) if pos["direction"] == "BUY" else min(pos["entry"], pos["sl"]))
        c.execute("""UPDATE demo_positions SET lots=lots-?, partials_done=partials_done+1,
                     sl=? WHERE id=?""", (close_lots, new_sl, position_id))
        c.execute("UPDATE demo_accounts SET balance=balance+? WHERE id=?", (pnl, pos["account_id"]))
    log.info("DEMO PARTIAL | user=%s pos=%s closed=%s lots pnl=%.2f", user_id, position_id, close_lots, pnl)
    return {"ok": True, "closed_lots": close_lots, "pnl": pnl, "remaining_lots": round(pos["lots"] - close_lots, 2),
            "new_sl": new_sl}


def close_at_price(user_id: int, position_id: int, exit_price: float, reason: str) -> dict:
    with db.conn() as c:
        pos = c.execute("SELECT * FROM demo_positions WHERE id=? AND user_id=? AND status='OPEN'",
                        (position_id, user_id)).fetchone()
        row = c.execute("SELECT * FROM market_catalog WHERE symbol=?", (pos["symbol"],)).fetchone() if pos else None
    if not pos:
        return {"ok": False, "error": "position not found or already closed"}
    pos = dict(pos)
    pnl = instruments.pnl(pos["symbol"], pos["direction"], pos["lots"], pos["entry"], exit_price, row)
    with db.tx(immediate=True) as c:
        again = c.execute("SELECT status FROM demo_positions WHERE id=?", (position_id,)).fetchone()
        if not again or again["status"] != "OPEN":
            return {"ok": False, "error": "position already closed (concurrent close blocked)"}
        c.execute("UPDATE demo_positions SET status=?, exit_price=?, pnl=?, closed_at=?, reason=? WHERE id=?",
                  ("WIN" if pnl >= 0 else "LOSS", exit_price, pnl, config.now(), reason, position_id))
        c.execute("UPDATE demo_accounts SET balance=balance+? WHERE id=?", (pnl, pos["account_id"]))
    return {"ok": True, "pnl": pnl, "exit": exit_price, "closed_lots": pos["lots"]}


def close_position(user_id: int, position_id: int, api_key: str = "") -> dict:
    """Manual full close at the current real price; P&L from actual movement."""
    with db.conn() as c:
        pos = c.execute("SELECT * FROM demo_positions WHERE id=? AND user_id=? AND status='OPEN'",
                        (position_id, user_id)).fetchone()
    if not pos:
        return {"ok": False, "error": "position not found or already closed"}
    with db.conn() as c:
        row = c.execute("SELECT * FROM market_catalog WHERE symbol=?", (pos["symbol"],)).fetchone()
    try:
        quote = market_data.fetch_price(row["provider_symbol"], api_key)
    except RuntimeError as e:
        return {"ok": False, "error": f"market data unavailable: {e}"}
    price = quote["price"]
    cost = _spread_slippage(pos["symbol"], "SELL" if pos["direction"] == "BUY" else "BUY", row)
    exit_price = price - cost if pos["direction"] == "BUY" else price + cost
    res = close_at_price(user_id, position_id, exit_price, "MANUAL_CLOSE")
    if res.get("ok"):
        log.info("DEMO CLOSE | user=%s | pos=%s | pnl=%.2f", user_id, position_id, res["pnl"])
    return res


def sync_positions_with_real_prices(user_id: int, api_key: str = "") -> list[dict]:
    """One maintenance pass over the user's OPEN demo positions:

    1. SL / final TP touches  -> close (conservative SL-first rule on gaps).
    2. Half-TP1 progress      -> one-time secure-profit event: partial close 50%
       of AUTO-TRADED signal positions, stop moved to breakeven.
    Returns a list of EVENTS for luxury notifications:
      {"kind": "SL"|"TP"|"HALF_TP1"|"PARTIAL_DONE", ...}
    """
    events = []
    with db.conn() as c:
        acc = c.execute("SELECT * FROM demo_accounts WHERE user_id=?", (user_id,)).fetchone()
        if not acc:
            return events
        acc = dict(acc)
        positions = [dict(r) for r in c.execute(
            "SELECT * FROM demo_positions WHERE account_id=? AND status='OPEN' ORDER BY id", (acc["id"],)).fetchall()]
        if not positions:
            return events
        syms = sorted({p["symbol"] for p in positions})
        ph_s = ",".join("?" * len(syms))
        catalog_map = {
            r["symbol"]: dict(r)
            for r in c.execute(f"SELECT * FROM market_catalog WHERE symbol IN ({ph_s})", tuple(syms)).fetchall()
        }
        sig_ids = sorted({int(p["signal_id"]) for p in positions if int(p.get("signal_id") or 0) > 0})
        tp1_map: dict[int, float] = {}
        if sig_ids:
            ph_i = ",".join("?" * len(sig_ids))
            for r in c.execute(f"SELECT id, tp1 FROM signals WHERE id IN ({ph_i})", tuple(sig_ids)).fetchall():
                if r["tp1"]:
                    try:
                        tp1_map[int(r["id"])] = float(r["tp1"])
                    except (TypeError, ValueError):
                        pass
    for pos in positions:
        symbol = pos["symbol"]
        row = catalog_map.get(symbol)
        if not row:
            continue
        try:
            quote = market_data.fetch_price(row["provider_symbol"], api_key)
            price = quote["price"]
        except RuntimeError:
            try:
                payload = market_data.fetch_candles(row["provider_symbol"], "5min", api_key, outputsize=30)
                price = payload["values"][-1]["close"]
            except RuntimeError:
                continue
        d = pos["direction"]
        sl, tp = pos["sl"] or 0, pos["tp"] or 0

        # 1) stop / target checks (touch model)
        sl_hit = (d == "BUY" and sl and price <= sl) or (d == "SELL" and sl and price >= sl)
        tp_hit = (d == "BUY" and tp and price >= tp) or (d == "SELL" and tp and price <= tp)
        if sl_hit:
            cost = _spread_slippage(symbol, "SELL" if d == "BUY" else "BUY", row)
            exit_price = (sl - cost) if d == "BUY" else (sl + cost)
            res = close_at_price(user_id, pos["id"], exit_price, "AUTO_SL")
            if res.get("ok"):
                events.append({"kind": "SL", "position_id": pos["id"], "pnl": res["pnl"],
                               "exit": exit_price, "symbol": symbol})
            continue
        if tp_hit:
            cost = _spread_slippage(symbol, "SELL" if d == "BUY" else "BUY", row)
            exit_price = (tp - cost) if d == "BUY" else (tp + cost)
            res = close_at_price(user_id, pos["id"], exit_price, "AUTO_TP")
            if res.get("ok"):
                events.append({"kind": "TP", "position_id": pos["id"], "pnl": res["pnl"],
                               "exit": exit_price, "symbol": symbol})
            continue

        # 2) Smart 50% Partial TP1 + Breakeven protection (controlled by partial_tp_be, default ON)
        ptb_val = acc.get("partial_tp_be")
        partial_be_on = True if ptb_val is None else bool(ptb_val)
        if partial_be_on and pos["signal_id"] and not pos["partials_done"] and tp:
            # Resolve the signal's true TP1 (preloaded in batch) so HALF_TP1
            # triggers at 50% of entry->TP1, falling back to pos.tp.
            tp1_target = tp
            cand_tp1 = tp1_map.get(int(pos["signal_id"]))
            if cand_tp1 and ((d == "BUY" and cand_tp1 > pos["entry"]) or (d == "SELL" and cand_tp1 < pos["entry"])):
                tp1_target = cand_tp1
            progress = 0.0
            if d == "BUY" and price > pos["entry"]:
                progress = (price - pos["entry"]) / max(1e-12, (tp1_target - pos["entry"]))
            elif d == "SELL" and price < pos["entry"]:
                progress = (pos["entry"] - price) / max(1e-12, (pos["entry"] - tp1_target))
            if progress >= HALF_TP1_TRIGGER:
                res = _partial_close(user_id, pos["id"], PARTIAL_FRACTION, price, "HALF_TP1_SECURE")
                if res.get("ok") and res.get("closed_lots"):
                    events.append({"kind": "HALF_TP1", "position_id": pos["id"],
                                   "closed_lots": res["closed_lots"], "pnl": res["pnl"],
                                   "remaining_lots": res["remaining_lots"], "new_sl": res["new_sl"],
                                   "price": price, "symbol": symbol, "direction": d})
                elif res.get("ok"):
                    events.append({"kind": "PARTIAL_DONE", "position_id": pos["id"],
                                   "pnl": res.get("pnl", 0.0), "symbol": symbol})
    return events


def auto_close_on_targets(user_id: int, api_key: str = "") -> list[dict]:
    """Legacy-compatible wrapper returning plain close events."""
    events = sync_positions_with_real_prices(user_id, api_key)
    return [{"position_id": e["position_id"], "kind": e["kind"], "pnl": e["pnl"]}
            for e in events if e["kind"] in ("SL", "TP")]


def account_overview(user_id: int, api_key: str = "") -> dict:
    """Balance / Equity / floating P&L / drawdown — computed from real prices."""
    acc = get_or_create_account(user_id)
    with db.conn() as c:
        open_pos = [dict(r) for r in c.execute(
            "SELECT * FROM demo_positions WHERE account_id=? AND status='OPEN' ORDER BY opened_at DESC LIMIT 20", (acc["id"],)).fetchall()]
        closed_pos = [dict(r) for r in c.execute(
            "SELECT * FROM demo_positions WHERE account_id=? AND status!='OPEN' ORDER BY closed_at DESC LIMIT 10", (acc["id"],)).fetchall()]
        agg = c.execute(
            "SELECT COUNT(*) total, COALESCE(SUM(CASE WHEN pnl>=0 THEN 1 ELSE 0 END),0) wins "
            "FROM demo_positions WHERE account_id=? AND status!='OPEN'", (acc["id"],)).fetchone()
        catalog_map: dict[str, dict] = {}
        if open_pos:
            syms = sorted({p["symbol"] for p in open_pos})
            ph = ",".join("?" * len(syms))
            catalog_map = {
                r["symbol"]: dict(r)
                for r in c.execute(f"SELECT * FROM market_catalog WHERE symbol IN ({ph})", tuple(syms)).fetchall()
            }
    floating = 0.0
    stale = False
    px_by_sym: dict[str, float | None] = {}
    for pos in open_pos:
        sym = pos["symbol"]
        row = catalog_map.get(sym)
        if not row:
            pos["current"] = None
            stale = True
            continue
        if sym not in px_by_sym:
            # Prefer hot in-memory quote cache first; fetch once per unique symbol
            c_px = market_data.cached_price(sym, max_age_seconds=90.0)
            if c_px > 0:
                px_by_sym[sym] = c_px
            else:
                try:
                    quote = market_data.fetch_price(row["provider_symbol"], api_key)
                    px_by_sym[sym] = float(quote["price"])
                except RuntimeError:
                    px_by_sym[sym] = None
        px = px_by_sym[sym]
        if px is None:
            pos["current"] = None
            stale = True
            continue
        floating += instruments.pnl(sym, pos["direction"], pos["lots"], pos["entry"], px, row)
        pos["current"] = px
    equity = acc["balance"] + floating
    peak = max(acc["peak_equity"], equity)
    if equity > acc["peak_equity"]:
        with db.conn() as c:
            c.execute("UPDATE demo_accounts SET peak_equity=? WHERE id=?", (equity, acc["id"]))
    drawdown = (peak - equity) / peak * 100 if peak > 0 else 0.0
    total_closed = int(agg["total"] or 0) if agg else 0
    total_wins = int(agg["wins"] or 0) if agg else 0
    ptb = acc.get("partial_tp_be")
    settings_dict = {
        "balance": float(acc.get("balance") or DEMO_DEFAULT_BALANCE),
        "risk_pct": float(acc.get("risk_pct") or 1.0),
        "lot_mode": (acc.get("lot_mode") or "auto"),
        "fixed_lot": float(acc.get("fixed_lot") or 0.01),
        "auto_trade": bool(acc.get("auto_trade") or 0),
        "partial_tp_be": True if ptb is None else bool(ptb),
    }
    return {"balance": acc["balance"], "equity": equity, "floating": floating,
            "drawdown_pct": drawdown, "open": open_pos, "closed": closed_pos,
            "win_rate": (total_wins / total_closed * 100) if total_closed else None,
            "data_stale": stale, "currency": acc.get("currency", "USD"),
            "settings": settings_dict}
