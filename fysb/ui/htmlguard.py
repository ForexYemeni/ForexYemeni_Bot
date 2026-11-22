"""HTML guard — the publish-reliability layer for every channel broadcast.

ROOT CAUSE (fixed 2026-11-22, live report): multi-market channel posts
concatenated 2-3 analysis cards and were hard-cut with text[:3900]. The cut
can land INSIDE a tag or entity ("<blockquot", "&quo") — Telegram then
rejects the whole post with «Can't parse entities», and the old catch-all
misreported that as a permissions problem («البوت مشرف بصلاحية النشر»).
One market = short message = never truncated; two+ markets = truncation =
parse error. That is exactly why the failure only appeared with multiple
markets.

This module makes invalid HTML structurally impossible:

  * validate_html()  — tag balance / overlap / stray-entity audit
  * truncate_safe()  — cuts at a boundary that can NEVER split a tag or
                       entity, then closes whatever tags remain open
  * plain_fallback() — final-resort plain text (tags stripped, unescaped)

Every channel send path (periodic analysis + live signals) routes through
this guard before touching the Telegram API.
"""
import html as _html
import re

# Tags we ourselves emit anywhere in the codebase (plus expandable variant).
_KNOWN_TAGS = {"b", "i", "u", "s", "code", "pre", "a", "blockquote"}
# Tags that never take a closing partner in our markup.
_VOID_TAGS = set()

_TAG_OPEN_RE = re.compile(r"<([a-zA-Z][a-zA-Z0-9]*)((?:\s[^<>]*?)?)\s*/?>")
_TAG_CLOSE_RE = re.compile(r"</\s*([a-zA-Z][a-zA-Z0-9]*)\s*>")


def _open_stack(text: str) -> list[str]:
    """Return the stack of tags that are still open at the end of *text*.

    Used both by the validator and by the safe truncator. Overlapping closes
    auto-heal: closing «b» while «i» is also open pops both (inner first).
    """
    stack: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "<":
            close = text.find(">", i)
            if close == -1:
                break  # dangling '<' — caller decides what to do
            inner = text[i + 1:close].strip()
            if inner.startswith("/"):
                tag = inner[1:].strip().lower()
                if tag in stack:
                    while stack and stack[-1] != tag:
                        stack.pop()
                    stack.pop()
            else:
                m = _TAG_OPEN_RE.match(text[i:close + 1])
                tag = m.group(1).lower() if m else (inner.split()[0].lower() if inner.split() else "")
                if tag and tag not in _VOID_TAGS:
                    stack.append(tag)
            i = close + 1
        elif ch == "&":
            semi = text.find(";", i)
            i = semi + 1 if (semi != -1 and semi - i <= 10) else i + 1
        else:
            i += 1
    return stack


def validate_html(text: str) -> tuple[bool, str]:
    """True when the markup is balanced and entity-safe for Telegram."""
    stack: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "<":
            close = text.find(">", i)
            if close == -1:
                return False, f"وسم غير مكتمل عند الحرف {i}"
            inner = text[i + 1:close].strip()
            if inner.startswith("/"):
                tag = inner[1:].strip().lower()
                if not stack:
                    return False, f"إغلاق بدون فتح: </{tag}>"
                if stack[-1] != tag:
                    return False, f"تراكب الوسوم: فُتح <{stack[-1]}> وأُغلق </{tag}>"
                stack.pop()
            else:
                tag = inner.split()[0].lower() if inner.split() else ""
                if not tag:
                    return False, f"وسم فارغ عند الحرف {i}"
                stack.append(tag)
            i = close + 1
        elif ch == "&":
            semi = text.find(";", i)
            if semi == -1 or semi - i > 10:
                return False, f"كيان HTML غير مكتمل عند الحرف {i}"
            i = semi + 1
        else:
            i += 1
    if stack:
        return False, "وسوم لم تُغلق: " + ", ".join(stack)
    return True, ""


def truncate_safe(text: str, limit: int = 3900) -> str:
    """Cut to *limit* chars at a boundary that can never break the markup.

    Order of protection (mirrors how a hard cut corrupts HTML):
      1. never end inside an open tag  (<blockquot|)
      2. never end inside an entity    (&quo|)
      3. close every tag left open     (</blockquote></b>…)
    """
    if len(text) <= limit:
        return text
    cut = text[:limit]
    last_lt, last_gt = cut.rfind("<"), cut.rfind(">")
    if last_gt < last_lt:  # a '<' opened but never closed within the budget
        cut = cut[:last_lt]
    amp, semi = cut.rfind("&"), cut.rfind(";")
    if amp > semi:  # dangling entity start
        cut = cut[:amp]
    for tag in reversed(_open_stack(cut)):
        cut += f"</{tag}>"
    return cut


_PLAIN_RE = re.compile(r"<[^>]+>")


def plain_fallback(text: str) -> str:
    """Strip all markup — the guaranteed-parseable last-resort version."""
    return _html.unescape(_PLAIN_RE.sub("", text))
