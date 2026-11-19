#!/usr/bin/env python3
import argparse, base64, json, sys, threading, time, traceback, urllib.parse, webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    import requests
except ImportError:
    sys.exit("Missing 'requests'. Run:  pip install requests")

API = "https://discord.com/api/v10"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

SUPER_PROPS = base64.b64encode(json.dumps({
    "os": "Windows", "browser": "Chrome", "device": "",
    "system_locale": "en-US", "browser_user_agent": UA,
    "browser_version": "131.0.0.0", "os_version": "10",
    "referrer": "", "referring_domain": "", "referrer_current": "",
    "referring_domain_current": "", "release_channel": "stable",
    "client_build_number": 370000, "client_event_source": None,
}, separators=(",", ":")).encode()).decode()

CHANNEL_EXTRA = {
    0: ["topic", "nsfw", "rate_limit_per_user", "default_auto_archive_duration",
        "default_thread_rate_limit_per_user"],
    2: ["bitrate", "user_limit", "rtc_region", "video_quality_mode", "nsfw"],
    4: [],
    5: ["topic", "nsfw", "rate_limit_per_user", "default_auto_archive_duration"],
    13: ["bitrate", "user_limit", "rtc_region"],
    15: ["topic", "nsfw", "rate_limit_per_user", "default_auto_archive_duration",
         "default_thread_rate_limit_per_user", "default_sort_order", "default_forum_layout"],
    16: ["topic", "nsfw", "rate_limit_per_user", "default_auto_archive_duration"],
}

COPYABLE_FEATURES = {
    "COMMUNITY", "NEWS", "WELCOME_SCREEN_ENABLED",
    "MEMBER_VERIFICATION_GATE_ENABLED", "PREVIEW_ENABLED",
    "THREADS_ENABLED", "GUILD_TAGS",
}

LOCK = threading.Lock()
STATE = {"log": [], "running": False, "done": False}

def log(msg):
    with LOCK:
        STATE["log"].append(str(msg))
    print(msg, flush=True)

class DapiError(Exception):
    pass

class Dapi:
    def __init__(self, token, delay=0.12):
        self.s = requests.Session()
        self.s.headers.update({
            "Authorization": token.strip(),
            "User-Agent": UA,
            "X-Super-Properties": SUPER_PROPS,
            "X-Discord-Locale": "en-US",
            "X-Discord-Timezone": "Europe/Belgrade",
            "X-Debug-Options": "bugReporterEnabled",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": "https://discord.com",
            "Referer": "https://discord.com/channels/@me",
        })
        self.delay = delay

    def req(self, method, path, **kw):
        url = API + path
        for _ in range(10):
            r = self.s.request(method, url, timeout=120, **kw)
            if r.status_code == 429:
                try:
                    wait = float(r.json().get("retry_after", 1.0))
                except Exception:
                    wait = 1.5
                time.sleep(wait + 0.2)
                continue
            if r.status_code >= 400:
                raise DapiError(f"{method} {path} -> HTTP {r.status_code}: {r.text[:250]}")
            if not r.content:
                return None
            try:
                return r.json()
            except Exception:
                return None
        raise DapiError(f"{method} {path} -> too many 429s")

def data_uri(url, mime=None):
    r = requests.get(url, headers={"User-Agent": UA}, timeout=120)
    if r.status_code != 200:
        raise RuntimeError(f"CDN {url} -> {r.status_code}")
    if mime is None:
        mime = "image/gif" if url.split("?")[0].lower().endswith(".gif") else "image/png"
    return f"data:{mime};base64," + base64.b64encode(r.content).decode()

def map_overwrites(overwrites, role_map, with_members=False):
    out = []
    for o in overwrites or []:
        if o["type"] == 0:
            nid = role_map.get(o["id"])
            if nid:
                out.append({"id": nid, "type": 0, "allow": o["allow"], "deny": o["deny"]})
        elif o["type"] == 1 and with_members:
            out.append({"id": o["id"], "type": 1, "allow": o["allow"], "deny": o["deny"]})
    return out

def safe(name, fn, stats):
    try:
        return fn()
    except DapiError as e:
        stats["fails"].append(f"{name}: {e}")
        log(f"     [X] {name} — {e}")
        return None
    except Exception as e:
        stats["fails"].append(f"{name}: {e}")
        log(f"     [X] {name} — {e}")
        return None

def parallel_delete(items, worker, stats, label):
    ok = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(worker, it): it for it in items}
        for f in as_completed(futures):
            try:
                f.result()
                ok += 1
            except DapiError as e:
                it = futures[f]
                stats["fails"].append(f"delete {label} {it}: {e}")
            except Exception as e:
                it = futures[f]
                stats["fails"].append(f"delete {label} {it}: {e}")
    return ok

def wipe_destination(d, dst_id, stats, do_roles, do_channels, do_emoji, do_stickers):
    log("--- WIPING DESTINATION SERVER ---")

    if do_channels:
        chs = safe("list channels", lambda: d.req("GET", f"/guilds/{dst_id}/channels"), stats) or []
        kids = [c for c in chs if c["type"] != 4]
        cats = [c for c in chs if c["type"] == 4]
        n = parallel_delete(kids, lambda c: d.req("DELETE", f"/channels/{c['id']}"), stats, "channel")
        n += parallel_delete(cats, lambda c: d.req("DELETE", f"/channels/{c['id']}"), stats, "channel")
        log(f"     deleted channels: {n}/{len(chs)}")

    if do_roles:
        roles = safe("list roles", lambda: d.req("GET", f"/guilds/{dst_id}/roles"), stats) or []
        targets = [r for r in roles if r["id"] != dst_id and not r.get("managed")]
        n = parallel_delete(targets, lambda r: d.req("DELETE", f"/guilds/{dst_id}/roles/{r['id']}"),
                            stats, "role")
        log(f"     deleted roles: {n}/{len(targets)}")

    if do_emoji:
        ems = safe("list emojis", lambda: d.req("GET", f"/guilds/{dst_id}/emojis"), stats) or []
        n = parallel_delete(ems, lambda e: d.req("DELETE", f"/guilds/{dst_id}/emojis/{e['id']}"),
                            stats, "emoji")
        log(f"     deleted emojis: {n}/{len(ems)}")

    if do_stickers:
        sts = safe("list stickers", lambda: d.req("GET", f"/guilds/{dst_id}/stickers"), stats) or []
        n = parallel_delete(sts, lambda s: d.req("DELETE", f"/guilds/{dst_id}/stickers/{s['id']}"),
                            stats, "sticker")
        log(f"     deleted stickers: {n}/{len(sts)}")

def copy_guild(src_id, dst_id, token, opts):
    d = Dapi(token)
    stats = {"fails": [], "skips": []}

    log("1/10  Token & servers...")
    me = d.req("GET", "/users/@me")
    log(f"     account: {me['username']} ({me['id']})")
    src = d.req("GET", f"/guilds/{src_id}")
    dst = d.req("GET", f"/guilds/{dst_id}")
    log(f"     source: {src['name']}  ->  destination: {dst['name']}")

    tier = int(dst.get("premium_tier", 0))
    max_e = {0: 0, 1: 50, 2: 100, 3: 250}.get(tier, 0)
    max_s = {0: 5, 1: 15, 2: 30, 3: 60}.get(tier, 5)
    if tier < 2:
        log(f"     destination Tier {tier}: role icons/emoji skipped (boost-gated)")
    log(f"     emoji limit: {max_e}, sticker limit: {max_s}")

    role_map = {}

    log("2/10  Wiping destination...")
    wipe_destination(d, dst_id, stats,
                     opts.get("wipe_roles", True), opts.get("wipe_channels", True),
                     opts.get("wipe_emoji", True), opts.get("wipe_stickers", True))

    log("3/10  Roles...")
    ev = next((r for r in src["roles"] if r["id"] == src_id), None)
    if ev:
        safe("@everyone permissions", lambda: d.req(
            "PATCH", f"/guilds/{dst_id}/roles/{dst_id}",
            json={"permissions": ev["permissions"]}), stats)

    created = 0
    for r in sorted(src["roles"], key=lambda x: x["position"]):
        if r["id"] == src_id:
            continue
        if r.get("managed"):
            stats["skips"].append(f"bot role {r['name']}")
            log(f"     - bot role (managed), skipped: {r['name']}")
            continue

        rname = ((r["name"] or "").strip() or "Role")[:100]

        def attempt(extra):
            p = {"name": rname, "permissions": r["permissions"], "color": r["color"],
                 "hoist": r["hoist"], "mentionable": r["mentionable"], **extra}
            return d.req("POST", f"/guilds/{dst_id}/roles", json=p)

        extra = {}
        if tier >= 2:
            if r.get("unicode_emoji"):
                extra["unicode_emoji"] = r["unicode_emoji"]
            if r.get("icon"):
                try:
                    extra["icon"] = data_uri(
                        f"https://cdn.discordapp.com/role-icons/{r['id']}/{r['icon']}.png?size=128")
                except Exception as e:
                    log(f"       role icon {rname}: {e}")

        new = None
        for label, payload in (("decorated", extra), ("plain", {}), ("minimal", {})):
            try:
                new = attempt(payload)
                if label != "decorated":
                    log(f"     [~] {rname}: created as {label} (fallback)")
                break
            except DapiError as e:
                es = str(e)
                if "50101" in es or "boost" in es.lower():
                    continue
                if "50035" in es and label == "decorated":
                    continue
                log(f"     [X] role {rname} — {es[:180]}")
                stats["fails"].append(f"role {rname}: {es[:180]}")
                break
        if new:
            role_map[r["id"]] = new["id"]
            created += 1
    log(f"     roles created: {created}")

    if role_map:
        pos = [{"id": role_map[r["id"]], "position": r["position"]}
               for r in src["roles"] if r["id"] in role_map]
        safe("role order", lambda: d.req("PATCH", f"/guilds/{dst_id}/roles", json=pos), stats)

    log("4/10  Channels...")
    src_ch = safe("source channels", lambda: d.req("GET", f"/guilds/{src_id}/channels"), stats) or []
    channel_map, ch_created = {}, 0
    cats = sorted([c for c in src_ch if c["type"] == 4], key=lambda c: c["position"])
    rest = sorted([c for c in src_ch if c["type"] != 4], key=lambda c: c["position"])

    def build_payload(c, as_type=None):
        t = as_type or c["type"]
        p = {"name": c["name"][:100], "type": t, "position": c["position"]}
        if t != 4 and c.get("parent_id") in channel_map:
            p["parent_id"] = channel_map[c["parent_id"]]
        for k in CHANNEL_EXTRA.get(t, []):
            if c.get(k) is not None:
                p[k] = c[k]
        if t == 15 and c.get("available_tags"):
            p["available_tags"] = [{"name": tt["name"][:20],
                                    "moderated": tt.get("moderated", False),
                                    **({"emoji_name": tt["emoji_name"]} if tt.get("emoji_name") else {})}
                                   for tt in c["available_tags"]][:20]
        dr = c.get("default_reaction_emoji")
        if t == 15 and dr and dr.get("emoji_name"):
            p["default_reaction_emoji"] = {"emoji_id": None, "emoji_name": dr["emoji_name"]}
        ow = map_overwrites(c.get("permission_overwrites"), role_map,
                            opts.get("member_overwrites", False))
        if ow:
            p["permission_overwrites"] = ow
        return p

    for c in cats + rest:
        if c["type"] in (10, 11, 12):
            stats["skips"].append(f"thread #{c['name']}")
            continue
        new = None
        try:
            new = d.req("POST", f"/guilds/{dst_id}/channels", json=build_payload(c))
        except DapiError as e:
            es = str(e)
            degrade = {5: 0, 13: 2, 15: 0, 16: 5}.get(c["type"])
            if degrade is not None:
                log(f"     [~] #{c['name']}: type {c['type']} blocked, downgrading to {degrade}")
                try:
                    new = d.req("POST", f"/guilds/{dst_id}/channels",
                                json=build_payload(c, as_type=degrade))
                except DapiError as e2:
                    es = str(e2)
            if new is None and ("bitrate" in es.lower() or "50035" in es):
                p = build_payload(c, as_type=degrade or c["type"])
                p.pop("bitrate", None)
                p.pop("rtc_region", None)
                p.pop("video_quality_mode", None)
                try:
                    new = d.req("POST", f"/guilds/{dst_id}/channels", json=p)
                    log(f"     [~] #{c['name']}: created without bitrate/region")
                except DapiError as e3:
                    es = str(e3)
            if new is None and ("permission_overwrites" in es or "overwrites" in es):
                p = build_payload(c, as_type=degrade or c["type"])
                p.pop("permission_overwrites", None)
                try:
                    new = d.req("POST", f"/guilds/{dst_id}/channels", json=p)
                    log(f"     [~] #{c['name']}: created without overwrites")
                except DapiError as e4:
                    es = str(e4)
            if new is None:
                try:
                    new = d.req("POST", f"/guilds/{dst_id}/channels",
                                json={"name": c["name"][:100], "type": degrade or c["type"]})
                    log(f"     [~] #{c['name']}: created bare-minimum")
                except DapiError as e5:
                    log(f"     [X] channel #{c['name']} — {str(e5)[:180]}")
                    stats["fails"].append(f"channel #{c['name']}: {str(e5)[:180]}")
        if new:
            channel_map[c["id"]] = new["id"]
            ch_created += 1
    log(f"     channels created: {ch_created}")

    log("5/10  Name, icon, banner...")
    def set_asset(field, url_fmt, srcv):
        if not srcv:
            return
        def do():
            uri = data_uri(url_fmt.format(h=srcv))
            d.req("PATCH", f"/guilds/{dst_id}", json={field: uri})
            log(f"     {field} [OK]")
        safe(field, do, stats)

    ext_i = "gif" if (src.get("icon") or "").startswith("a_") else "png"
    set_asset("icon", "https://cdn.discordapp.com/icons/" + src_id + "/{h}." + ext_i + "?size=1024",
              src.get("icon"))
    ext_b = "gif" if (src.get("banner") or "").startswith("a_") else "png"
    set_asset("banner", "https://cdn.discordapp.com/banners/" + src_id + "/{h}." + ext_b + "?size=2048",
              src.get("banner"))
    set_asset("splash", "https://cdn.discordapp.com/splashes/" + src_id + "/{h}.png?size=2048",
              src.get("splash"))
    set_asset("discovery_splash",
              "https://cdn.discordapp.com/discovery-splashes/" + src_id + "/{h}.png?size=2048",
              src.get("discovery_splash"))

    log("6/10  Server settings...")
    full = {
        "name": src["name"][:100],
        "verification_level": src.get("verification_level", 0),
        "default_message_notifications": src.get("default_message_notifications", 0),
        "explicit_content_filter": src.get("explicit_content_filter", 0),
        "preferred_locale": src.get("preferred_locale", "en-US"),
        "afk_timeout": src.get("afk_timeout", 300),
        "system_channel_flags": src.get("system_channel_flags", 0),
        "premium_progress_bar_enabled": bool(src.get("premium_progress_bar_enabled")),
    }
    if src.get("description") is not None:
        full["description"] = src["description"]
    for f in ("afk_channel_id", "system_channel_id", "rules_channel_id", "public_updates_channel_id"):
        v = src.get(f)
        if v and v in channel_map:
            full[f] = channel_map[v]

    mini = {k: full[k] for k in ("name", "verification_level",
            "default_message_notifications", "explicit_content_filter",
            "afk_timeout", "system_channel_flags") if k in full}
    if "system_channel_id" in full:
        mini["system_channel_id"] = full["system_channel_id"]
    for pl, label in ((full, "full settings"), (mini, "core settings"),
                      ({"name": src["name"][:100]}, "name only")):
        try:
            d.req("PATCH", f"/guilds/{dst_id}", json=pl)
            log(f"     settings applied ({label}) [OK]")
            break
        except DapiError as e:
            log(f"     [~] {label} rejected: {str(e)[:140]}")
    else:
        stats["fails"].append("guild settings patch failed at all levels")

    if opts.get("features") and src.get("features"):
        feats = [f for f in src["features"] if f in COPYABLE_FEATURES]
        for f in feats:
            try:
                d.req("PATCH", f"/guilds/{dst_id}", json={"features": [f]})
                log(f"     feature enabled: {f} [OK]")
            except DapiError as e:
                log(f"     [~] feature {f} rejected ({str(e)[:100]})")
                stats["fails"].append(f"feature {f}")

    log("7/10  Emojis & stickers...")
    if opts.get("emojis") and max_e > 0:
        emojis = safe("list emojis", lambda: d.req("GET", f"/guilds/{src_id}/emojis"), stats) or []
        usable = [e for e in emojis if e.get("available")][:max_e]
        skipped = len([e for e in emojis if e.get("available")]) - len(usable)
        if skipped > 0:
            log(f"     {skipped} emojis skipped (tier limit)")

        def upload_emoji(e):
            ex = "gif" if e.get("animated") else "png"
            uri = data_uri(f"https://cdn.discordapp.com/emojis/{e['id']}.{ex}?size=64&quality=lossless")
            d.req("POST", f"/guilds/{dst_id}/emojis", json={
                "name": e["name"][:32], "image": uri,
                "roles": [role_map[r] for r in e.get("roles", []) if r in role_map]})
            return e["name"]

        ok, failed = [], []
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(upload_emoji, e): e for e in usable}
            for f in as_completed(futures):
                try:
                    ok.append(f.result())
                except Exception as ex:
                    failed.append((futures[f]["name"], str(ex)[:160]))
        for name, err in failed:
            stats["fails"].append(f"emoji {name}: {err}")
            log(f"     [X] emoji {name} — {err}")
        log(f"     emojis uploaded: {len(ok)}/{len(usable)}")
    elif opts.get("emojis"):
        log("     destination has no emoji slots (Tier 0), skipping")

    if opts.get("stickers") and max_s > 0:
        stickers = safe("list stickers", lambda: d.req("GET", f"/guilds/{src_id}/stickers"), stats) or []
        usable = stickers[:max_s]
        skipped = len(stickers) - len(usable)
        if skipped > 0:
            log(f"     {skipped} stickers skipped (tier limit)")

        def upload_sticker(s):
            r = requests.get(f"https://media.discordapp.net/stickers/{s['id']}.png?size=320",
                             headers={"User-Agent": UA}, timeout=120)
            r.raise_for_status()
            d.req("POST", f"/guilds/{dst_id}/stickers",
                  data={"name": s["name"][:30],
                        "tags": (s.get("tags") or "sticker")[:20],
                        "description": (s.get("description") or "")[:100]},
                  files={"files[0]": ("sticker.png", r.content, "image/png")})
            return s["name"]

        ok, failed = [], []
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(upload_sticker, s): s for s in usable}
            for f in as_completed(futures):
                try:
                    ok.append(f.result())
                except Exception as ex:
                    failed.append((futures[f]["name"], str(ex)[:160]))
        for name, err in failed:
            stats["fails"].append(f"sticker {name}: {err}")
            log(f"     [X] sticker {name} — {err}")
        log(f"     stickers uploaded: {len(ok)}/{len(usable)}")

    log("8/10  Welcome screen, widget, events, bans...")
    if opts.get("welcome"):
        ws = safe("welcome screen fetch", lambda: d.req("GET", f"/guilds/{src_id}/welcome-screen"), stats)
        if ws:
            wc = [{"channel_id": channel_map[c["channel_id"]],
                   "description": c.get("description", ""),
                   "emoji_id": None,
                   "emoji_name": c.get("emoji_name") or None}
                  for c in ws.get("welcome_channels", [])
                  if c["channel_id"] in channel_map]
            ok = safe("welcome screen", lambda: d.req(
                "PATCH", f"/guilds/{dst_id}/welcome-screen",
                json={"enabled": ws.get("enabled", True),
                      "description": ws.get("description") or "",
                      "welcome_channels": wc}), stats)
            if ok is None:
                safe("welcome screen (simple)", lambda: d.req(
                    "PATCH", f"/guilds/{dst_id}/welcome-screen",
                    json={"enabled": ws.get("enabled", True),
                          "description": ws.get("description") or ""}), stats)

    if opts.get("widget"):
        w = safe("widget fetch", lambda: d.req("GET", f"/guilds/{src_id}/widget"), stats)
        if w:
            safe("widget", lambda: d.req(
                "PATCH", f"/guilds/{dst_id}/widget",
                json={"enabled": bool(w.get("enabled")),
                      "channel_id": channel_map.get(w.get("channel_id"))}), stats)

    if opts.get("events"):
        evs = safe("events fetch", lambda: d.req(
            "GET", f"/guilds/{src_id}/scheduled-events?with_user_count=false"), stats) or []
        for e in evs:
            if e.get("status") in ("COMPLETED", "CANCELED"):
                continue
            p = {"name": e["name"][:100], "description": e.get("description") or "",
                 "privacy_level": e["privacy_level"],
                 "scheduled_start_time": e["scheduled_start_time"],
                 "entity_type": e["entity_type"]}
            if e.get("scheduled_end_time"):
                p["scheduled_end_time"] = e["scheduled_end_time"]
            if e["entity_type"] == 3:
                p["entity_metadata"] = {"location": (e.get("entity_metadata") or {}).get("location", "")}
            elif e.get("channel_id") in channel_map:
                p["channel_id"] = channel_map[e["channel_id"]]
            else:
                continue
            ok = safe(f"event {e['name']}", lambda p=p: d.req(
                "POST", f"/guilds/{dst_id}/scheduled-events", json=p), stats)
            if ok is None:
                safe(f"event {e['name']} (as external)", lambda e=e, p=p: d.req(
                    "POST", f"/guilds/{dst_id}/scheduled-events",
                    json={"name": p["name"], "description": p["description"],
                          "privacy_level": 2, "entity_type": 3,
                          "scheduled_start_time": p["scheduled_start_time"],
                          "entity_metadata": {"location": "Copied event"}}), stats)

    log("9/10  Bans...")
    if opts.get("bans"):
        bans, after = [], None
        while True:
            try:
                q = "?limit=1000" + (f"&after={after}" if after else "")
                batch = d.req("GET", f"/guilds/{src_id}/bans{q}") or []
            except DapiError as e:
                log(f"     [X] cannot read bans: {str(e)[:140]}")
                stats["fails"].append("bans fetch")
                break
            bans += batch
            if len(batch) < 1000:
                break
            after = batch[-1]["user"]["id"]
        b_ok = 0
        for b in bans:
            if safe(f"ban {b['user']['id']}",
                    lambda b=b: d.req("PUT", f"/guilds/{dst_id}/bans/{b['user']['id']}",
                                      json={"delete_message_seconds": 0}), stats) is not None:
                b_ok += 1
        log(f"     bans copied: {b_ok}/{len(bans)}")

    log("10/10  Done.")
    log(f"     roles: {len(role_map)} | channels: {ch_created}")
    if stats["skips"]:
        log("     skipped: " + ", ".join(stats["skips"][:10]) + (" ..." if len(stats["skips"]) > 10 else ""))
    if stats["fails"]:
        log(f"     errors ({len(stats['fails'])}):")
        for f in stats["fails"][:25]:
            log("       - " + f[:220])
    else:
        log("     no errors [OK]")
    log("     NOT copied: messages, members, bots, threads, invites, boosts, vanity URL.")

def run_job(src, dst, token, opts):
    try:
        copy_guild(src, dst, token, opts)
    except Exception as e:
        log("FATAL: " + str(e))
        for line in traceback.format_exc().splitlines()[-8:]:
            log("   " + line)
    finally:
        with LOCK:
            STATE["running"] = False
            STATE["done"] = True

def start_copy(data):
    src = str(data.get("src", "")).strip()
    dst = str(data.get("dst", "")).strip()
    token = str(data.get("token", "")).strip()
    opts = data.get("opts") or {}
    if not (src.isdigit() and dst.isdigit()):
        return {"error": "Server IDs must be numbers."}
    if src == dst:
        return {"error": "Source and destination must be different."}
    if len(token) < 20:
        return {"error": "Token looks too short."}
    with LOCK:
        if STATE["running"]:
            return {"error": "A copy is already running."}
        STATE["log"].clear()
        STATE["running"] = True
        STATE["done"] = False
    threading.Thread(target=run_job, args=(src, dst, token, opts), daemon=True).start()
    return {"ok": True}

PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Discord Copier — Ultimate</title><style>
:root{color-scheme:dark}
*{box-sizing:border-box}
body{font-family:'Segoe UI',system-ui,sans-serif;background:radial-gradient(1200px 700px at 70% -10%,#2a0a3d 0%,#0d0116 55%,#070109 100%);color:#e6d9ff;margin:0;min-height:100vh;padding:32px 20px}
.wrap{max-width:860px;margin:0 auto}
h1{font-size:26px;margin:0 0 6px;background:linear-gradient(90deg,#a855f7,#d946ef,#7c3aed);-webkit-background-clip:text;background-clip:text;color:transparent;letter-spacing:.5px}
p.sub{color:#9b8ab8;font-size:13px;margin:0 0 26px}
.card{background:rgba(30,10,50,.55);border:1px solid rgba(168,85,247,.25);border-radius:14px;padding:24px;backdrop-filter:blur(10px);box-shadow:0 0 40px rgba(168,85,247,.08)}
label.fl{display:block;font-size:11px;text-transform:uppercase;letter-spacing:.12em;color:#a855f7;margin:16px 0 6px;font-weight:600}
input[type=text],input[type=password]{width:100%;background:#120521;border:1px solid rgba(168,85,247,.35);border-radius:8px;padding:11px 14px;color:#f0e6ff;font-size:14px;font-family:ui-monospace,Consolas,monospace;transition:border .2s,box-shadow .2s}
input:focus{outline:none;border-color:#c084fc;box-shadow:0 0 12px rgba(168,85,247,.4)}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:8px 22px;margin:20px 0 6px}
.grid label{display:flex;align-items:center;gap:9px;font-size:13.5px;color:#c9b8e8;cursor:pointer}
.grid input[type=checkbox]{accent-color:#a855f7;width:16px;height:16px;cursor:pointer}
.btn{background:linear-gradient(90deg,#7c3aed,#a855f7,#d946ef);color:#fff;border:0;border-radius:10px;padding:13px;font-size:15.5px;font-weight:700;cursor:pointer;width:100%;margin-top:20px;letter-spacing:.4px;box-shadow:0 4px 24px rgba(168,85,247,.35);transition:transform .15s,box-shadow .15s}
.btn:hover:not(:disabled){transform:translateY(-1px);box-shadow:0 6px 30px rgba(168,85,247,.55)}
.btn:disabled{opacity:.45;cursor:default;box-shadow:none}
pre{background:#0a0214;border:1px solid rgba(168,85,247,.2);border-radius:10px;padding:14px;height:440px;overflow:auto;font-size:12.5px;line-height:1.6;white-space:pre-wrap;margin-top:22px;font-family:ui-monospace,Consolas,monospace;color:#c4b0e6}
pre::-webkit-scrollbar{width:8px}pre::-webkit-scrollbar-thumb{background:#4c1d95;border-radius:4px}
.warn{margin-top:14px;font-size:12px;color:#8b7bab}
</style></head><body><div class="wrap">
<h1>&#9889; Discord Copier — ULTIMATE</h1>
<p class="sub">Wipes the destination server, then clones everything from the source. Token account must be in BOTH servers with Admin on destination.</p>
<div class="card">
<label class="fl">Source server ID (copy FROM)</label>
<input id="src" type="text" placeholder="123456789012345678" autocomplete="off">
<label class="fl">Destination server ID (copy TO)</label>
<input id="dst" type="text" placeholder="987654321098765432" autocomplete="off">
<label class="fl">User token</label>
<input id="token" type="password" placeholder="MTIzNDU2..." autocomplete="off">
<div class="grid">
 <label><input type="checkbox" id="wipe_roles" checked> Wipe existing roles</label>
 <label><input type="checkbox" id="wipe_channels" checked> Wipe existing channels</label>
 <label><input type="checkbox" id="wipe_emoji" checked> Wipe existing emojis</label>
 <label><input type="checkbox" id="wipe_stickers" checked> Wipe existing stickers</label>
 <label><input type="checkbox" id="emojis" checked> Copy emojis</label>
 <label><input type="checkbox" id="stickers" checked> Copy stickers</label>
 <label><input type="checkbox" id="welcome" checked> Welcome screen</label>
 <label><input type="checkbox" id="widget" checked> Widget</label>
 <label><input type="checkbox" id="events" checked> Scheduled events</label>
 <label><input type="checkbox" id="bans" checked> Bans</label>
 <label><input type="checkbox" id="features"> Guild features (Community/News)</label>
 <label><input type="checkbox" id="member_ow"> Member overwrites (user IDs)</label>
</div>
<button class="btn" id="go">&#9889; START COPY</button>
<pre id="out">Ready. The destination will be wiped before copying — make sure the ID is correct.</pre>
<p class="warn">Destroys everything currently in the destination (roles/channels/emojis/stickers) based on your checkboxes.</p>
</div></div><script>
const out=document.getElementById('out'),go=document.getElementById('go');
let cursor=0,timer=null;
function poll(){
 fetch('/log?from='+cursor).then(r=>r.json()).then(d=>{
  if(d.lines.length){out.textContent+=(out.textContent?'\n':'')+d.lines.join('\n');out.scrollTop=out.scrollHeight;}
  cursor=d.from;
  if(d.done){go.disabled=false;go.innerHTML='&#9889; RUN AGAIN';clearInterval(timer);timer=null;}
 }).catch(()=>{});
}
go.onclick=function(){
 const g=id=>document.getElementById(id);
 const b={src:g('src').value.trim(),dst:g('dst').value.trim(),token:g('token').value.trim(),
  opts:Object.fromEntries(['wipe_roles','wipe_channels','wipe_emoji','wipe_stickers',
   'emojis','stickers','welcome','widget','events','bans','features','member_ow']
   .map(id=>[id,g(id).checked]))};
 go.disabled=true;go.innerHTML='&#9668; RUNNING...';out.textContent='';
 fetch('/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)})
  .then(r=>r.json()).then(d=>{
   if(d.error){out.textContent='Error: '+d.error;go.disabled=false;go.textContent='START COPY';return;}
   cursor=0;if(!timer)timer=setInterval(poll,700);
  }).catch(e=>{out.textContent='Error: '+e;go.disabled=false;go.textContent='START COPY';});
};
</script></body></html>"""

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.startswith("/log"):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            start = int(q.get("from", ["0"])[0])
            with LOCK:
                lines = STATE["log"][start:]
                payload = {"lines": lines, "from": len(STATE["log"]),
                           "running": STATE["running"], "done": STATE["done"]}
            self._send(200, json.dumps(payload), "application/json; charset=utf-8")
        else:
            self._send(200, PAGE, "text/html; charset=utf-8")

    def do_POST(self):
        if self.path == "/start":
            try:
                n = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                data = {}
            self._send(200, json.dumps(start_copy(data)), "application/json")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()

    port = a.port
    for _ in range(20):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            port += 1
    else:
        sys.exit("No free port found.")

    url = f"http://127.0.0.1:{port}"
    print(f"Discord Copier ULTIMATE -> {url}   (Ctrl+C to quit)")
    if not a.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()