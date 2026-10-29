#!/usr/bin/env python3
"""PR Radar watcher — free change detection for official Canadian immigration pages.

Runs on GitHub Actions. For each page in sources.json it extracts the readable
text of the main content, compares it with the last saved snapshot, and opens a
GitHub issue listing what changed. For the IRCC news feed it reports new items
that match immigration keywords. Standard library only. No AI, no paid services.

It never interprets a change. It only reports that official text changed and
shows the added and removed lines, with a link to the page.
"""
import datetime as dt
import difflib
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

ROOT = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(ROOT, "state", "state.json")
SNAP_DIR = os.path.join(ROOT, "snapshots")
UA = "Mozilla/5.0 (compatible; PR-Radar-personal-watcher/1.0; +https://github.com)"
FAIL_ALERT_AFTER = 3          # consecutive failed runs before alerting about a source
HEARTBEAT_DAYS = 7            # commit at least this often so GitHub keeps the schedule active
MAX_ADDED, MAX_REMOVED = 25, 15

BLOCK = {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "div", "section", "article", "dt", "dd",
         "td", "th", "br", "caption", "summary", "details", "table", "ul", "ol", "blockquote", "figcaption"}
SKIP = {"script", "style", "noscript", "nav", "header", "footer", "svg", "form", "button", "template"}


class MainText(HTMLParser):
    """Collects visible text, preferring the <main> element."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_main = 0
        self.skip = 0
        self.main_lines, self.body_lines = [], []
        self.buf_main, self.buf_body = [], []
        self.meta = {}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta":
            k = (a.get("name") or a.get("property") or "").lower()
            if k in ("dcterms.modified", "article:modified_time"):
                self.meta["modified"] = (a.get("content") or "").strip()
            return
        if tag == "main":
            self.in_main += 1
        if tag in SKIP:
            self.skip += 1
        if tag in BLOCK:
            self._flush()

    def handle_endtag(self, tag):
        if tag in BLOCK:
            self._flush()
        if tag in SKIP and self.skip:
            self.skip -= 1
        if tag == "main" and self.in_main:
            self._flush()
            self.in_main -= 1

    def handle_data(self, data):
        if self.skip:
            return
        self.buf_body.append(data)
        if self.in_main:
            self.buf_main.append(data)

    def _flush(self):
        for buf, lines in ((self.buf_main, self.main_lines), (self.buf_body, self.body_lines)):
            t = re.sub(r"\s+", " ", "".join(buf)).strip()
            if t:
                lines.append(t)
            buf.clear()

    def result(self):
        self._flush()
        lines = self.main_lines if len(self.main_lines) >= 5 else self.body_lines
        # drop consecutive duplicates
        out = []
        for line in lines:
            if not out or out[-1] != line:
                out.append(line)
        return out


def page_text(raw_html):
    p = MainText()
    p.feed(raw_html)
    p.close()
    return p.result(), p.meta.get("modified")


def fetch(url, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-CA,en;q=0.9"})
            with urllib.request.urlopen(req, timeout=30) as r:
                charset = r.headers.get_content_charset() or "utf-8"
                return r.read().decode(charset, errors="replace")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"{type(last).__name__}: {last}")


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False, sort_keys=True)
        f.write("\n")


def now_iso():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def check_page(src, state, report):
    st = state["pages"].setdefault(src["id"], {})
    snap_path = os.path.join(SNAP_DIR, src["id"] + ".txt")
    try:
        lines, modified = page_text(fetch(src["url"]))
    except Exception as e:  # noqa: BLE001
        st["fails"] = st.get("fails", 0) + 1
        st["last_error"] = str(e)[:300]
        report["dirty"] = True
        if st["fails"] == FAIL_ALERT_AFTER:
            report["failures"].append((src, st["last_error"]))
        return
    if len(lines) < 3:
        st["fails"] = st.get("fails", 0) + 1
        st["last_error"] = "Page returned almost no text"
        report["dirty"] = True
        if st["fails"] == FAIL_ALERT_AFTER:
            report["failures"].append((src, st["last_error"]))
        return
    if st.get("fails", 0) >= FAIL_ALERT_AFTER:
        report["recovered"].append(src)
    if st.get("fails"):
        report["dirty"] = True
    st["fails"] = 0
    st.pop("last_error", None)
    text = "\n".join(lines) + "\n"
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    st["last_ok"] = now_iso()
    if "hash" not in st:
        st.update(hash=digest, modified=modified, since=now_iso())
        with open(snap_path, "w", encoding="utf-8") as f:
            f.write(text)
        report["baseline"].append(src)
        report["dirty"] = True
        return
    if digest == st["hash"]:
        return
    try:
        with open(snap_path, encoding="utf-8") as f:
            old = f.read().splitlines()
    except FileNotFoundError:
        old = []
    added, removed = [], []
    for d in difflib.unified_diff(old, lines, lineterm="", n=0):
        if d.startswith("+++") or d.startswith("---") or d.startswith("@@"):
            continue
        if d.startswith("+"):
            added.append(d[1:])
        elif d.startswith("-"):
            removed.append(d[1:])
    report["changes"].append({"src": src, "added": added, "removed": removed, "old_mod": st.get("modified"), "new_mod": modified})
    st.update(hash=digest, modified=modified, since=now_iso())
    with open(snap_path, "w", encoding="utf-8") as f:
        f.write(text)
    report["dirty"] = True


ATOM = "{http://www.w3.org/2005/Atom}"


def parse_feed(raw):
    root = ET.fromstring(raw)
    items = []
    for e in root.iter(ATOM + "entry"):
        def t(tag):
            el = e.find(ATOM + tag)
            return (el.text or "").strip() if el is not None and el.text else ""
        link = ""
        for l in e.findall(ATOM + "link"):
            link = l.get("href") or link
        summ = re.sub(r"<[^>]+>", " ", html.unescape(t("summary") or t("content")))
        items.append({"id": t("id") or link, "title": t("title"), "link": link, "date": (t("published") or t("updated"))[:10], "summary": re.sub(r"\s+", " ", summ).strip()[:400]})
    for e in root.iter("item"):  # RSS 2.0 fallback
        g = lambda tag: (e.findtext(tag) or "").strip()  # noqa: E731
        items.append({"id": g("guid") or g("link"), "title": g("title"), "link": g("link"), "date": g("pubDate")[:16], "summary": re.sub(r"<[^>]+>", " ", html.unescape(g("description")))[:400]})
    return items


def check_feed(src, state, report):
    st = state["feeds"].setdefault(src["id"], {})
    try:
        items = parse_feed(fetch(src["url"]))
    except Exception as e:  # noqa: BLE001
        st["fails"] = st.get("fails", 0) + 1
        st["last_error"] = str(e)[:300]
        report["dirty"] = True
        if st["fails"] == FAIL_ALERT_AFTER:
            report["failures"].append((src, st["last_error"]))
        return
    if st.get("fails"):
        report["dirty"] = True
    st["fails"] = 0
    st.pop("last_error", None)
    st["last_ok"] = now_iso()
    seen = set(st.get("seen", []))
    first_run = not seen
    kws = [k.lower() for k in src.get("keywords", [])]
    for it in items:
        if not it["id"] or it["id"] in seen:
            continue
        seen.add(it["id"])
        if first_run:
            continue
        hay = (it["title"] + " " + it["summary"]).lower()
        hits = [k for k in kws if k in hay]
        if hits or not kws:
            report["news"].append({"src": src, "item": it, "hits": hits})
    if first_run:
        report["baseline"].append(src)
    st["seen"] = sorted(seen)[-500:]
    if first_run or report["news"]:
        report["dirty"] = True


def build_issue(report, owner):
    parts, titles = [], []
    for c in report["changes"]:
        s = c["src"]
        titles.append(s["name"].split(":")[-1].strip())
        mod = ""
        if c["new_mod"] and c["new_mod"] != c["old_mod"]:
            mod = f" — page date changed {c['old_mod'] or '?'} → {c['new_mod']}"
        block = [f"### 📄 {s['name']}{mod}", f"{s['url']}", ""]
        if c["added"]:
            block.append(f"**New or changed text** ({len(c['added'])} line{'s' if len(c['added']) != 1 else ''}):")
            block += [f"> {line}" for line in c["added"][:MAX_ADDED]]
            if len(c["added"]) > MAX_ADDED:
                block.append(f"> … and {len(c['added']) - MAX_ADDED} more")
        if c["removed"]:
            block.append("")
            block.append("<details><summary>Removed text</summary>\n")
            block += [f"- ~~{line}~~" for line in c["removed"][:MAX_REMOVED]]
            block.append("\n</details>")
        if s.get("note"):
            block.append(f"\n_{s['note']}_")
        parts.append("\n".join(block))
    for n in report["news"]:
        it = n["item"]
        titles.append(it["title"][:40])
        parts.append(f"### 📰 IRCC news: {it['title']}\n{it['link']}\nPublished {it['date'] or 'date not given'} · matched: {', '.join(n['hits']) or 'all'}\n\n> {it['summary']}")
    for s, err in report["failures"]:
        titles.append("source unreachable")
        parts.append(f"### ⚠️ Could not read: {s['name']}\n{s['url']}\nFailed {FAIL_ALERT_AFTER} runs in a row. Last error: `{err}`\nNo update was invented for this source. If the page moved, update its URL in `sources.json`.")
    for s in report["recovered"]:
        titles.append("source reachable again")
        parts.append(f"### ✅ Reachable again: {s['name']}")
    if not parts:
        return None, None
    when = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    title = "[PR Radar] " + "; ".join(titles)[:180]
    body = (f"@{owner} official immigration pages changed (checked {when}).\n\n"
            "This watcher only detects changes. It does not interpret them. "
            "To get a plain-language explanation and update your Canada PR Intelligence app, ask Claude: **“PR Radar found a change — what does it mean for me?”** and paste this issue's link.\n\n---\n\n"
            + "\n\n---\n\n".join(parts))
    return title, body[:60000]


def open_issue(title, body):
    token, repo = os.environ.get("GH_TOKEN"), os.environ.get("REPO")
    if not token or not repo:
        print("No GH_TOKEN/REPO: printing instead of opening an issue\n", title, "\n", body)
        return
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/issues", method="POST",
                                 data=json.dumps({"title": title, "body": body}).encode(),
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        print("Opened issue:", json.load(r).get("html_url"))


def push_ntfy(title, body):
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return
    first = re.sub(r"[#>*_`~]", "", body.split("---", 2)[-1]).strip()[:300]
    issues_url = f"https://github.com/{os.environ.get('REPO', '')}/issues"
    req = urllib.request.Request(f"https://ntfy.sh/{topic}", data=first.encode("utf-8"), method="POST",
                                 headers={"Title": title[:200].encode("ascii", "ignore").decode(), "Click": issues_url, "Tags": "maple_leaf", "Priority": "high"})
    try:
        urllib.request.urlopen(req, timeout=20).read()
    except Exception as e:  # noqa: BLE001
        print("ntfy push failed:", e)


def main():
    cfg = load_json(os.path.join(ROOT, "sources.json"), {})
    state = load_json(STATE_FILE, {})
    state.setdefault("pages", {})
    state.setdefault("feeds", {})
    report = {"changes": [], "news": [], "failures": [], "recovered": [], "baseline": [], "dirty": False}
    for src in cfg.get("pages", []):
        check_page(src, state, report)
    for src in cfg.get("feeds", []):
        check_feed(src, state, report)
    owner = os.environ.get("OWNER", "")
    title, body = build_issue(report, owner)
    if report["baseline"] and not state.get("started"):
        state["started"] = now_iso()
        ok = len(report["baseline"])
        failed = [s for s in cfg.get("pages", []) + cfg.get("feeds", []) if (state["pages"].get(s["id"]) or state["feeds"].get(s["id"]) or {}).get("fails")]
        title = title or "[PR Radar] Watcher is running"
        body = (body or "") + (f"\n\n---\n\n@{owner} PR Radar is set up. Saved a starting copy of {ok} source(s). "
                               "From now on you'll get an issue here only when one of them changes."
                               + (f"\n\nNot reachable on this first run: {', '.join(s['name'] for s in failed)}" if failed else ""))
    last_beat = state.get("heartbeat")
    if not last_beat or (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(last_beat)).days >= HEARTBEAT_DAYS:
        state["heartbeat"] = now_iso()
        report["dirty"] = True
    state["last_run"] = now_iso()
    if report["dirty"] or title:
        save_json(STATE_FILE, state)
    if title:
        open_issue(title, body)
        push_ntfy(title, body)
    print(f"pages changed: {len(report['changes'])}, news: {len(report['news'])}, failures: {len(report['failures'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
