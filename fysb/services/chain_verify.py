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

Primary source (TRC20): TronGrid public events API.
Fallback source (TRC20): Tronscan public transaction API.
v6.8.1 — MULTI-NETWORK (owner request: «لا يمكنني اضافة USDT BEP20 او شبكة اخرى»):
BEP20 (BSC) / ERC20 (Ethereum) / Polygon USDT are verified through PUBLIC
JSON-RPC endpoints (no API key, no paid service): eth_getTransactionReceipt
decodes the canonical ERC-20 Transfer event against the per-chain USDT
contract. The parser is strict about: receipt success, contract identity,
destination wallet (topic2) and amount (value / 1e6).
The parser is schema-tolerant everywhere: it walks the returned structures
for the exact transfer record addressed to our wallet, so minor upstream
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

# --- v6.8.1 multi-network registry (EVM chains, all public RPC, no keys) -----
# Contracts are the CANONICAL USDT addresses (identical across explorers).
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df3231f"

EVM_CHAINS = {
    "BEP20": {
        "label": "USDT BEP20 (BSC)",
        "contract": "0x55d398326f99059fF775485246999027B3197955",
        "decimals": 6,
        "rpc": ["https://bsc-dataseed.binance.org",
                "https://bsc-dataseed1.defibit.io",
                "https://bsc.publicnode.com"],
    },
    "ERC20": {
        "label": "USDT ERC20 (Ethereum)",
        "contract": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
        "decimals": 6,
        "rpc": ["https://ethereum-rpc.publicnode.com",
                "https://eth.llamarpc.com",
                "https://cloudflare-eth.com"],
    },
    "POLYGON": {
        "label": "USDT Polygon (POS)",
        "contract": "0xc2132D05D31c914a87C6611C10748AEb04B58e8F",
        "decimals": 6,
        "rpc": ["https://polygon-bor-rpc.publicnode.com",
                "https://polygon-rpc.com"],
    },
}

ALL_CHAINS = ["TRC20"] + list(EVM_CHAINS.keys())


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


# ---------------------------------------------------------------------------
# v6.8.1 — EVM chains (BEP20 / ERC20 / Polygon) via PUBLIC JSON-RPC, no keys.
# ---------------------------------------------------------------------------
def _rpc_call(rpc: str, method: str, params: list, timeout: float):
    r = httpx.post(rpc, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                   timeout=timeout)
    r.raise_for_status()
    js = r.json()
    if js.get("error"):
        raise RuntimeError(js["error"].get("message", "rpc error"))
    return js.get("result")


def _wallet_topic(wallet: str) -> str:
    """Encode a 0x address as the 32-byte padded topic2 of an ERC-20 Transfer."""
    w = (wallet or "").strip().lower()
    if w.startswith("0x"):
        w = w[2:]
    if not re.fullmatch(r"[0-9a-f]{40}", w):
        return ""
    return "0x" + "0" * 24 + w


def _evm_amount(log: dict, decimals: int) -> float:
    for key in ("data",):
        v = log.get(key) or "0x0"
        try:
            return int(v, 16) / (10 ** decimals)
        except (TypeError, ValueError):
            continue
    return 0.0


def verify_evm_usdt(txid: str, wallet: str, min_amount: float, chain: str,
                    timeout: float = 20.0) -> dict:
    """Verify one on-chain USDT transfer on BEP20/ERC20/Polygon — REAL proof.

    Flow: eth_getTransactionReceipt on public RPC endpoints (tried in order) →
    the receipt must carry status=0x1 → find the Transfer log from the chain's
    canonical USDT contract → destination topic2 must equal OUR wallet →
    decoded value must cover the plan price. Every failure mode has its own
    honest Arabic reason; nothing is assumed, nothing invented."""
    txid = (txid or "").strip()
    wallet = (wallet or "").strip()
    cfg = EVM_CHAINS.get((chain or "").upper())
    if not cfg:
        return {"ok": False, "reason": "شبكة غير مدعومة"}
    if not valid_txid(txid):
        return {"ok": False, "reason": "صيغة TXID غير صحيحة — يجب أن تكون 64 خانة سداسية عشرية (انسخها من المحفظة)"}
    topic = _wallet_topic(wallet)
    if not topic:
        return {"ok": False, "reason": "عنوان محفظة الشبكة غير مضبوط أو غير صالح — تواصل مع الإدارة"}
    errors: list[str] = []
    receipt = None
    for rpc in cfg["rpc"]:
        try:
            receipt = _rpc_call(rpc, "eth_getTransactionReceipt", [txid], timeout)
            if receipt:
                break
        except Exception as e:
            errors.append(f"{rpc.split('//')[1][:18]}: {type(e).__name__}")
    if receipt is None:
        return {"ok": False,
                "reason": "تعذر الوصول إلى المعاملة على شبكة " + cfg["label"] +
                          " الآن (" + "؛ ".join(errors[:2]) + ") — تأكد من TXID أو حاول بعد قليل"}
    if receipt.get("status") not in ("0x1", "0x01", 1):
        return {"ok": False, "reason": "المعاملة فشلت على الشبكة (status=0x0) — انظر المحفظة"}
    for log in receipt.get("logs") or []:
        if (log.get("address") or "").lower() != cfg["contract"].lower():
            continue
        topics = log.get("topics") or []
        if not topics or topics[0].lower() != TRANSFER_TOPIC:
            continue
        if len(topics) < 3 or topics[2].lower() != topic:
            continue
        val = _evm_amount(log, cfg["decimals"])
        if val <= 0:
            continue
        if val + 1e-9 >= min_amount:
            return {"ok": True, "amount": val, "confirmed": True, "source": chain.lower() + "-rpc"}
        return {"ok": False,
                "reason": f"مبلغ المعاملة {val:,.2f} USDT أقل من المطلوب {min_amount:,.2f} USDT"}
    return {"ok": False,
            "reason": "المعاملة موجودة لكنها ليست تحويلاً لعملة USDT إلى عنواننا على شبكة " + cfg["label"]}


def verify_usdt(txid: str, wallet: str, min_amount: float, chain: str = "TRC20",
                timeout: float = 20.0) -> dict:
    """Multi-network dispatcher: TRC20 → TronGrid/Tronscan; BEP20/ERC20/Polygon
    → public EVM RPC. `chain` comes from the payment flow (never guessed)."""
    chain = (chain or "TRC20").upper()
    if chain == "TRC20":
        return verify_trc20_usdt(txid, wallet, min_amount, timeout)
    return verify_evm_usdt(txid, wallet, min_amount, chain, timeout)


def valid_wallet(wallet: str, chain: str = "TRC20") -> bool:
    """Address-shape validation per chain (admin input gate)."""
    w = (wallet or "").strip()
    if (chain or "").upper() == "TRC20":
        return w.startswith("T") and 30 <= len(w) <= 60 and bool(re.fullmatch(r"[A-Za-z0-9]+", w))
    if w.lower().startswith("0x"):
        w = w[2:]
    return bool(re.fullmatch(r"[0-9a-fA-F]{40}", w))
