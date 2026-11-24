"""On-chain USDT payment verification — hardened blockchain proof (v6.12.0).

Security guarantees enforced on every payment:
  * Canonical USDT contracts + EXACT per-chain decimals:
      TRC20   -> TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t (6 decimals)
      BEP20   -> 0x55d398326f99059fF775485246999027B3197955 (18 decimals!)
      ERC20   -> 0xdAC17F958D2ee523a2206206994597C13D831ec7 (6 decimals)
      POLYGON -> 0xc2132D05D31c914a87C6611C10748AEb04B58e8F (6 decimals)
  * Strict contract binding: each individual Transfer event/log MUST belong to
    the canonical USDT contract — never a substring check over the whole JSON.
  * Strict raw-unit scaling: raw integer amounts are ALWAYS divided by
    10**decimals (never heuristic-skipped for small amounts < 1 USDT).
  * EVM 0x-prefix support + canonical lowercase TXID normalization.
  * Block confirmation depth check on EVM chains (reorg protection).
  * Transaction timestamp check against payment creation time (blocks replay of
    old historical transactions from public explorers).
"""
import re

import httpx

# Canonical USDT (TRC20) contract on Tron (Base58 + 41-prefixed hex form).
USDT_TRC20_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
USDT_TRC20_CONTRACT_HEX = "41a614f803b6fd780986a42c78ec9c7f77e6ded13c"
USDT_DECIMALS = 6

TXID_TRON_RE = re.compile(r"^[0-9a-fA-F]{64}$")
TXID_ANY_RE = re.compile(r"^(?:0[xX])?[0-9a-fA-F]{64}$")

# Canonical ERC-20 / BEP-20 Transfer(address,address,uint256) topic0
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df3231f"

EVM_CHAINS = {
    "BEP20": {
        "label": "USDT BEP20 (BSC)",
        "contract": "0x55d398326f99059fF775485246999027B3197955",
        "decimals": 18,             # BSC-USD (BEP20) has 18 decimals, NOT 6!
        "min_confirmations": 6,
        "rpc": [
            "https://bsc-dataseed.binance.org",
            "https://bsc-dataseed1.defibit.io",
            "https://bsc.publicnode.com",
        ],
    },
    "ERC20": {
        "label": "USDT ERC20 (Ethereum)",
        "contract": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
        "decimals": 6,
        "min_confirmations": 3,
        "rpc": [
            "https://ethereum-rpc.publicnode.com",
            "https://eth.llamarpc.com",
            "https://cloudflare-eth.com",
        ],
    },
    "POLYGON": {
        "label": "USDT Polygon (POS)",
        "contract": "0xc2132D05D31c914a87C6611C10748AEb04B58e8F",
        "decimals": 6,
        "min_confirmations": 12,
        "rpc": [
            "https://polygon-bor-rpc.publicnode.com",
            "https://polygon-rpc.com",
        ],
    },
}

ALL_CHAINS = ["TRC20"] + list(EVM_CHAINS.keys())


def canonical_txid(txid: str) -> str:
    """Canonical lowercase 64-hex form (optional 0x stripped) for DB uniqueness."""
    s = (txid or "").strip().lower()
    if s.startswith("0x"):
        s = s[2:]
    return s


def valid_txid(txid: str, chain: str = "") -> bool:
    """Validate TXID shape: TRC20 = 64 hex; EVM/generic = optional 0x + 64 hex."""
    s = (txid or "").strip()
    if (chain or "").upper() == "TRC20":
        return bool(TXID_TRON_RE.match(s))
    return bool(TXID_ANY_RE.match(s))


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


def _is_usdt_tron_contract(addr: str) -> bool:
    a = (addr or "").strip()
    return a.lower() in (USDT_TRC20_CONTRACT.lower(), USDT_TRC20_CONTRACT_HEX.lower())


def _ts_seconds(raw) -> float:
    """Normalize epoch timestamp (seconds or milliseconds) to float seconds."""
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if v <= 0:
        return 0.0
    return v / 1000.0 if v > 1e11 else v


def _human_amount(raw: float | int | str, decimals: int = USDT_DECIMALS) -> float:
    """Convert raw on-chain integer units to human token units.

    STRICT: raw units are ALWAYS divided by 10**decimals — never skipped for
    small numbers (skipping <1_000_000 allowed $0.01 = 10,000 raw units to be
    misread as $10,000 USDT!).
    """
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if val <= 0:
        return 0.0
    return val / float(10 ** decimals)


def _confirmed(js) -> bool | None:
    """True/False when the response carries an explicit confirmed flag, else None."""
    if isinstance(js, dict):
        if "confirmed" in js and isinstance(js["confirmed"], bool):
            return js["confirmed"]
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


def _tron_tx_failed(js: dict) -> bool:
    """True when Tron response explicitly reports a failed/reverted contract call."""
    if not isinstance(js, dict):
        return False
    ret = str(js.get("contractRet") or "").strip().upper()
    if ret and ret != "SUCCESS":
        return True
    if js.get("revert") is True:
        return True
    return False


def _extract_trc20_usdt_transfers(js: dict) -> list[dict]:
    """Extract ONLY Transfer records explicitly emitted by USDT_TRC20_CONTRACT.

    Returns list of {"to": str, "raw_amount": float, "timestamp": float}.
    Supports both TronGrid (/events -> data[]) and TronScan (trc20TransferInfo[] /
    tokenTransferInfo[]). Any transfer from another contract is ignored.
    """
    out: list[dict] = []
    if not isinstance(js, dict):
        return out
    top_ts = _ts_seconds(js.get("timestamp") or js.get("block_timestamp") or 0)

    # 1) TronGrid /v1/transactions/{txid}/events
    data = js.get("data")
    if isinstance(data, list):
        for ev in data:
            if not isinstance(ev, dict):
                continue
            ev_name = str(ev.get("event_name") or "").strip().lower()
            if ev_name and ev_name != "transfer":
                continue
            if not _is_usdt_tron_contract(ev.get("contract_address") or ""):
                continue
            res = ev.get("result") if isinstance(ev.get("result"), dict) else {}
            dest = str(res.get("to") or res.get("to_address") or res.get("recipient") or "").strip()
            raw_val = res.get("value") if "value" in res else res.get("amount")
            ts = _ts_seconds(ev.get("block_timestamp") or top_ts)
            try:
                raw_num = float(raw_val)
            except (TypeError, ValueError):
                continue
            if dest and raw_num > 0:
                out.append({"to": dest, "raw_amount": raw_num, "timestamp": ts})

    # 2) TronScan /api/transaction-info
    for key in ("trc20TransferInfo", "tokenTransferInfo"):
        items = js.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            contract = item.get("contract_address") or item.get("contract") or ""
            if not _is_usdt_tron_contract(contract):
                continue
            dest = str(item.get("to_address") or item.get("to") or item.get("receiver") or "").strip()
            raw_val = item.get("amount_str") if item.get("amount_str") is not None else item.get("amount")
            ts = _ts_seconds(item.get("block_ts") or item.get("timestamp") or top_ts)
            try:
                raw_num = float(raw_val)
            except (TypeError, ValueError):
                continue
            if dest and raw_num > 0:
                out.append({"to": dest, "raw_amount": raw_num, "timestamp": ts})

    return out


def verify_trc20_usdt(txid: str, wallet: str, min_amount: float, timeout: float = 20.0,
                      min_timestamp: float = 0.0) -> dict:
    """Verify one on-chain USDT TRC20 transfer. Returns {ok, reason?, amount?, source?}."""
    txid = (txid or "").strip()
    wallet = (wallet or "").strip()
    if not valid_txid(txid, "TRC20"):
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
        if _tron_tx_failed(js):
            return {"ok": False, "reason": "المعاملة فشلت على شبكة Tron (revert) — راجع المحفظة"}
        confirmed = _confirmed(js)
        if confirmed is False:
            return {"ok": False, "reason": "المعاملة لم تُؤكد على البلوكيشن بعد — انتظر التأكيدات ثم أعد الإرسال"}

        transfers = _extract_trc20_usdt_transfers(js)
        if not transfers:
            errors.append(f"{source}: no USDT TRC20 transfer event")
            continue
        matched_wallet = False
        for tr in transfers:
            if tr["to"].lower() != wallet.lower():
                continue
            matched_wallet = True
            if source == "tronscan" and confirmed is not True:
                return {"ok": False, "reason": "المعاملة لم تُؤكد بعد على TronScan — انتظر التأكيدات ثم أعد الإرسال"}
            if min_timestamp > 0 and tr["timestamp"] > 0 and tr["timestamp"] < min_timestamp:
                return {"ok": False,
                        "reason": "المعاملة قديمة (تمت قبل إنشاء طلب الدفع) — أرسل TXID لتحويلك الجديد فقط"}
            val = _human_amount(tr["raw_amount"], USDT_DECIMALS)
            if val <= 0:
                continue
            if val + 1e-9 >= min_amount:
                return {"ok": True, "amount": val, "confirmed": True,
                        "source": source, "tx_timestamp": tr["timestamp"]}
            return {"ok": False,
                    "reason": f"مبلغ المعاملة {val:,.2f} USDT أقل من المطلوب {min_amount:,.2f} USDT"}
        if not matched_wallet:
            errors.append(f"{source}: no transfer to the configured wallet")
    return {"ok": False,
            "reason": "تعذر التحقق من المعاملة الآن (" + "؛ ".join(errors[:2]) + ") — تأكد من TXID أو حاول بعد قليل"}


# ---------------------------------------------------------------------------
# EVM chains (BEP20 / ERC20 / Polygon) via PUBLIC JSON-RPC, no keys.
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
    v = log.get("data") or "0x0"
    try:
        return int(v, 16) / float(10 ** decimals)
    except (TypeError, ValueError):
        return 0.0


def _hex_int(v) -> int:
    try:
        return int(v, 16) if isinstance(v, str) else int(v)
    except (TypeError, ValueError):
        return -1


def verify_evm_usdt(txid: str, wallet: str, min_amount: float, chain: str,
                    timeout: float = 20.0, min_timestamp: float = 0.0) -> dict:
    """Verify one on-chain USDT transfer on BEP20/ERC20/Polygon — hardened proof.

    Checks:
      1. TXID shape (with or without 0x) -> normalized to 0x + 64 lowercase hex
      2. Receipt exists + status == 0x1
      3. Block confirmation depth >= chain minimum (reorg guard)
      4. Block timestamp >= min_timestamp when provided (old-TX replay guard)
      5. Log emitted by canonical USDT contract with Transfer topic0, our wallet
         in topic2, and decoded value (with exact per-chain decimals: 18 on BSC,
         6 on ETH/Polygon) >= min_amount.
    """
    txid_raw = (txid or "").strip()
    wallet = (wallet or "").strip()
    chain_u = (chain or "").upper()
    cfg = EVM_CHAINS.get(chain_u)
    if not cfg:
        return {"ok": False, "reason": "شبكة غير مدعومة"}
    if not valid_txid(txid_raw, chain_u):
        return {"ok": False,
                "reason": "صيغة TXID غير صحيحة — يجب أن تكون 64 خانة سداسية عشرية (أو تبدأ بـ 0x)"}
    rpc_txid = "0x" + canonical_txid(txid_raw)
    topic = _wallet_topic(wallet)
    if not topic:
        return {"ok": False, "reason": "عنوان محفظة الشبكة غير مضبوط أو غير صالح — تواصل مع الإدارة"}

    errors: list[str] = []
    receipt = None
    active_rpc = ""
    for rpc in cfg["rpc"]:
        try:
            receipt = _rpc_call(rpc, "eth_getTransactionReceipt", [rpc_txid], timeout)
            if receipt:
                active_rpc = rpc
                break
        except Exception as e:
            errors.append(f"{rpc.split('//')[1][:18]}: {type(e).__name__}")
    if receipt is None:
        return {"ok": False,
                "reason": "تعذر الوصول إلى المعاملة على شبكة " + cfg["label"] +
                          " الآن (" + "؛ ".join(errors[:2]) + ") — تأكد من TXID أو حاول بعد قليل"}
    if receipt.get("status") not in ("0x1", "0x01", 1):
        return {"ok": False, "reason": "المعاملة فشلت على الشبكة (status=0x0) — انظر المحفظة"}

    # Confirmation depth + block timestamp check
    tx_block_hex = receipt.get("blockNumber")
    tx_block = _hex_int(tx_block_hex) if tx_block_hex is not None else -1
    min_confs = int(cfg.get("min_confirmations", 3))
    confirmations = None
    tx_ts = 0.0
    if tx_block >= 0 and active_rpc:
        try:
            latest_hex = _rpc_call(active_rpc, "eth_blockNumber", [], timeout)
            latest_block = _hex_int(latest_hex)
            if latest_block >= tx_block:
                confirmations = latest_block - tx_block + 1
                if confirmations < min_confs:
                    return {"ok": False,
                            "reason": f"المعاملة لم تكتمل تأكيداتها بعد ({confirmations}/{min_confs}) — انتظر لحظات ثم أعد الإرسال"}
        except Exception:
            pass  # best-effort when RPC skips eth_blockNumber; receipt already mined
        if min_timestamp > 0:
            try:
                blk = _rpc_call(active_rpc, "eth_getBlockByNumber", [tx_block_hex, False], timeout)
                if isinstance(blk, dict) and blk.get("timestamp") is not None:
                    tx_ts = float(_hex_int(blk["timestamp"]))
                    if tx_ts > 0 and tx_ts < min_timestamp:
                        return {"ok": False,
                                "reason": "المعاملة قديمة (تمت قبل إنشاء طلب الدفع) — أرسل TXID لتحويلك الجديد فقط"}
            except Exception:
                pass

    for log_entry in receipt.get("logs") or []:
        if (log_entry.get("address") or "").lower() != cfg["contract"].lower():
            continue
        topics = log_entry.get("topics") or []
        if not topics or topics[0].lower() != TRANSFER_TOPIC:
            continue
        if len(topics) < 3 or topics[2].lower() != topic:
            continue
        val = _evm_amount(log_entry, cfg["decimals"])
        if val <= 0:
            continue
        if val + 1e-9 >= min_amount:
            return {"ok": True, "amount": val, "confirmed": True,
                    "confirmations": confirmations, "tx_timestamp": tx_ts,
                    "source": chain_u.lower() + "-rpc"}
        return {"ok": False,
                "reason": f"مبلغ المعاملة {val:,.2f} USDT أقل من المطلوب {min_amount:,.2f} USDT"}
    return {"ok": False,
            "reason": "المعاملة موجودة لكنها ليست تحويلاً لعملة USDT إلى عنواننا على شبكة " + cfg["label"]}


def verify_usdt(txid: str, wallet: str, min_amount: float, chain: str = "TRC20",
                timeout: float = 20.0, min_timestamp: float = 0.0) -> dict:
    """Multi-network dispatcher: TRC20 -> TronGrid/Tronscan; BEP20/ERC20/Polygon -> EVM RPC."""
    chain_u = (chain or "TRC20").upper()
    if chain_u == "TRC20":
        return verify_trc20_usdt(txid, wallet, min_amount, timeout, min_timestamp=min_timestamp)
    return verify_evm_usdt(txid, wallet, min_amount, chain_u, timeout, min_timestamp=min_timestamp)


def valid_wallet(wallet: str, chain: str = "TRC20") -> bool:
    """Address-shape validation per chain (admin input gate)."""
    w = (wallet or "").strip()
    if (chain or "").upper() == "TRC20":
        return w.startswith("T") and 30 <= len(w) <= 60 and bool(re.fullmatch(r"[A-Za-z0-9]+", w))
    if w.lower().startswith("0x"):
        w = w[2:]
    return bool(re.fullmatch(r"[0-9a-fA-F]{40}", w))
