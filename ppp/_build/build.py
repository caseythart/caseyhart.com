#!/usr/bin/env python3
"""Build the Philosophy, Programs, and Prompts pages from the show's knowledge graph.

    pip install rdflib jinja2        # once
    python3 ppp/_build/build.py      # from the repo root, after editing the .ttl

See ppp/_build/README.md for what to edit and run.

Reads   ppp/podcast_ontology_data.ttl
Runs    the SPARQL queries in ppp/_build/queries/
Writes  ppp/index.html, ppp/episode/<number>/index.html, ppp/sources/index.html

Everything under ppp/_build/ starts with an underscore, so GitHub Pages (Jekyll) does not publish it.
Edit the templates in ppp/_build/templates/, not the generated HTML.
"""
import datetime
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import rdflib
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

HERE = Path(__file__).resolve().parent
PPP = HERE.parent
DATA = PPP / "podcast_ontology_data.ttl"
SITE = "https://caseyhart.com/ppp/"
DENSE = "https://caseyhart.com/ontology/dense/"


# ---------- reading the graph ----------

def load_graph():
    g = rdflib.Graph()
    g.parse(DATA, format="turtle")
    return g


def query(g, name):
    """Run queries/<name>.rq and return rows as dicts of plain Python values."""
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


# ---------- formatting helpers (used by the templates) ----------

def fmt_length(secs):
    h, m = divmod(round(secs / 60), 60)
    return f"{h}h {m}m" if h else f"{m}m"


def fmt_clock(secs):
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_date(d):
    return f"{d:%b} {d.day}, {d.year}" if d else ""


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


# ---------- assembling the data the pages need ----------

def assemble(g):
    problems = []

    show = (query(g, "show") or [{}])[0]
    hosts = {str(r["person"]): {"name": f'{r["first"]} {r["last"]}', "page": r["page"]} for r in query(g, "hosts")}

    episodes = []
    for r in query(g, "episodes"):
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

    for r in query(g, "segments"):
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
    for r in query(g, "cites"):
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

    for r in query(g, "related"):
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


def as_script(data):
    return Markup(json.dumps(data, ensure_ascii=False, indent=2).replace("</", "<\\/"))


# ---------- writing the pages ----------

def main():
    g = load_graph()
    show, hosts, episodes, sources, problems = assemble(g)
    if problems:
        print("Problems in the data:\n  " + "\n  ".join(problems))
        sys.exit(1)

    env = Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=select_autoescape(["html"]),
                      trim_blocks=True, lstrip_blocks=True)
    env.filters.update(hm=fmt_length, clock=fmt_clock, date=fmt_date, domain=domain, paragraphs=paragraphs)
    common = {"show": show, "hosts": hosts, "episodes": episodes, "sources": sources,
              "latest": episodes[-1], "data_file": DATA.name}

    def write(path, template, **ctx):
        path.parent.mkdir(parents=True, exist_ok=True)
        html = env.get_template(template).render(**common, **ctx)
        path.write_text(html, encoding="utf-8")

    write(PPP / "index.html", "index.html", root="", jsonld=as_script(series_jsonld(show, hosts)))
    for ep in episodes:
        write(PPP / "episode" / ep["slug"] / "index.html", "episode.html", root="../../", ep=ep,
              jsonld=as_script(episode_jsonld(ep)))
    write(PPP / "sources" / "index.html", "sources.html", root="../")

    known = {e["slug"] for e in episodes}
    stale = [p.name for p in (PPP / "episode").iterdir() if p.is_dir() and p.name not in known]
    n_cites = sum(len(e["sources"]) for e in episodes)
    print(f"Built {len(episodes)} episode pages, the landing page and the sources page "
          f"({len(sources)} sources, {n_cites} citations) from {len(g)} triples.")
    if stale:
        print("Old episode folders no longer in the data (delete them if that's right): " + ", ".join(sorted(stale)))


if __name__ == "__main__":
    main()
