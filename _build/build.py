#!/usr/bin/env python3
"""Build the show pages on caseyhart.com from the shows' knowledge graphs.

    pip install -r _build/requirements.txt     # once
    python3 _build/build.py                    # from the repo root, after editing either .ttl

Reads   ppp/podcast_ontology_data.ttl                       (Philosophy, Programs, and Prompts)
        ontology-explained/ontology_explained_data.ttl      (Ontology Explained)
        into one graph, so a source cited by both shows is one node.
Runs    the SPARQL queries in _build/queries/{ppp,oe,shared}/
Writes  ppp/index.html, ppp/episode/<number>/index.html, ppp/sources/index.html
        ontology-explained/index.html, ontology-explained/<video-slug>/index.html,
        ontology-explained/sources/index.html

_build/ starts with an underscore, so GitHub Pages (Jekyll) does not publish it.
Edit the templates in _build/templates/, not the generated HTML. See _build/README.md.
"""
import datetime
import json
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

import rdflib
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PPP = ROOT / "ppp"
OE = ROOT / "ontology-explained"
DATA = PPP / "podcast_ontology_data.ttl"
OE_DATA = OE / "ontology_explained_data.ttl"
SITE = "https://caseyhart.com/ppp/"
OE_SITE = "https://caseyhart.com/ontology-explained/"
DENSE = "https://caseyhart.com/ontology/dense/"
OE_RESERVED = {"sources"}                 # folder names under ontology-explained/ that aren't videos


# ---------- reading the graph ----------

def load_graph():
    g = rdflib.Graph()
    g.parse(DATA, format="turtle")
    g.parse(OE_DATA, format="turtle")
    return g


def query(g, name):
    """Run queries/<name>.rq (e.g. "ppp/episodes") and return rows as dicts of plain Python values."""
    result = g.query((HERE / "queries" / f"{name}.rq").read_text())
    names = [str(v) for v in result.vars]
    rows = []
    for r in result:
        rows.append({k: (None if v is None else (v if isinstance(v, rdflib.URIRef) else v.toPython()))
                     for k, v in zip(names, r)})
    return rows


def seconds(d):
    """xsd:duration -> whole seconds (rdflib gives timedelta or isodate.Duration)."""
    if isinstance(d, datetime.timedelta):
        return int(d.total_seconds())
    return int(d.tdelta.total_seconds())


def local(iri):
    return str(iri)[len(DENSE):] if str(iri).startswith(DENSE) else str(iri)


def slugify(text, limit=60):
    """'Newcomb’s Problem Explained: One Box Or Two?' -> 'newcombs-problem-explained-one-box-or-two'."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"['’]", "", text.lower())
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    if len(text) > limit:
        text = text[:limit].rsplit("-", 1)[0]
    return text


# ---------- formatting helpers (used by the templates) ----------

def fmt_length(secs):
    h, m = divmod(round(secs / 60), 60)
    return f"{h}h {m}m" if h else f"{m}m"


def fmt_minutes(secs):
    """Length in words for the Ontology Explained pages: '10 min', '1 hr 11 min'."""
    h, m = divmod(max(1, round(secs / 60)), 60)
    return (f"{h} hr {m} min" if m else f"{h} hr") if h else f"{m} min"


def fmt_clock(secs):
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_date(d):
    return f"{d:%b} {d.day}, {d.year}" if d else ""


def fmt_day(d):
    return f"{d:%b} {d.day}" if d else ""


def fmt_month(d):
    return f"{d:%B} {d.year}" if d else ""


def fmt_number(n):
    """Decimal('6.10') -> '6.1', Decimal('3') -> '3'."""
    s = format(n.normalize(), "f")
    return s


def domain(url):
    m = re.match(r"https?://(?:www\.)?([^/]+)", url or "")
    return m.group(1) if m else ""


def youtube_id(url):
    m = re.search(r"[?&]v=([\w-]{11})", url or "")
    return m.group(1) if m else None


def paragraphs(text):
    """Description text -> list of ('p', line) / ('ul', [items]) blocks."""
    blocks = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(("- ", "• ", "* ")):
            if not blocks or blocks[-1][0] != "ul":
                blocks.append(("ul", []))
            blocks[-1][1].append(line[2:].strip())
        else:
            blocks.append(("p", line))
    return blocks


def first_paragraph(text):
    return next((b[1] for b in paragraphs(text) if b[0] == "p" and not b[1].startswith(('"', "“"))), "")


# ---------- Philosophy, Programs, and Prompts ----------

def assemble(g):
    problems = []

    show = (query(g, "ppp/show") or [{}])[0]
    hosts = {str(r["person"]): {"name": f'{r["first"]} {r["last"]}', "page": r["page"]} for r in query(g, "ppp/hosts")}

    episodes = []
    for r in query(g, "ppp/episodes"):
        num = fmt_number(r["number"])
        ep = {
            "iri": str(r["ep"]), "number": num, "slug": num, "name": r["name"],
            "date": r["date"], "length": seconds(r["duration"]) if r["duration"] is not None else None,
            "youtube": r["youtube"], "spotify": r["spotify"], "apple": r["apple"],
            "youtube_id": youtube_id(r["youtube"]), "description": r["description"] or "",
            "chapters": [], "sources": [], "cited_by": [], "related": [],
        }
        for field, what in (("date", "release date (:releaseDate)"), ("length", "duration (:hasDuration)"),
                            ("youtube_id", "YouTube link of the form https://www.youtube.com/watch?v=<11-character id>")):
            if not ep[field]:
                problems.append(f"Episode {num} has no {what}")
        episodes.append(ep)
    by_iri = {e["iri"]: e for e in episodes}

    for r in query(g, "ppp/segments"):
        ep = by_iri[str(r["ep"])]
        ep["chapters"].append({"start": seconds(r["start"]), "name": r["name"]})
    for ep in episodes:
        ep["chapters"].sort(key=lambda c: c["start"])
        for c in ep["chapters"]:
            if ep["length"] and c["start"] >= ep["length"]:
                problems.append(f"Episode {ep['number']}: chapter '{c['name']}' starts after the episode ends")

    # sources: one entry per IRI, collecting every link, type and citing episode
    sources = {}
    cites = defaultdict(list)                       # episode iri -> [source iri] in a stable order
    for r in query(g, "ppp/cites"):
        e_iri, s_iri = str(r["ep"]), str(r["src"])
        if s_iri in by_iri:                         # one episode citing another
            citer = by_iri[e_iri]
            if citer not in by_iri[s_iri]["cited_by"]:
                by_iri[s_iri]["cited_by"].append(citer)
            if s_iri not in cites[e_iri]:
                cites[e_iri].append(s_iri)
            continue
        s = sources.setdefault(s_iri, {"iri": s_iri, "id": local(s_iri).lower(), "name": None, "kinds": set(),
                                       "links": [], "by": set(), "episodes": []})
        s["name"] = s["name"] or r["name"]
        if r["kind"]:
            s["kinds"].add(local(r["kind"]))
        for link in (r["youtube"], r["uri"]):
            if link and link not in s["links"]:
                s["links"].append(link)
        if r["creator"] and str(r["creator"]) in hosts:
            s["by"].add(hosts[str(r["creator"])]["name"].split()[0])
        if by_iri[e_iri] not in s["episodes"]:
            s["episodes"].append(by_iri[e_iri])
        if s_iri not in cites[e_iri]:
            cites[e_iri].append(s_iri)

    order = {e["iri"]: i for i, e in enumerate(episodes)}
    for s in sources.values():
        s["episodes"].sort(key=lambda e: order[e["iri"]])
        s["kind"] = "Video" if "Video" in s["kinds"] else "Link"
        s["url"] = s["links"][0] if s["links"] else None
        if not s["name"]:
            problems.append(f"Source {local(s['iri'])} has no name")

    for ep in episodes:
        items = []
        for s_iri in cites[ep["iri"]]:
            if s_iri in by_iri:
                items.append({"episode": by_iri[s_iri]})
            else:
                s = sources[s_iri]
                items.append({"source": s, "elsewhere": [e for e in s["episodes"] if e is not ep]})
        # RDF has no order, so: other episodes of the show first, then sources alphabetically
        ep["sources"] = sorted(items, key=lambda i: (0, i["episode"]["number"]) if "episode" in i
                               else (1, (i["source"]["name"] or "").lower()))
        ep["cited_by"].sort(key=lambda e: order[e["iri"]])

    for r in query(g, "ppp/related"):
        ep, other = by_iri[str(r["ep"])], by_iri[str(r["other"])]
        names = sorted(n for n in (r["names"] or "").split("|") if n)
        ep["related"].append({"episode": other, "shared": int(r["shared"]), "names": names})
    for ep in episodes:
        ep["related"].sort(key=lambda x: (-x["shared"], order[x["episode"]["iri"]]))

    for i, ep in enumerate(episodes):
        ep["prev"] = episodes[i - 1] if i > 0 else None
        ep["next"] = episodes[i + 1] if i + 1 < len(episodes) else None

    source_list = sorted(sources.values(),
                         key=lambda s: (-len(s["episodes"]), order[s["episodes"][0]["iri"]], (s["name"] or "").lower()))
    return show, hosts, episodes, source_list, problems


# ---------- Ontology Explained ----------

def assemble_oe(g, problems):
    channel = (query(g, "oe/channel") or [{}])[0]

    playlists = {}
    for r in query(g, "oe/playlists"):
        playlists[str(r["playlist"])] = {
            "iri": str(r["playlist"]), "name": r["name"], "slug": "series-" + slugify(r["name"]),
            "description": r["description"] or "", "youtube": r["youtube"], "videos": [],
        }

    videos, taken = [], set(OE_RESERVED)
    for r in query(g, "oe/videos"):
        base = slugify(r["name"]) or (youtube_id(r["youtube"]) or "video").lower().lstrip("_-")
        slug, n = base, 2
        while slug in taken:                        # two videos with the same title
            slug, n = f"{base}-{n}", n + 1
        taken.add(slug)
        v = {
            "iri": str(r["video"]), "name": r["name"], "slug": slug,
            "date": r["date"], "time": r["time"],
            "length": seconds(r["duration"]) if r["duration"] is not None else None,
            "youtube": r["youtube"], "youtube_id": youtube_id(r["youtube"]), "description": r["description"] or "",
            "chapters": [], "playlists": [], "positions": [], "sources": [], "cited_by": [], "related": [],
        }
        for field, what in (("date", "release date (:releaseDate/:universalDate)"), ("length", "duration (:hasDuration)"),
                            ("youtube_id", "YouTube link of the form https://www.youtube.com/watch?v=<11-character id>")):
            if not v[field]:
                problems.append(f"Video '{v['name']}' has no {what}")
        videos.append(v)
    # oldest first; same-day uploads by upload time when the data has it
    videos.sort(key=lambda v: (v["date"] or datetime.date.min, str(v["time"] or ""), v["name"]))
    by_iri = {v["iri"]: v for v in videos}
    order = {v["iri"]: i for i, v in enumerate(videos)}

    for r in query(g, "oe/membership"):
        v, pl = by_iri.get(str(r["video"])), playlists.get(str(r["playlist"]))
        if v and pl and pl not in v["playlists"]:
            v["playlists"].append(pl)
            pl["videos"].append(v)
    for pl in playlists.values():
        pl["videos"].sort(key=lambda v: order[v["iri"]])
        pl["length"] = sum(v["length"] or 0 for v in pl["videos"])
        for i, v in enumerate(pl["videos"]):
            v["positions"].append({"playlist": pl, "index": i + 1, "count": len(pl["videos"]),
                                   "prev": pl["videos"][i - 1] if i else None,
                                   "next": pl["videos"][i + 1] if i + 1 < len(pl["videos"]) else None})
    for v in videos:
        v["playlists"].sort(key=lambda pl: pl["name"])
        v["positions"].sort(key=lambda p: p["playlist"]["name"])

    for r in query(g, "oe/segments"):
        v = by_iri[str(r["video"])]
        v["chapters"].append({"start": seconds(r["start"]), "name": r["name"]})
    for v in videos:
        v["chapters"].sort(key=lambda c: c["start"])
        for c in v["chapters"]:
            if v["length"] and c["start"] >= v["length"]:
                problems.append(f"Video '{v['name']}': chapter '{c['name']}' starts after the video ends")

    for i, v in enumerate(videos):
        v["prev"] = videos[i - 1] if i > 0 else None
        v["next"] = videos[i + 1] if i + 1 < len(videos) else None

    # newest activity first, so the series in progress are at the top
    pl_list = sorted((p for p in playlists.values() if p["videos"]),
                     key=lambda p: (-order[p["videos"][-1]["iri"]], p["name"]))
    return channel, pl_list, videos


# ---------- links between the two shows ----------

def connect(g, hosts, episodes, ppp_sources, videos, playlists, problems):
    """Citations and shared sources across PPP and Ontology Explained, for both shows' pages."""
    for ep in episodes:
        ep.update(show="ppp", path=f"ppp/episode/{ep['slug']}/", title=f"PPP Episode {ep['number']}: {ep['name']}")
    for v in videos:
        v.update(show="oe", path=f"ontology-explained/{v['slug']}/", title=v["name"])
    for pl in playlists:
        pl.update(show="playlist", path=f"ontology-explained/#{pl['slug']}", title=f"{pl['name']} (series)")
    pages = {x["iri"]: x for x in episodes + videos + playlists}
    rank = {x["iri"]: (0 if x["show"] == "ppp" else 1, i) for i, x in enumerate(episodes + videos)}

    citers = defaultdict(list)                     # cited iri -> episodes/videos that cite it
    oe_sources = {}
    oe_cites = defaultdict(list)                   # video iri -> cited iris, in a stable order
    for r in query(g, "shared/cites"):
        citer, s_iri = pages[str(r["citer"])], str(r["src"])
        if citer not in citers[s_iri]:
            citers[s_iri].append(citer)
        if citer["show"] != "oe":
            continue
        if s_iri not in oe_cites[citer["iri"]]:
            oe_cites[citer["iri"]].append(s_iri)
        target = pages.get(s_iri)
        if target and target["show"] in ("oe", "playlist"):
            continue                               # our own videos and series aren't "sources"
        s = oe_sources.setdefault(s_iri, {"iri": s_iri, "id": local(s_iri).lower(), "name": None, "kinds": set(),
                                          "links": [], "by": set(), "page": target})
        s["name"] = s["name"] or (target["title"] if target else r["name"])
        if r["kind"]:
            s["kinds"].add(local(r["kind"]))
        for link in (r["youtube"], r["uri"]):
            if link and link not in s["links"]:
                s["links"].append(link)
        if r["creator"] and str(r["creator"]) in hosts:
            s["by"].add(hosts[str(r["creator"])]["name"].split()[0])
    for lst in citers.values():
        lst.sort(key=lambda x: rank[x["iri"]])

    # PPP pages: an Ontology Explained video cited on PPP links to its page here
    for s in ppp_sources:
        page = pages.get(s["iri"])
        s["page"] = page if page and page["show"] == "oe" else None
        s["oe"] = [c for c in citers[s["iri"]] if c["show"] == "oe"]
    for ep in episodes:
        for item in ep["sources"]:
            if "source" in item:
                item["elsewhere_oe"] = [c for c in item["source"]["oe"]]
        ep["cited_by_oe"] = [c for c in citers[ep["iri"]] if c["show"] == "oe"]
        ep["related_oe"] = []

    # Ontology Explained pages
    for s in oe_sources.values():
        s["videos"] = [c for c in citers[s["iri"]] if c["show"] == "oe"]
        s["episodes"] = [c for c in citers[s["iri"]] if c["show"] == "ppp"]
        s["url"] = s["links"][0] if s["links"] else None
        if s["page"]:
            s["kind"] = "PPP episode"
        elif "Video" in s["kinds"]:
            s["kind"] = "YouTube video" if s["url"] else "Video"
        elif s["url"] and "youtube.com" in s["url"]:
            s["kind"] = "YouTube"
        elif s["url"]:
            s["kind"] = domain(s["url"])
        else:
            s["kind"] = "No link"
        if not s["name"]:
            problems.append(f"Source {local(s['iri'])} has no name")

    for v in videos:
        items = []
        for s_iri in oe_cites[v["iri"]]:
            target = pages.get(s_iri)
            if target and target["show"] in ("oe", "playlist"):
                items.append({"page": target})
            else:
                s = oe_sources[s_iri]
                items.append({"source": s, "elsewhere": [c for c in citers[s_iri] if c is not v]})
        # our own videos and series first (oldest first), then everything else alphabetically
        v["sources"] = sorted(items, key=lambda i: (0, rank.get(i["page"]["iri"], (2, 0)), "") if "page" in i
                              else (1, (0, 0), (i["source"]["name"] or "").lower()))
        v["cited_by"] = list(citers[v["iri"]])

    for r in query(g, "shared/related"):
        a, b = pages[str(r["a"])], pages[str(r["b"])]
        if a["show"] == "ppp" and b["show"] == "ppp":
            continue                                # PPP's own related.rq covers these
        names = sorted(n for n in (r["names"] or "").split("|") if n)
        entry = {"page": b, "shared": int(r["shared"]), "names": names}
        if a["show"] == "ppp":
            a["related_oe"].append(entry)
        else:
            a["related"].append(entry)
    for ep in episodes:
        ep["related_oe"].sort(key=lambda x: (-x["shared"], -rank[x["page"]["iri"]][1]))
        ep["related_oe"] = ep["related_oe"][:4]
    for v in videos:
        skip = {c["iri"] for c in v["cited_by"]} | {i["page"]["iri"] for i in v["sources"] if "page" in i}
        skip |= {other["iri"] for pl in v["playlists"] for other in pl["videos"]}   # the series box lists these
        rel = [x for x in v["related"] if x["page"]["iri"] not in skip]
        rel.sort(key=lambda x: (-x["shared"], -rank[x["page"]["iri"]][1]))
        v["related"] = rel[:6]

    oe_source_list = sorted(oe_sources.values(),
                            key=lambda s: (-len(s["videos"]), rank[s["videos"][0]["iri"]], (s["name"] or "").lower()))

    for r in query(g, "shared/names"):
        problems.append(f"{local(r['thing'])} has more than one :name ({r['names']}). "
                        "Make them match in both .ttl files")
    return oe_source_list


# ---------- structured data for search engines ----------

def series_jsonld(show, hosts):
    return {
        "@context": "https://schema.org", "@type": "PodcastSeries",
        "name": "Philosophy, Programs, and Prompts", "alternateName": "PPP", "url": SITE,
        "description": show.get("comment"), "image": SITE + "ppp-icon.png", "inLanguage": "en",
        "webFeed": "https://anchor.fm/s/11521f410/podcast/rss",
        "author": [{"@type": "Person", "name": h["name"], **({"url": str(h["page"])} if h["page"] else {})}
                   for h in hosts.values()],
        "sameAs": [u for u in (show.get("youtube"), show.get("spotify"), show.get("apple")) if u],
    }


def episode_jsonld(ep):
    first = next((b[1] for b in paragraphs(ep["description"]) if b[0] == "p" and not b[1].startswith('"')), "")
    data = {
        "@context": "https://schema.org", "@type": "PodcastEpisode",
        "name": ep["name"], "episodeNumber": ep["number"], "url": f"{SITE}episode/{ep['slug']}/",
        "datePublished": ep["date"].isoformat() if ep["date"] else None,
        "timeRequired": f"PT{ep['length'] // 60}M" if ep["length"] else None,
        "description": first,
        "partOfSeries": {"@type": "PodcastSeries", "name": "Philosophy, Programs, and Prompts", "url": SITE},
        "sameAs": [u for u in (ep["youtube"], ep["spotify"], ep["apple"]) if u],
        "citation": [{"@type": "CreativeWork", "name": i["source"]["name"],
                      **({"url": i["source"]["url"]} if i["source"]["url"] else {})}
                     for i in ep["sources"] if "source" in i],
    }
    return {k: v for k, v in data.items() if v not in (None, "", [])}


CASEY = {"@type": "Person", "name": "Casey Hart", "url": "https://caseyhart.com/"}


def channel_jsonld(channel, videos):
    return {
        "@context": "https://schema.org", "@type": "CollectionPage",
        "name": "Ontology Explained", "url": OE_SITE, "inLanguage": "en",
        "description": channel.get("comment"), "author": CASEY,
        "image": "https://caseyhart.com/images/logo.png",
        "sameAs": [u for u in (channel.get("youtube"),) if u],
        "mainEntity": {"@type": "ItemList", "numberOfItems": len(videos),
                       "itemListElement": [{"@type": "ListItem", "position": i + 1, "url": OE_SITE + v["slug"] + "/"}
                                           for i, v in enumerate(reversed(videos))]},
    }


def video_jsonld(v):
    uploaded = v["time"] or v["date"]
    data = {
        "@context": "https://schema.org", "@type": "VideoObject",
        "name": v["name"], "url": f"{OE_SITE}{v['slug']}/", "author": CASEY,
        "description": first_paragraph(v["description"]) or v["name"],
        "thumbnailUrl": f"https://i.ytimg.com/vi/{v['youtube_id']}/hqdefault.jpg",
        "uploadDate": uploaded.isoformat() if uploaded else None,
        "duration": f"PT{v['length'] // 60}M{v['length'] % 60}S" if v["length"] else None,
        "embedUrl": f"https://www.youtube.com/embed/{v['youtube_id']}",
        "sameAs": [v["youtube"]],
        "isPartOf": [{"@type": "CreativeWorkSeries", "name": pl["name"], "url": f"{OE_SITE}#{pl['slug']}"}
                     for pl in v["playlists"]],
        "citation": [{"@type": "CreativeWork", "name": i["source"]["name"],
                      **({"url": i["source"]["url"]} if i["source"]["url"] else {})}
                     for i in v["sources"] if "source" in i],
    }
    return {k: x for k, x in data.items() if x not in (None, "", [])}


def as_script(data):
    return Markup(json.dumps(data, ensure_ascii=False, indent=2).replace("</", "<\\/"))


# ---------- writing the pages ----------

def environment(name):
    env = Environment(loader=FileSystemLoader(HERE / "templates" / name), autoescape=select_autoescape(["html"]),
                      trim_blocks=True, lstrip_blocks=True)
    env.filters.update(hm=fmt_length, clock=fmt_clock, date=fmt_date, domain=domain, paragraphs=paragraphs,
                       minutes=fmt_minutes, day=fmt_day, month=fmt_month)
    return env


def writer(env, common):
    def write(path, template, **ctx):
        path.parent.mkdir(parents=True, exist_ok=True)
        html = env.get_template(template).render(**common, **ctx)
        path.write_text(html, encoding="utf-8")
    return write


def main():
    g = load_graph()
    show, hosts, episodes, sources, problems = assemble(g)
    channel, playlists, videos = assemble_oe(g, problems)
    oe_sources = connect(g, hosts, episodes, sources, videos, playlists, problems)
    if problems:
        print("Problems in the data:\n  " + "\n  ".join(problems))
        sys.exit(1)

    # Philosophy, Programs, and Prompts
    write = writer(environment("ppp"), {"show": show, "hosts": hosts, "episodes": episodes, "sources": sources,
                                        "latest": episodes[-1], "data_file": DATA.name})
    write(PPP / "index.html", "index.html", root="", jsonld=as_script(series_jsonld(show, hosts)))
    for ep in episodes:
        write(PPP / "episode" / ep["slug"] / "index.html", "episode.html", root="../../", ep=ep,
              jsonld=as_script(episode_jsonld(ep)))
    write(PPP / "sources" / "index.html", "sources.html", root="../")

    # Ontology Explained
    by_year = defaultdict(list)
    for v in reversed(videos):
        by_year[v["date"].year].append(v)
    write = writer(environment("oe"), {
        "channel": channel, "videos": videos, "playlists": playlists, "sources": oe_sources,
        "latest": videos[-1], "data_file": OE_DATA.name,
        "hours": round(sum(v["length"] or 0 for v in videos) / 3600),
        "chaptered": sum(1 for v in videos if v["chapters"]),
    })
    write(OE / "index.html", "index.html", root="", by_year=sorted(by_year.items(), reverse=True),
          jsonld=as_script(channel_jsonld(channel, videos)))
    for v in videos:
        write(OE / v["slug"] / "index.html", "video.html", root="../", v=v, jsonld=as_script(video_jsonld(v)))
    write(OE / "sources" / "index.html", "sources.html", root="../")

    n_cites = sum(len(e["sources"]) for e in episodes)
    print(f"PPP: built {len(episodes)} episode pages, the landing page and the sources page "
          f"({len(sources)} sources, {n_cites} citations).")
    n_cites = sum(len(v["sources"]) for v in videos)
    print(f"Ontology Explained: built {len(videos)} video pages, the landing page and the sources page "
          f"({len(oe_sources)} sources, {n_cites} citations, {len(playlists)} series).")
    print(f"Read {len(g)} triples from {DATA.relative_to(ROOT)} and {OE_DATA.relative_to(ROOT)}.")

    known = {e["slug"] for e in episodes}
    stale = [p.name for p in (PPP / "episode").iterdir() if p.is_dir() and p.name not in known]
    if stale:
        print("Old PPP episode folders no longer in the data (delete them if that's right): " + ", ".join(sorted(stale)))
    known = {v["slug"] for v in videos} | OE_RESERVED
    stale = [p.name for p in OE.iterdir() if p.is_dir() and p.name not in known]
    if stale:
        print("Old Ontology Explained video folders no longer in the data (a video was renamed or removed; "
              "delete them if that's right): " + ", ".join(sorted(stale)))


if __name__ == "__main__":
    main()
