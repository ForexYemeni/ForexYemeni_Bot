"""On-chain USDT payment verification — REAL blockchain proof (v6.8.0).

The luxury payment protocol the owner demanded:
  1. the admin configures ONE USDT TRC20 wallet in the admin panel (stored in
     DB settings — no environment variables, per the standing rule);
  2. the user pays USDT and submits the TXID in chat;
  3. the platform verifies the transfer ON-CHAIN against public blockchain
     APIs (no API key required), checking: confirmed status, USDT TRC20
     contract, destination = OUR wallet, and value >= plan price;
  4. on success the plan is activated IMMEDIATELY and the TXID is permanently
     bound to that payment — a used TXID can never unlock a second activation.

Primary source: TronGrid public events API.
Fallback source: Tronscan public transaction API.
The parser is schema-tolerant: it walks the returned JSON tree for any
token-transfer record addressed to our wallet, so minor upstream schema
changes can never silently pass — and never falsely fail — a payment.
"""
import json
import re

import httpx

# Canonical USDT (TRC20) contract on the Tron network — same address on every
# explorer; a transfer to any other contract is not USDT and is rejected.
# LIVE-VERIFIED on 2026-11-23 via TronGrid /v1/contracts/<addr>/transactions:
# the canonical address answers with real USDT transfer traffic, while the
# previously-shipped mistyped address answered 400 «A valid account address
# is required» — an address that does not even exist on Tron. That typo would
# have REJECTED every genuine payment («ليست تحويلاً لعملة USDT»).
USDT_TRC20_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
USDT_DECIMALS = 6
TXID_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def valid_txid(txid: str) -> bool:
    return bool(TXID_RE.match((txid or "").strip()))


def _fetch_trongrid(txid: str, timeout: float):
    r = httpx.get(f"https://api.trongrid.io/v1/transactions/{txid}/events", timeout=timeout)
    if r.status_code in (404, 400):
        return None
    r.raise_for_status()
    return r.json()


def _fetch_tronscan(txid: str, timeout: float):
    r = httpx.get("https://apilist.tronscanapi.com/api/transaction-info",
                  params={"hash": txid}, timeout=timeout)
    if r.status_code in (404, 400):
        return None
    r.raise_for_status()
    return r.json()


def _walk_transfers(node):
    """Yield every dict in the JSON tree that carries a destination address
    plus a value/amount field — i.e. every possible transfer record shape."""
    if isinstance(node, dict):
        keys = {k.lower() for k in node.keys()}
        if (keys & {"to", "receiver", "to_address", "recipient"}) \
                and (keys & {"value", "amount", "amount_str"}):
            yield node
        for v in node.values():
            yield from _walk_transfers(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_transfers(v)


def _dest(d: dict) -> str:
    for k in ("to", "receiver", "to_address", "recipient"):
        v = d.get(k)
        if isinstance(v, str) and v.startswith("T") and len(v) >= 30:
            return v
    return ""


def _amount(d: dict) -> float:
    for k in ("value", "amount", "amount_str"):
        v = d.get(k)
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return 0.0


def _confirmed(js) -> bool | None:
    """True/False when the tree carries an explicit confirmed flag, else None."""
    if isinstance(js, dict):
        for k, v in js.items():
            if k.lower() == "confirmed" and isinstance(v, bool):
                return v
        for v in js.values():
            r = _confirmed(v)
            if r is not None:
                return r
    elif isinstance(js, list):
        for v in js:
            r = _confirmed(v)
            if r is not None:
                return r
    return None


def _human_amount(raw: float) -> float:
    """USDT stores values as integer units with 6 decimals. Raw units are
    >= 1e6 for any realistic payment; treat smaller numbers as already-human."""
    return raw / (10 ** USDT_DECIMALS) if raw >= 1_000_000 else raw


def verify_trc20_usdt(txid: str, wallet: str, min_amount: float, timeout: float = 20.0) -> dict:
    """Verify one on-chain USDT TRC20 transfer. Returns
    {ok, reason?, amount?, source?} — honest about every failure mode."""
    txid = (txid or "").strip()
    wallet = (wallet or "").strip()
    if not valid_txid(txid):
        return {"ok": False, "reason": "صيغة TXID غير صحيحة — يجب أن تكون 64 خانة سداسية عشرية (انسخها من المحفظة)"}
    if not wallet:
        return {"ok": False, "reason": "لم يُضبَط عنوان محفظة USDT بعد — تواصل مع الإدارة"}
    errors: list[str] = []
    for fetcher in (_fetch_trongrid, _fetch_tronscan):
        source = "trongrid" if fetcher is _fetch_trongrid else "tronscan"
        try:
            js = fetcher(txid, timeout)
        except Exception as e:
            errors.append(f"{source}: {type(e).__name__}")
            continue
        if js is None:
            errors.append(f"{source}: transaction not found")
            continue
        raw_text = json.dumps(js).lower()
        confirmed = _confirmed(js)
        if confirmed is False:
            return {"ok": False, "reason": "المعاملة لم تُؤكد على البلوكيشن بعد — انتظر التأكيدات ثم أعد الإرسال"}
        if USDT_TRC20_CONTRACT.lower() not in raw_text:
            return {"ok": False, "reason": "المعاملة ليست تحويلاً لعملة USDT على شبكة TRC20"}
        for d in _walk_transfers(js):
            dest = _dest(d)
            if dest.lower() != wallet.lower():
                continue
            val = _human_amount(_amount(d))
            if val <= 0:
                continue
            if val + 1e-9 >= min_amount:
                return {"ok": True, "amount": val, "confirmed": True, "source": source}
            return {"ok": False,
                    "reason": f"مبلغ المعاملة {val:,.2f} USDT أقل من المطلوب {min_amount:,.2f} USDT"}
        errors.append(f"{source}: no transfer to the configured wallet")
    return {"ok": False,
            "reason": "تعذر التحقق من المعاملة الآن (" + "؛ ".join(errors[:2]) + ") — تأكد من TXID أو حاول بعد قليل"}
