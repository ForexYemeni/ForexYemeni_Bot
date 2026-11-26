"""Payments service — methods, requests, approval gate. No double-approval."""
from fysb import config, db

# v6.8.1 — per-network USDT wallets for instant on-chain activation.
# The owner demanded BEP20 + more networks next to TRC20; each chain keeps its
# own wallet setting and its own verifier (public blockchain APIs, no keys).
CHAIN_WALLET_KEYS = {
    "TRC20": "usdt_wallet_trc20",
    "BEP20": "usdt_wallet_bep20",
    "ERC20": "usdt_wallet_erc20",
    "POLYGON": "usdt_wallet_polygon",
}


def chain_wallet(chain: str) -> str:
    key = CHAIN_WALLET_KEYS.get((chain or "").upper())
    return db.get_setting(key, "") if key else ""


def set_chain_wallet(chain: str, wallet: str) -> bool:
    key = CHAIN_WALLET_KEYS.get((chain or "").upper())
    if not key:
        return False
    db.set_setting(key, (wallet or "").strip())
    return True


def configured_chains() -> list[str]:
    """Networks whose wallets are actually configured — the user may pick
    ONLY among these (a chain without a destination wallet cannot verify)."""
    return [ch for ch in CHAIN_WALLET_KEYS if chain_wallet(ch)]


def list_methods(enabled_only: bool = False) -> list:
    with db.conn() as c:
        q = "SELECT * FROM payment_methods" + (" WHERE enabled=1" if enabled_only else "") + " ORDER BY id"
        return [dict(r) for r in c.execute(q).fetchall()]


def get_method(mid: int):
    with db.conn() as c:
        r = c.execute("SELECT * FROM payment_methods WHERE id=?", (mid,)).fetchone()
    return dict(r) if r else None


def add_method(title: str, instructions: str, account_ref: str, country: str = "", linked_plans: str = "") -> int:
    with db.conn() as c:
        cur = c.execute("""INSERT INTO payment_methods(title,instructions,account_ref,enabled,created_at,country,linked_plans)
                           VALUES(?,?,?,1,?,?,?)""", (title, instructions, account_ref, config.now(), country, linked_plans))
        return cur.lastrowid


def update_method(mid: int, **fields) -> bool:
    allowed = {"title", "instructions", "account_ref", "enabled", "country", "linked_plans"}
    sets = ", ".join(f"{k}=?" for k in fields if k in allowed)
    vals = [v for k, v in fields.items() if k in allowed]
    if not sets:
        return False
    with db.conn() as c:
        cur = c.execute(f"UPDATE payment_methods SET {sets} WHERE id=?", (*vals, mid))
        return cur.rowcount > 0


def delete_method(mid: int) -> bool:
    with db.conn() as c:
        return c.execute("DELETE FROM payment_methods WHERE id=?", (mid,)).rowcount > 0


def create_request(user_id: int, plan_code: str, amount: float, method: str) -> int:
    with db.conn() as c:
        cur = c.execute("INSERT INTO payments(user_id,plan_code,amount,method,status,created_at) VALUES(?,?,?,?,'PENDING',?)",
                        (user_id, plan_code, amount, method, config.now()))
        return cur.lastrowid


def attach_receipt(payment_id: int, user_id: int, file_id: str) -> bool:
    with db.conn() as c:
        cur = c.execute("UPDATE payments SET receipt_file_id=? WHERE id=? AND user_id=? AND status='PENDING'",
                        (file_id, payment_id, user_id))
        return cur.rowcount > 0


def txid_used(txid: str) -> int:
    """The internal id of the payment already bound to this TXID (0 = free).
    Case-insensitive and 0x-prefix-agnostic: one on-chain transaction can
    activate ONE plan — ever."""
    from fysb.services.chain_verify import canonical_txid
    canon = canonical_txid(txid)
    if not canon:
        return 0
    with db.conn() as c:
        r = c.execute(
            "SELECT id FROM payments WHERE LOWER(txid)=? OR LOWER(txid)=?",
            (canon, "0x" + canon),
        ).fetchone()
    return r["id"] if r else 0


def attach_txid(payment_id: int, user_id: int, txid: str, chain: str) -> dict:
    """Bind a VERIFIED on-chain TXID to a PENDING payment and block its reuse
    forever (v6.12.0 hardened blockchain protocol). Stores canonical lowercase
    TXID (no 0x prefix) inside an atomic transaction backed by a DB UNIQUE
    index on LOWER(txid)."""
    from fysb.services.chain_verify import canonical_txid
    canon = canonical_txid(txid)
    if not canon:
        return {"ok": False, "error": "صيغة TXID غير صالحة"}
    try:
        with db.tx(immediate=True) as c:
            other = c.execute(
                "SELECT id FROM payments WHERE LOWER(txid)=? OR LOWER(txid)=?",
                (canon, "0x" + canon),
            ).fetchone()
            if other:
                return {"ok": False, "error": f"هذا TXID مُستخدم مسبقاً في الطلب #{other['id']} — المعاملة محجوبة"}
            cur = c.execute("""UPDATE payments SET txid=?, chain=?, confirmed_at=?
                               WHERE id=? AND user_id=? AND status='PENDING'""",
                            (canon, (chain or "").upper(), config.now(), payment_id, user_id))
            if cur.rowcount == 0:
                return {"ok": False, "error": "الطلب غير موجود أو تمت مراجعته مسبقاً"}
    except Exception as e:
        if "unique" in str(e).lower() or "constraint" in str(e).lower():
            return {"ok": False, "error": "هذا TXID مُستخدم مسبقاً — المعاملة محجوبة"}
        raise
    return {"ok": True}


def get_request(pid: int):
    with db.conn() as c:
        r = c.execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone()
    return dict(r) if r else None


def list_requests(status: str = "PENDING", limit: int = 15) -> list:
    with db.conn() as c:
        if status:
            rows = c.execute("SELECT * FROM payments WHERE status=? ORDER BY id DESC LIMIT ?", (status, limit)).fetchall()
        else:
            rows = c.execute("SELECT * FROM payments ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def approve_request(pid: int, actor: int) -> dict:
    """Approve exactly once (guarded by PENDING state inside a transaction)."""
    from fysb.services.subscriptions import grant_subscription
    with db.tx(immediate=True) as c:
        p = c.execute("SELECT * FROM payments WHERE id=? AND status='PENDING'", (pid,)).fetchone()
        if not p:
            return {"ok": False, "error": "طلب غير موجود أو تمت مراجعته مسبقاً"}
        c.execute("UPDATE payments SET status='APPROVED', reviewed_at=? WHERE id=?", (config.now(), pid))
    p_dict = dict(p)
    plan = None
    with db.conn() as c:
        plan = c.execute("SELECT * FROM plans WHERE code=?", (p_dict["plan_code"],)).fetchone()
    grant_res = {}
    p_code_up = str(p_dict.get("plan_code") or "").upper()
    if p_code_up == "ACADEMY":
        from fysb.services import academy as _acad, partners as _partners
        _acad.grant_paid_access(p_dict["user_id"], source="PAYMENT", granted_by=actor)
        # v8.7.0: Channel Partners also earn their exact commission on Academy purchases!
        try:
            p_comm = _partners.credit_commission_on_subscription(
                p_dict["user_id"],
                "ACADEMY",
                payment_id=pid,
                amount=float(p_dict.get("amount") or 0),
                currency=p_dict.get("currency") or "USD",
            )
            if p_comm:
                grant_res["commission"] = p_comm
        except Exception:
            pass
    elif p_code_up == "TRUE_ANALYSIS":
        from fysb.services import partners as _partners, true_analysis as _ta
        _ta.grant_paid_access(p_dict["user_id"], actor=actor)
        # v8.7.0: Channel Partners also earn their exact commission on True Analysis purchases!
        try:
            p_comm = _partners.credit_commission_on_subscription(
                p_dict["user_id"],
                "TRUE_ANALYSIS",
                payment_id=pid,
                amount=float(p_dict.get("amount") or 0),
                currency=p_dict.get("currency") or "USD",
            )
            if p_comm:
                grant_res["commission"] = p_comm
        except Exception:
            pass
    elif plan:
        pl_dict = dict(plan)
        grant_res = grant_subscription(
            p_dict["user_id"],
            p_dict["plan_code"],
            source="PAYMENT",
            actor=actor,
            payment_id=pid,
            amount=float(p_dict.get("amount") or 0),
            currency=p_dict.get("currency") or pl_dict.get("currency") or "USD",
        )
    # v8.0.0 (PAY-2) & v8.7.0: User Affiliate Commission — NEVER double-credit if a Channel Partner
    # commission was already credited for this exact payment!
    aff_comm = None
    partner_credited = bool(isinstance(grant_res, dict) and grant_res.get("commission"))
    if not partner_credited:
        try:
            from fysb.services import affiliates as _aff
            aff_comm = _aff.record_commission_on_payment(
                int(p_dict["user_id"]),
                float(p_dict.get("amount") or 0),
                payment_id=pid,
            )
        except Exception as exc:
            from fysb.config import log
            log.debug("affiliate commission skipped for payment #%s: %s", pid, exc)
    # v7.6.0: Redeem attached promo code (if any) atomically on approval
    try:
        pr_code = str(p_dict.get("promo_code") or "").strip()
        pr_pct = int(p_dict.get("discount_pct") or 0)
        if pr_code and pr_pct > 0:
            from fysb.services import promos as _promos
            orig_amt = float((dict(plan).get("price") if plan else 0) or 0)
            saved_amt = max(0.0, round(orig_amt - float(p_dict.get("amount") or 0), 2))
            _promos.redeem_promo(pr_code, int(p_dict["user_id"]), payment_id=pid,
                                 plan_code=str(p_dict.get("plan_code") or ""), saved_amount=saved_amt)
    except Exception:
        pass
    from fysb.services.audit import log as audit_log
    audit_log(actor, f"payment_approved:{pid}")
    return {"ok": True, "payment": p_dict, "commission": grant_res.get("commission"), "affiliate_commission": aff_comm}


def reject_request(pid: int, reason: str, actor: int) -> dict:
    with db.tx(immediate=True) as c:
        p = c.execute("SELECT * FROM payments WHERE id=? AND status='PENDING'", (pid,)).fetchone()
        if not p:
            return {"ok": False, "error": "طلب غير موجود أو تمت مراجعته مسبقاً"}
        c.execute("UPDATE payments SET status='REJECTED', rejection_reason=?, reviewed_at=? WHERE id=?",
                  (reason[:400], config.now(), pid))
    return {"ok": True, "payment": dict(p)}


def stats() -> dict:
    with db.conn() as c:
        return {
            "pending": c.execute("SELECT COUNT(*) n FROM payments WHERE status='PENDING'").fetchone()["n"],
            "approved": c.execute("SELECT COUNT(*) n FROM payments WHERE status='APPROVED'").fetchone()["n"],
            "rejected": c.execute("SELECT COUNT(*) n FROM payments WHERE status='REJECTED'").fetchone()["n"],
            "approved_amount": c.execute("SELECT COALESCE(SUM(amount),0) s FROM payments WHERE status='APPROVED'").fetchone()["s"],
        }
