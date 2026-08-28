# -*- coding: utf-8 -*-
"""
Turn a GitHub issue title into a watchlist command.

The title is untrusted text from an issue, so nothing here reaches a shell and
everything is validated by shape before it is emitted as a workflow output.

    add LUCK              -> action=add     add=LUCK
    add LUCK, SYS         -> action=add     add=LUCK,SYS
    remove DCR            -> action=remove  remove=DCR
    refresh               -> action=refresh
    ratios                -> action=ratios
    anything else         -> action=none
"""

import os, re, sys

SYMBOL_RE = re.compile(r"^[A-Z0-9]{1,12}$")
MAX = 20


def symbols(text):
    out = []
    for raw in re.split(r"[,\s;/]+", text.strip().upper()):
        raw = raw.strip()
        if raw and SYMBOL_RE.match(raw) and raw not in out:
            out.append(raw)
    return out[:MAX]


def parse(title):
    t = (title or "").strip()
    # tolerate "Add: LUCK", "ADD LUCK", "add  luck"
    m = re.match(r"^\s*(add|remove|delete|drop|refresh|update|ratios|balance)\b[:\s]*(.*)$",
                 t, re.I)
    if not m:
        return {"action": "none", "add": "", "remove": "", "summary": ""}
    verb, rest = m.group(1).lower(), m.group(2)

    if verb == "add":
        syms = symbols(rest)
        if not syms:
            return {"action": "none", "add": "", "remove": "", "summary": ""}
        return {"action": "add", "add": ",".join(syms), "remove": "",
                "summary": "Add %s" % ", ".join(syms)}

    if verb in ("remove", "delete", "drop"):
        syms = symbols(rest)
        if not syms:
            return {"action": "none", "add": "", "remove": "", "summary": ""}
        return {"action": "remove", "add": "", "remove": ",".join(syms),
                "summary": "Remove %s" % ", ".join(syms)}

    if verb in ("ratios", "balance"):
        return {"action": "ratios", "add": "", "remove": "",
                "summary": "Refresh data and balance-sheet ratios"}

    return {"action": "refresh", "add": "", "remove": "", "summary": "Refresh PSX data"}


def main():
    title = os.environ.get("TITLE", "")
    r = parse(title)

    # Anyone may ask for a stock to be collected: that is additive and changes
    # nobody's watchlist, because watchlists live in each visitor's browser.
    # Removing data, or kicking off a long ratios run, stays owner-only.
    is_owner = os.environ.get("IS_OWNER", "true").lower() == "true"
    if not is_owner and r["action"] not in ("add",):
        print("non-owner may only request 'add'; ignoring %r" % r["action"])
        r = {"action": "none", "add": "", "remove": "", "summary": ""}

    print("title  : %r" % title)
    print("owner  : %s" % is_owner)
    print("action : %s" % r["action"])
    if r["add"]:
        print("add    : %s" % r["add"])
    if r["remove"]:
        print("remove : %s" % r["remove"])

    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            for k, v in r.items():
                f.write("%s=%s\n" % (k, v))
    return 0


if __name__ == "__main__":
    sys.exit(main())
