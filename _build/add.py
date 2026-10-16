#!/usr/bin/env python3
"""Add a new PPP episode or Ontology Explained video to the data from its YouTube page, then rebuild.

    python3 _build/add.py https://www.youtube.com/watch?v=VIDEO_ID
    python3 _build/add.py https://www.youtube.com/watch?v=VIDEO_ID --spotify https://open.spotify.com/episode/...

It works out which show the video belongs to from its YouTube channel, reads the title, length,
upload date and time, and description from YouTube, and writes into that show's .ttl:

  - the episode or video, with the summary paragraphs at the top of the description as :description
  - one segment per chapter (the timestamp lines in the description)
  - a :cites for each link in the description. Sources already in either file, your own videos
    and PPP episodes are reused; new sources are minted by the README's rule
    (DOI > arXiv > ISBN > cleaned URL) and named from the description or the page itself
  - PPP only: the Apple Podcasts link (looked up on Apple) and the Spotify link (if you pass it)
  - Ontology Explained only: :partOf for each of your playlists the video is in (checked on YouTube)

Then it runs build.py. Review with `git diff`, change anything you like in the .ttl (run the build
again if you do), and commit.

Run it again with the same YouTube link to fill in a PPP episode's Spotify or Apple link once
they're up. A second run changes nothing else.

Needs yt-dlp (pip install -r _build/requirements.txt). If YouTube starts refusing it, update it:
pip install -U yt-dlp. See _build/README.md.
"""
import argparse
import datetime
import difflib
import html
import json
import re
import ssl
import subprocess
import sys
import urllib.parse
import urllib.request
import uuid
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import rdflib
from rdflib import RDF

sys.dont_write_bytecode = True                  # no __pycache__ in the repo from importing build.py
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build import DATA, OE_DATA, DENSE, ROOT, fmt_date, fmt_length, fmt_number, slugify, youtube_id  # noqa: E402

D = rdflib.Namespace(DENSE)
PACIFIC = ZoneInfo("America/Los_Angeles")      # upload times are written in Pacific time, like the rest of the file

# A cited YouTube video from one of these channels gets ":createdBy <person>". Lowercase handles or channel IDs.
# (Channels in the data with a :createdBy, like Ontology Explained's, are added to this automatically.)
PEOPLE_BY_CHANNEL = {
    "@ontologyexplained": "CaseyHart",
    "@internetofbugs": "CarlBrown",
}

# Links in a description that are never cited sources: profiles, support links, the site itself.
# (Your own videos, series and PPP episodes are recognised before these rules apply.)
NOT_SOURCES = [
    (r"^mailto:", "email address"),
    (r"//(www\.)?(patreon\.com|ko-fi\.com|buymeacoffee\.com|linktr\.ee|discord\.gg|discord\.com)\b", "support or community link"),
    (r"//(www\.)?linkedin\.com/(in|company)/", "LinkedIn profile"),
    (r"//(www\.)?(twitter\.com|x\.com|instagram\.com|tiktok\.com|threads\.net|facebook\.com|twitch\.tv)/@?[\w.]+/?$", "social profile"),
    (r"//bsky\.app/profile/[^/]+/?$", "social profile"),
    (r"//open\.spotify\.com/show/", "podcast's Spotify page"),
    (r"//podcasts\.apple\.com/.*/id\d+$", "podcast's Apple page"),
    (r"//(www\.)?caseyhart\.com\b", "this website"),
    (r"//(www\.)?internetofbugs\.com/?$", "Carl's website"),
]

# Query parameters dropped from every link (README: "no si=, utm_… or t=").
TRACKING = {"si", "t", "fbclid", "gclid", "igsh", "igshid", "mc_cid", "mc_eid", "ref_src"}

PARAGRAPH_BREAK = "\n\n"
LINK_PROPS = (D.uri, D["url-Youtube"], D["url-Spotify"], D["url-ApplePodcasts"])
PPP_SOURCES_BEFORE = "##### TOPICS"                              # new PPP sources go just above this line
PPP_EPISODES_BEFORE = "##### REFERENCES"                         # new PPP episodes go just above this line
OE_VIDEOS_BEFORE = "#### Sources cited in the show notes"        # new videos go above, new sources below (A-Z)


# ---------- links ----------

URL = re.compile(r"https?://[^\s<>\"'\[\]]+")


def trim_url(url):
    """Drop punctuation that ended the sentence rather than the link."""
    while url and (url[-1] in ".,;:!?'\"”’" or (url[-1] == ")" and url.count("(") < url.count(")"))):
        url = url[:-1]
    return url


def clean(url):
    """A link as stored in the data, and what it points at: 'video', 'playlist', 'channel' or 'link'."""
    p = urllib.parse.urlsplit(url.strip())
    host = p.netloc.lower()
    bare = host[4:] if host.startswith("www.") else host
    params = urllib.parse.parse_qs(p.query)
    if bare in ("youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be", "youtube-nocookie.com"):
        vid = None
        if bare == "youtu.be":
            vid = p.path.strip("/").split("/")[0]
        elif p.path == "/watch":
            vid = (params.get("v") or [None])[0]
        else:
            m = re.match(r"/(?:shorts|live|embed|v)/([\w-]{11})", p.path)
            vid = m.group(1) if m else None
        if vid and re.fullmatch(r"[\w-]{11}", vid):
            return "video", f"https://www.youtube.com/watch?v={vid}"
        if p.path == "/playlist" and params.get("list"):
            return "playlist", f"https://www.youtube.com/playlist?list={params['list'][0]}"
        return "channel", f"https://www.youtube.com{p.path}".rstrip("/")

    if re.match(r"(?:[\w-]+\.)?amazon\.", bare) and (m := AMAZON_ITEM.search(p.path)):
        return "link", f"https://{host}/dp/{m.group(1)}"     # drop the title slug, ref= and tracking

    def keep(part):
        key = urllib.parse.unquote(part.split("=", 1)[0])
        if key in TRACKING or key.startswith("utm_"):
            return False
        if bare == "podcasts.apple.com":
            return key == "i"                  # README: the /us/ store link without l=…
        if bare == "open.spotify.com":
            return False
        if bare in ("x.com", "twitter.com"):
            return key != "s"
        return True
    query = "&".join(part for part in p.query.split("&") if part and keep(part))
    return "link", urllib.parse.urlunsplit((p.scheme.lower(), host, p.path, query, ""))


def isbn13_ok(digits):
    return sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(digits)) % 10 == 0


def isbn10_to_13(isbn10):
    """'0198707614' -> '9780198707615', or None if it isn't a valid ISBN-10."""
    if not re.fullmatch(r"\d{9}[\dXx]", isbn10):
        return None
    if sum((10 - i) * (10 if c in "Xx" else int(c)) for i, c in enumerate(isbn10)) % 11:
        return None
    core = "978" + isbn10[:9]
    return core + str((10 - sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(core)) % 10) % 10)


AMAZON_ITEM = re.compile(r"/(?:dp|gp/product|o/ASIN|exec/obidos/ASIN)/([A-Z0-9]{10})(?=/|$)", re.I)


def identifier(kind, link):
    """The best identifier for a source (README: DOI > arXiv > ISBN > cleaned URL) and which it is."""
    if kind != "link":
        return link, "YouTube"
    p = urllib.parse.urlsplit(link)
    host = p.netloc.lower().removeprefix("www.")
    path = urllib.parse.unquote(p.path)
    doi = None
    if host in ("doi.org", "dx.doi.org"):
        doi = path.lstrip("/")
    elif m := re.search(r"/doi/(?:abs/|full/|pdf/|epdf/|epub/)?(10\.\d{4,9}/[^?#]+)", path):
        doi = m.group(1)
    elif host == "nature.com" and (m := re.match(r"/articles/([\w.-]+)$", path)):
        doi = "10.1038/" + m.group(1)
    if doi and doi.startswith("10."):
        return "https://doi.org/" + doi.rstrip("/"), "DOI"
    if host in ("arxiv.org", "export.arxiv.org"):
        m = re.match(r"/(?:abs|pdf|html)/((?:\d{4}\.\d{4,5})|(?:[a-z-]+(?:\.[A-Z]{2})?/\d{7}))(?:v\d+)?(?:\.pdf)?/?$", path)
        if m:
            return "https://arxiv.org/abs/" + m.group(1), "arXiv"
    for digits in re.findall(r"(?<!\d)(97[89]\d{10})(?!\d)", path):
        if isbn13_ok(digits):
            return "urn:isbn:" + digits, "ISBN"
    if re.match(r"(?:[\w-]+\.)?amazon\.", host) and (m := AMAZON_ITEM.search(path)):
        if isbn := isbn10_to_13(m.group(1)):              # a book's Amazon ID is its ISBN-10
            return "urn:isbn:" + isbn, "ISBN"
    return link.rstrip("/"), "URL"


def watch_url(video_id):
    return f"https://www.youtube.com/watch?v={video_id}"


def mint(ident):
    return "Source-" + str(uuid.uuid5(uuid.NAMESPACE_URL, ident))


# ---------- the description ----------

TS = r"(?:\d{1,2}:)?\d{1,2}:\d{2}"
LEAD = r"(?:[^\w\s(\[#]+\s*|\d{1,2}[.)]\s+)?"         # a bullet, arrow, emoji or "1." before the time
CHAPTER_TIME_FIRST = re.compile(rf"^{LEAD}[(\[]?({TS})[)\]]?\s*(?:[-–—|:.·•]\s*)?(\S.*?)$")
CHAPTER_TIME_LAST = re.compile(rf"^{LEAD}(\S.*?)\s*(?:[-–—|:@]\s*)?[(\[]?({TS})[)\]]?$")
TIME_ANYWHERE = re.compile(rf"(?<![\d:]){TS}(?![\d:])")
HEADING = re.compile(r"^\W*(chapters?|timestamps?|time ?stamps?|links?|sources?|references?|resources?|"
                     r"mentioned|show ?notes|further reading|read more|credits|music|sponsors?|subscribe|"
                     r"follow|support|connect|listen|watch|find (us|me)|about (us|me|the hosts?))\b", re.I)
CALL_TO_ACTION = re.compile(r"\b(subscribe|patreon|newsletter|sponsor(ed)?|merch|leave a comment|the bell|"
                            r"support the (show|channel)|join (my|our|the) )\b", re.I)
ASK = re.compile(r"^(follow|subscribe|support|join|connect|listen (on|to|here)|find (us|me)|sign up|get (the|my|our))\b", re.I)
GENERIC_LABELS = {"here", "link", "links", "source", "sources", "video", "article", "paper", "website", "watch",
                  "read", "read more", "pdf", "full video", "full episode"}


def seconds_of(stamp):
    secs = 0
    for part in stamp.split(":"):
        secs = secs * 60 + int(part)
    return secs


def chapter(line):
    """'1:05:14 Title' (or 'Title - 1:05:14') -> (3914, 'Title'), else None."""
    line = line.strip()
    if m := CHAPTER_TIME_FIRST.match(line):
        stamp, title = m.groups()
    elif m := CHAPTER_TIME_LAST.match(line):
        title, stamp = m.groups()
    else:
        return None
    if URL.search(title):
        return None
    return seconds_of(stamp), title.strip(" -–—|:·•")


def is_prose(paragraph):
    lines = [l.strip() for l in paragraph.splitlines() if l.strip()]
    if not lines or any(URL.search(l) or chapter(l) for l in lines):
        return False
    if all(l.startswith("#") for l in lines):
        return False                                   # hashtags
    first = lines[0]
    if HEADING.match(first) and (first.endswith(":") or len(lines) == 1 and len(first) < 70):
        return False
    if len(paragraph) < 250 and CALL_TO_ACTION.search(paragraph):
        return False
    return True


def tidy_label(text):
    """'- Beetle in a box (Wikipedia): ' -> 'Beetle in a box (Wikipedia)'."""
    label = re.sub(r"^\s*(?:[-•*▶►→]|\d+[.)])\s+", "", text)
    label = re.sub(r"\s+", " ", label).strip(" :-–—|[]")
    while label.endswith("(") or (label.endswith(")") and label.count("(") < label.count(")")):
        label = label[:-1].rstrip(" :-–—|")
    while label.startswith(")") or (label.startswith("(") and label.count("(") > label.count(")")):
        label = label[1:].lstrip(" :-–—|")
    if label.startswith("(") and label.endswith(")") and label.count("(") == 1:
        label = label[1:-1].strip()
    return label


def read_description(text):
    """YouTube description -> summary paragraphs, chapters, links (with labels), and where the summary stopped."""
    text = (text or "").replace("\r\n", "\n")
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    summary, stopped_at, started = [], None, False
    for p in paragraphs:
        if is_prose(p):
            if stopped_at is None:
                summary.append(p)
                started = True
        elif started and stopped_at is None:
            stopped_at = p.splitlines()[0].strip()

    lines = [l.strip() for l in text.splitlines()]
    chapters = []
    for line in lines:
        c = chapter(line)
        if c and not line.startswith("#"):
            chapters.append(c)
    if len(chapters) < 2:                              # one stray time isn't a chapter list
        chapters = []
    chapters.sort(key=lambda c: c[0])

    links, used_labels = [], set()
    for i, line in enumerate(lines):
        for raw in URL.findall(line):
            url = trim_url(raw)
            label = line.replace(raw, " ")
            for other in URL.findall(label):
                label = label.replace(other, " ")
            label = tidy_label(label)
            if label.lower() in GENERIC_LABELS:
                label = ""
            if not label and i > 0:
                prev = lines[i - 1]
                if (prev and not URL.search(prev) and not chapter(prev) and not prev.endswith(":")
                        and not HEADING.match(prev) and prev not in used_labels and len(prev) < 200):
                    label = tidy_label(prev)
            if label:
                used_labels.add(label)
            links.append((url, label))
    return summary, chapters, links, stopped_at


# ---------- the web (all optional except the video itself) ----------

def _context():
    try:
        import certifi                                # comes with yt-dlp; fixes python.org Pythons on macOS
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def http_get(url, limit=800_000, timeout=12):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
                      "(KHTML, like Gecko) Version/17.0 Safari/605.1.15",
        "Accept-Language": "en-US,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout, context=_context()) as r:
        ctype = r.headers.get("Content-Type", "")
        body = r.read(limit)
        return ctype, body.decode(r.headers.get_content_charset() or "utf-8", "replace")


def page_title(url):
    try:
        ctype, body = http_get(url)
    except Exception:
        return None
    if "html" not in ctype:
        return None
    for pattern in (r'<meta[^>]+property=["\']og:title["\'][^>]*content=["\']([^"\']+)',
                    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*property=["\']og:title',
                    r"<title[^>]*>(.*?)</title>"):
        if m := re.search(pattern, body, re.I | re.S):
            title = re.sub(r"\s+", " ", html.unescape(m.group(1))).strip()
            if title:
                return title
    return None


def oembed(link):
    """Title, channel name and channel (its @handle or UC… ID) of a YouTube video, without yt-dlp."""
    try:
        _, body = http_get("https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote(link, safe=""))
        data = json.loads(body)
        channel = (data.get("author_url") or "").rstrip("/").rsplit("/", 1)[-1]
        return data.get("title"), data.get("author_name"), channel
    except Exception:
        return None, None, None


class _Quiet:
    """yt-dlp logger that keeps quiet; failures come back as exceptions and are reported once, by us."""
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


COOKIES_FROM = None                               # set by --cookies-from-browser


def youtube_metadata(url, flat=False):
    try:
        import yt_dlp
    except ImportError:
        sys.exit("This needs yt-dlp. Install it with:  pip install -r _build/requirements.txt")
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "ignore_no_formats_error": True,
            "noprogress": True, "logger": _Quiet()}
    if flat:
        opts["extract_flat"] = "in_playlist"
    if COOKIES_FROM:
        opts["cookiesfrombrowser"] = (COOKIES_FROM,)
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def video_facts(info):
    ts = info.get("release_timestamp") or info.get("timestamp")
    when = datetime.datetime.fromtimestamp(ts, PACIFIC).replace(microsecond=0) if ts else None
    if when:
        date = when.date()
    elif info.get("upload_date"):
        date = datetime.datetime.strptime(info["upload_date"], "%Y%m%d").date()
    else:
        date = None
    return {
        "id": info["id"], "title": (info.get("fulltitle") or info.get("title") or "").strip(),
        "description": info.get("description") or "", "duration": int(info.get("duration") or 0),
        "when": when, "date": date, "channel_id": info.get("channel_id") or "",
        "handle": (info.get("uploader_id") or "").lower(),
        "youtube_chapters": [(int(c.get("start_time") or 0), (c.get("title") or "").strip())
                             for c in info.get("chapters") or [] if (c.get("title") or "").strip()],
    }


def apple_episode(show_url, name, date):
    """Find the episode's Apple Podcasts link through Apple's public lookup API. -> (link, how) or (None, why)."""
    m = re.search(r"/id(\d+)", show_url or "")
    if not m:
        return None, "the podcast has no Apple link in the data"
    try:
        _, body = http_get(f"https://itunes.apple.com/lookup?id={m.group(1)}&entity=podcastEpisode&limit=200")
        results = [r for r in json.loads(body).get("results", []) if r.get("wrapperType") == "podcastEpisode"]
    except Exception as e:
        return None, f"couldn't reach Apple ({e.__class__.__name__})"

    def norm(s):
        return re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower()).strip()
    scored = sorted(((difflib.SequenceMatcher(None, norm(r.get("trackName")), norm(name)).ratio(), r) for r in results),
                    key=lambda x: -x[0])
    if scored and scored[0][0] >= 0.6:
        return clean(scored[0][1]["trackViewUrl"])[1], "matched by title"
    if date:
        near = [r for r in results if r.get("releaseDate")
                and abs((datetime.date.fromisoformat(r["releaseDate"][:10]) - date).days) <= 2]
        if len(near) == 1:
            return clean(near[0]["trackViewUrl"])[1], f"matched by release date: \"{near[0].get('trackName')}\""
    return None, "Apple hasn't listed it yet"


# ---------- the data ----------

class Data:
    def __init__(self, ppp_text, oe_text):
        self.g = rdflib.Graph()
        self.g.parse(data=ppp_text, format="turtle")
        self.g.parse(data=oe_text, format="turtle")
        self.by_link = {}
        for prop in LINK_PROPS:
            for node, value in self.g.subject_objects(prop):
                kind, link = clean(str(value))
                for key in {identifier(kind, link)[0], link.rstrip("/"), str(value).rstrip("/")}:
                    self.by_link.setdefault(key, node)
        self.show = next(self.g.subjects(RDF.type, D.Podcast), None)
        self.channel = D.OntologyExplained

    def local(self, node):
        return str(node)[len(DENSE):]

    def name(self, node):
        if (node, RDF.type, D.PodcastEpisode) in self.g:
            return f"PPP Episode {fmt_number(self.g.value(node, D.episodeNumber).toPython())}"
        value = self.g.value(node, D.name)
        return str(value) if value is not None else None

    def link_of(self, node, prop):
        value = self.g.value(node, prop)
        return str(value) if value is not None else None

    def find(self, kind, link):
        ident = identifier(kind, link)[0]
        node = self.by_link.get(ident) or self.by_link.get(link.rstrip("/"))
        if node is None and (D[mint(ident)], None, None) in self.g:
            node = D[mint(ident)]
        if node is None:
            node = self.site_page(link)
        return node

    def site_page(self, link):
        """A link to an episode or video page on caseyhart.com -> that episode or video."""
        if m := re.search(r"caseyhart\.com/ppp/episode/([\d.]+)/?$", link):
            node = D[f"PPP-Episode{m.group(1)}"]
            return node if (node, RDF.type, D.PodcastEpisode) in self.g else None
        if m := re.search(r"caseyhart\.com/ontology-explained/([\w-]+)/?$", link):
            for node in self.channel_videos():
                if slugify(self.name(node) or "") == m.group(1):
                    return node
        return None

    def channel_videos(self):
        return [v for v in self.g.subjects(D.partOf, self.channel) if (v, RDF.type, D.Video) in self.g]

    def playlists(self):
        found = [(pl, self.name(pl), self.link_of(pl, D["url-Youtube"]))
                 for pl in self.g.subjects(RDF.type, D.Playlist) if (pl, D.partOf, self.channel) in self.g]
        return sorted(found, key=lambda x: x[1] or "")

    def is_show_or_person(self, node):
        return any((node, RDF.type, t) in self.g for t in (D.Podcast, D.YouTubeChannel, D.Person))

    @property
    def people_by_channel(self):
        """PEOPLE_BY_CHANNEL plus every channel in the data that has a :createdBy (by its UC… ID)."""
        people = {k.lower(): v for k, v in PEOPLE_BY_CHANNEL.items()}
        for channel in self.g.subjects(RDF.type, D.YouTubeChannel):
            creators = list(self.g.objects(channel, D.createdBy))
            if self.channel_id(channel) and len(creators) == 1:
                people.setdefault(self.channel_id(channel).lower(), self.local(creators[0]))
        return people

    def channel_id(self, node):
        url = self.link_of(node, D["url-Youtube"]) or ""
        m = re.search(r"/channel/(UC[\w-]{22})", url)
        return m.group(1) if m else None


# ---------- writing Turtle ----------

def lit(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\r", "") + '"'


def long_lit(s):
    return '"""' + s.replace("\\", "\\\\").replace('"', '\\"') + '"""'


def duration(secs):
    h, rest = divmod(int(secs), 3600)
    m, s = divmod(rest, 60)
    out = (f"{h}H" if h else "") + (f"{m}M" if m else "") + (f"{s}S" if s else "")
    return f'"PT{out or "0S"}"^^xsd:duration'


def any_uri(link):
    return f'{lit(link)}^^xsd:anyURI'


def list_lines(prop, items, comments=None, sep=","):
    """':hasPart :A,\\n\\t\\t:B ;' with an optional '# comment' after each item."""
    out = []
    for i, item in enumerate(items):
        last = i == len(items) - 1
        text = ("\t" + prop + " " if i == 0 else "\t\t") + item + (" ;" if last else sep)
        if comments and comments[i]:
            text += "\t# " + comments[i]
        out.append(text)
    return out


def segment_blocks(parent, cls, chapters):
    blocks = []
    for i, (start, title) in enumerate(chapters, 1):
        blocks.append(f":{parent}-Segment{i:02d} a :{cls} ;\n\t:name {lit(title)} ;\n"
                      f"\t:startingTimestamp {duration(start)} ;\n.")
    return blocks


def source_block(src, with_id_comment):
    lines = []
    if with_id_comment and src["youtube_id"]:
        lines.append(f"# {src['youtube_id']}")
    lines.append(f":{src['iri']} a :{'Video' if src['kind'] == 'video' else 'Content'} ;")
    if src["creator"]:
        lines.append(f"\t:createdBy :{src['creator']} ;")
    if src["name"]:
        lines.append(f"\t:name {lit(src['name'])} ;")
    else:
        lines.append("\t# TODO: add a :name. The build stops until this source has one.")
    lines.append(f"\t:{'url-Youtube' if src['kind'] == 'video' else 'uri'} {any_uri(src['link'])} ;")
    lines.append(".")
    return "\n".join(lines)


def insert_before(text, marker, chunk, gap):
    """Put chunk on its own just above the line that starts with marker, `gap` blank lines after what's above."""
    i = text.find("\n" + marker)
    if i < 0:
        return text.rstrip("\n") + "\n" * (gap + 1) + chunk.strip("\n") + "\n", False
    head, tail = text[:i + 1], text[i + 1:]
    blank_after = len(head) - len(head.rstrip("\n")) - 1
    return head.rstrip("\n") + "\n" * (gap + 1) + chunk.strip("\n") + "\n" * (blank_after + 1) + tail, True


def insert_sorted(text, marker, blocks):
    """Insert source blocks into the A-Z list below the marker line (the Ontology Explained sources)."""
    found = re.search(r"(?:^|\n)" + re.escape(marker) + r"[^\n]*\n(?:[ \t]*\n)*", text)
    if not found:
        return text.rstrip() + "\n\n" + "\n\n".join(b for _, b in blocks) + "\n"
    head, section = text[:found.end()], text[found.end():]
    body = section.rstrip()
    ending = section[len(body):] or "\n"
    pieces = re.split(r"(\n(?:[ \t]*\n)+)", body) if body else []   # unit, separator, unit, separator, ...

    def key(name):
        return re.sub(r"^\W+", "", name or "").casefold()

    for name, block in blocks:
        at = None
        for j in range(0, len(pieces), 2):
            unit = pieces[j]
            if unit.lstrip().startswith("####"):    # the next section: stop here
                at = j
                break
            m = re.search(r'^\s*:name\s+"((?:[^"\\]|\\.)*)"', unit, re.M)
            if name and m and key(re.sub(r"\\(.)", r"\1", m.group(1))) > key(name):
                at = j
                break
        if at is None:
            pieces += (["\n\n"] if pieces else []) + [block]
        else:
            pieces[at:at] = [block, "\n\n"]
    return head + "".join(pieces) + ending


def replace_or_add_line(text, block_start, prop, value_line, after_props):
    """Inside the block that starts at block_start, set the `prop` line, or add it after the first of after_props."""
    end = text.index("\n.", block_start)
    block = text[block_start:end]
    lines = block.split("\n")
    for i, line in enumerate(lines):
        if line.strip().startswith(prop + " "):
            lines[i] = value_line
            break
    else:
        at = None
        for after in after_props:
            at = next((i for i, l in enumerate(lines) if l.strip().startswith(after + " ")), None)
            if at is not None:
                break
        lines.insert((at if at is not None else 0) + 1, value_line)
    return text[:block_start] + "\n".join(lines) + text[end:]


# ---------- putting it together ----------

def resolve_links(links, data, this_video, offline, oe_file):
    """Each link in the description -> something to cite, or a reason it isn't cited."""
    cites, new, skipped, seen = [], [], [], set()
    for url, label in links:
        kind, link = clean(url)
        if kind == "video" and youtube_id(link) == this_video:
            continue
        node = data.find(kind, link)
        if node is not None:
            if data.is_show_or_person(node):
                skipped.append((link, "the show or channel itself"))
            elif node not in seen:
                seen.add(node)
                cites.append({"node": data.local(node), "name": data.name(node), "link": link, "status": "known"})
            continue
        why = next((why for pattern, why in NOT_SOURCES if re.search(pattern, link, re.I)), None)
        if not why and label and (CALL_TO_ACTION.search(label) or ASK.match(label)):
            why = f"looks like a call to action: {label!r}"
        if why or kind in ("channel", "playlist"):
            skipped.append((link, why or ("YouTube channel" if kind == "channel" else "not one of your series")))
            continue
        ident, how = identifier(kind, link)
        iri = mint(ident)
        if D[iri] in seen:
            continue
        seen.add(D[iri])
        src = {"iri": iri, "kind": kind, "link": link, "how": how, "creator": None, "name": label or None,
               "youtube_id": youtube_id(link) if kind == "video" else None, "named_from": "description"}
        if kind == "video" and not offline:
            title, channel_name, channel = oembed(link)
            if title:
                src["name"], src["named_from"] = title, "YouTube"
            src["channel"] = channel_name or channel
            src["creator"] = data.people_by_channel.get((channel or "").lower())
        elif not src["name"] and not offline:
            title = page_title(link)
            if title:
                src["name"], src["named_from"] = title, "the page's title"
        if not src["name"]:
            src["named_from"] = None
        src["block"] = source_block(src, with_id_comment=oe_file)
        new.append(src)
        cites.append({"node": iri, "name": src["name"], "link": link, "status": "new", "src": src})
    return cites, new, skipped


EP_SUFFIX = re.compile(r"\s*[|｜:–—-]\s*(?:PPP\s*)?(?:Ep|Episode)\.?\s*#?\s*(\d+(?:\.\d+)?)\b.*$", re.I)
EP_PREFIX = re.compile(r"^\s*(?:PPP\s*)?(?:Ep|Episode)\.?\s*#?\s*(\d+(?:\.\d+)?)\s*[:|｜–—-]\s*", re.I)


def episode_number(title, data, given):
    """'Some Title | Ep 15' (or 'Ep 15: Some Title') -> (Decimal('15'), 'Some Title')."""
    m = EP_SUFFIX.search(title) or EP_PREFIX.search(title)
    name = (EP_SUFFIX.sub("", title) if EP_SUFFIX.search(title) else EP_PREFIX.sub("", title)).strip() if m else title
    if given is not None:
        return Decimal(given), name
    if m:
        return Decimal(m.group(1)), name
    numbers = [n.toPython() for n in data.g.objects(None, D.episodeNumber)]
    return Decimal(int(max(numbers, default=Decimal(0))) + 1), name


def main():
    ap = argparse.ArgumentParser(description="Add a new PPP episode or Ontology Explained video from its YouTube link.",
                                 epilog="See _build/README.md, 'Adding an episode or video'.")
    ap.add_argument("url", help="the episode's or video's YouTube link")
    ap.add_argument("--spotify", help="PPP: the episode's Spotify link")
    ap.add_argument("--apple", help="PPP: the episode's Apple Podcasts link (default: looked up on Apple)")
    ap.add_argument("--number", help="PPP: the episode number (default: from '| Ep N' in the title, else the next one)")
    ap.add_argument("--series", action="append", default=[], metavar="NAME",
                    help="Ontology Explained: a series (playlist) it's in; repeat for more "
                         "(default: found by checking your playlists on YouTube)")
    ap.add_argument("--show", choices=["ppp", "oe"], help="which show (default: from the YouTube channel)")
    ap.add_argument("--dry-run", action="store_true", help="show what would be added; change nothing")
    ap.add_argument("--no-build", action="store_true", help="write the .ttl but don't run the build")
    ap.add_argument("--offline", action="store_true",
                    help="look nothing else up (source titles, Apple link, playlists)")
    ap.add_argument("--from-json", metavar="FILE", help="use saved metadata (yt-dlp -J URL > FILE) instead of YouTube")
    ap.add_argument("--cookies-from-browser", metavar="BROWSER",
                    help="let yt-dlp use your YouTube sign-in from this browser (safari, chrome, firefox), "
                         "for when YouTube asks it to sign in")
    args = ap.parse_args()
    global COOKIES_FROM
    COOKIES_FROM = args.cookies_from_browser

    try:
        rdflib.Graph().query("ASK {}")              # the build runs SPARQL; make sure it can, before writing anything
    except Exception as e:
        sys.exit(f"rdflib can't run SPARQL queries in this Python ({e.__class__.__name__}: {e}),\n"
                 "so the build would fail. Nothing was changed. Fix it with:\n"
                 "  pip3 install -r _build/requirements.txt")

    kind, link = clean(args.url)
    if kind != "video":
        sys.exit(f"That isn't a link to a YouTube video: {args.url}")
    vid = youtube_id(link)

    ppp_text, oe_text = DATA.read_text(encoding="utf-8"), OE_DATA.read_text(encoding="utf-8")
    data = Data(ppp_text, oe_text)
    existing = data.find("video", link)

    # A second run for a PPP episode: fill in its podcast links, nothing else.
    if existing is not None and (existing, RDF.type, D.PodcastEpisode) in data.g:
        return fill_links(args, data, existing, ppp_text, oe_text)
    if existing is not None and (existing, D.partOf, data.channel) in data.g:
        print(f"{data.name(existing)!r} is already in {OE_DATA.relative_to(ROOT)} (:{data.local(existing)}). "
              "Nothing to do.")
        return 0

    if args.from_json:
        info = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
    else:
        print(f"Reading {link} from YouTube...")
        try:
            info = youtube_metadata(link)
        except Exception as e:
            sys.exit(f"YouTube didn't give the video's details:\n  {str(e).replace('ERROR: ', '')[:400]}\n"
                     "Things to try:\n"
                     "  pip install -U yt-dlp                     (YouTube changes often; new yt-dlp keeps up)\n"
                     "  --cookies-from-browser safari             (if YouTube says to sign in or confirm you're not a bot)\n"
                     "  yt-dlp -J <link> > video.json, then --from-json video.json")
    v = video_facts(info)
    if v["id"] != vid:
        sys.exit(f"The metadata is for video {v['id']}, not {vid}.")

    show = args.show
    if not show:
        if v["channel_id"] and v["channel_id"] == data.channel_id(data.show):
            show = "ppp"
        elif v["channel_id"] and v["channel_id"] == data.channel_id(data.channel):
            show = "oe"
        else:
            sys.exit(f"The video is on channel {v['channel_id'] or '(unknown)'}, which is neither PPP's nor "
                     "Ontology Explained's. Use --show ppp or --show oe if it belongs to one of them.")

    summary, chapters, links, stopped_at = read_description(v["description"])
    wrote_chapters = (len(TIME_ANYWHERE.findall(v["description"])) >= 2
                      or re.search(r"(?im)^\W*(chapters?|timestamps?|time ?stamps?)\W*$", v["description"]))
    if not chapters and v["youtube_chapters"] and wrote_chapters:
        # The description has timestamps written in a way read_description doesn't follow, but YouTube made
        # chapters from them: use YouTube's list. (Without timestamps in the description, YouTube's list would
        # be automatic chapters, which we don't record.)
        chapters = v["youtube_chapters"]
        print(f"Note: took the {len(chapters)} chapters from YouTube's chapter list; "
              "I couldn't read the timestamp lines in the description myself.")
    problems = []
    for field, what in (("date", "upload date"), ("duration", "length")):
        if not v[field]:
            problems.append(f"YouTube didn't give the {what}.")
    if problems:
        sys.exit("\n".join(problems))
    late = [c for c in chapters if c[0] >= v["duration"]]
    if chapters and chapters[0][0] != 0:
        print("Note: the first chapter doesn't start at 0:00, so YouTube won't show chapters either.")

    if show == "ppp":
        if existing is not None:
            print(f"Note: this video is already cited as :{data.local(existing)} ({data.name(existing)!r}). "
                  "Point those :cites at the new episode instead.")
        return add_episode(args, data, v, summary, chapters, links, stopped_at, ppp_text, oe_text, late)
    return add_video(args, data, v, existing, summary, chapters, links, stopped_at, ppp_text, oe_text, late)


def add_episode(args, data, v, summary, chapters, links, stopped_at, ppp_text, oe_text, late):
    number, name = episode_number(v["title"], data, args.number)
    num = fmt_number(number)
    ep = f"PPP-Episode{num}"
    if (D[ep], None, None) in data.g:
        sys.exit(f"Episode {num} is already in the data with a different YouTube link "
                 f"({data.link_of(D[ep], D['url-Youtube'])}). Use --number to give this one another number.")
    cites, new, skipped = resolve_links(links, data, v["id"], args.offline, oe_file=False)

    spotify = clean(args.spotify)[1] if args.spotify else None
    apple, apple_how = (clean(args.apple)[1], "given") if args.apple else (None, None)
    if not apple and not args.offline:
        apple, apple_how = apple_episode(data.link_of(data.show, D["url-ApplePodcasts"]), name, v["date"])

    lines = [f":{ep} a :PodcastEpisode ;",
             f"\t:name {lit(name)} ;",
             f"\t:hasDuration [:duration {duration(v['duration'])}] ;",
             f'\t:episodeNumber "{num}"^^xsd:decimal ;',
             f'\t:releaseDate [:universalDate "{v["date"].isoformat()}"^^xsd:date] ;',
             f"\t:partOf :{data.local(data.show)} ;",
             f"\t:url-Youtube {any_uri(watch_url(v['id']))} ;"]
    if spotify:
        lines.append(f"\t:url-Spotify {any_uri(spotify)} ;")
    if apple:
        lines.append(f"\t:url-ApplePodcasts {any_uri(apple)} ;")
    if summary:
        lines.append(f"\t:description {long_lit(PARAGRAPH_BREAK.join(summary))} ;")
    if chapters:
        lines += list_lines(":hasPart", [f":{ep}-Segment{i:02d}" for i in range(1, len(chapters) + 1)])
    if cites:
        lines += list_lines(":cites", [":" + c["node"] for c in cites])
    lines.append(".")
    chunk = f"#### Episode {num}\n" + "\n\n".join(["\n".join(lines)] + segment_blocks(ep, "PodcastSegment", chapters))

    new_ppp, _ = insert_before(ppp_text, PPP_EPISODES_BEFORE, chunk, gap=2)
    if new:
        group = f"#### First cited in Episode {num}\n" + "\n\n".join(s["block"] for s in new)
        new_ppp, _ = insert_before(new_ppp, PPP_SOURCES_BEFORE, group, gap=1)

    page = f"/ppp/episode/{num}/"
    print(f"\nPPP Episode {num}: {name}")
    print(f"  {fmt_date(v['date'])} · {fmt_length(v['duration'])} · {len(chapters)} chapters · page {page}")
    report_description(summary, stopped_at, late)
    print(f"  Spotify: {spotify or 'not added yet. Run this again with --spotify <link> once it is up.'}")
    if apple:
        print(f"  Apple:   {apple}  ({apple_how})")
    else:
        print(f"  Apple:   not added yet ({apple_how or '--offline'}). Run this again later to look it up.")
    report_cites(cites, skipped)
    return finish(args, ppp_text, new_ppp, oe_text, oe_text, page)


def add_video(args, data, v, existing, summary, chapters, links, stopped_at, ppp_text, oe_text, late):
    iri = mint(watch_url(v["id"]))
    name = v["title"]
    if existing is not None:                       # cited somewhere already (say on PPP) but not described yet
        iri = data.local(existing)
        old = data.name(existing)
        if old and old != name:
            print(f"Note: this video is already cited as {old!r}. Keeping that name (YouTube has {name!r}).")
            name = old
    cites, new, skipped = resolve_links(links, data, v["id"], args.offline, oe_file=True)
    series = find_series(args, data, v["id"])

    when = v["when"]
    date_part = f':universalDate "{v["date"].isoformat()}"^^xsd:date'
    if when:
        date_part += f' ; :universalDateTime "{when.isoformat()}"^^xsd:dateTime'
    part_of = "\t:partOf :OntologyExplained" + "".join(f", :{data.local(node)}" for node, _, _ in series) + " ;"
    if series:
        part_of += "\t# " + "; ".join(title for _, title, _ in series)
    lines = [f"# {v['id']} · {name}",
             f":{iri} a :Video ;",
             f"\t:name {lit(name)} ;",
             f"\t:hasDuration [:duration {duration(v['duration'])}] ;",
             f"\t:releaseDate [{date_part}] ;",
             part_of,
             "\t:createdBy :CaseyHart ;",
             f"\t:url-Youtube {any_uri(watch_url(v['id']))} ;"]
    if summary:
        lines.append(f"\t:description {long_lit(PARAGRAPH_BREAK.join(summary))} ;")
    if chapters:
        lines += list_lines(":hasPart", [f":{iri}-Segment{i:02d}" for i in range(1, len(chapters) + 1)])
    if cites:
        lines += list_lines(":cites", [":" + c["node"] for c in cites], [c["name"] for c in cites], sep=" ,")
    lines.append(".")
    chunk = "\n\n".join(["\n".join(lines)] + segment_blocks(iri, "VideoSegment", chapters))

    new_oe, _ = insert_before(oe_text, OE_VIDEOS_BEFORE, chunk, gap=1)
    if new:
        new_oe = insert_sorted(new_oe, OE_VIDEOS_BEFORE, [(s["name"], s["block"]) for s in new])

    page = f"/ontology-explained/{slugify(name)}/"
    print(f"\nOntology Explained: {name}")
    clock = f" {when.strftime('%I:%M %p').lstrip('0')}" if when else ""
    print(f"  {fmt_date(v['date'])}{clock} · {fmt_length(v['duration'])} · {len(chapters)} chapters · page {page}")
    report_description(summary, stopped_at, late)
    if series:
        print("  Series: " + "; ".join(title for _, title, _ in series))
    elif args.offline:
        print("  Series: not checked (--offline). Use --series NAME to put it in one.")
    else:
        print("  Series: none found. If it belongs in one, run with --series NAME, or add the playlist to :partOf.")
    report_cites(cites, skipped)
    return finish(args, ppp_text, ppp_text, oe_text, new_oe, page)


def find_series(args, data, video_id):
    """Which of the channel's playlists the video is in: from --series, or by reading each playlist on YouTube."""
    playlists = data.playlists()
    if args.series:
        chosen = []
        for wanted in args.series:
            w = wanted.casefold()
            matches = ([p for p in playlists if (p[1] or "").casefold() == w]
                       or [p for p in playlists if w in (p[1] or "").casefold()])
            if len(matches) != 1:
                sys.exit(f"--series {wanted!r} matches {len(matches)} series. The series are: "
                         + "; ".join(p[1] for p in playlists))
            chosen.append(matches[0])
        return chosen
    if args.offline:
        return []
    print(f"Checking which of your {len(playlists)} series it's in...")
    found = []
    for p in playlists:
        if not p[2]:
            continue
        try:
            info = youtube_metadata(p[2], flat=True)
        except Exception:
            print(f"  couldn't read the {p[1]!r} playlist on YouTube; skipped it")
            continue
        if video_id in {e.get("id") for e in info.get("entries") or []}:
            found.append(p)
    return found


def fill_links(args, data, ep, ppp_text, oe_text):
    """Second run for a PPP episode: add (or, when you pass one, replace) its Spotify and Apple links."""
    have_spotify, have_apple = data.link_of(ep, D["url-Spotify"]), data.link_of(ep, D["url-ApplePodcasts"])
    spotify = clean(args.spotify)[1] if args.spotify else None
    apple, how = (clean(args.apple)[1], "given") if args.apple else (None, None)
    if not apple and not have_apple and not args.offline:
        date = data.g.value(data.g.value(ep, D.releaseDate), D.universalDate)
        apple, how = apple_episode(data.link_of(data.show, D["url-ApplePodcasts"]), str(data.g.value(ep, D.name)),
                                   date.toPython() if date is not None else None)
    start = re.search(rf"^:{re.escape(data.local(ep))} a :PodcastEpisode ;", ppp_text, re.M)
    if not start:
        sys.exit(f"Couldn't find the block for :{data.local(ep)} in {DATA.relative_to(ROOT)}.")
    text, changes = ppp_text, []
    if spotify and spotify != have_spotify:
        text = replace_or_add_line(text, start.start(), ":url-Spotify", f"\t:url-Spotify {any_uri(spotify)} ;",
                                   [":url-Youtube"])
        changes.append(f"Spotify: {spotify}")
    if apple and apple != have_apple:
        text = replace_or_add_line(text, start.start(), ":url-ApplePodcasts",
                                   f"\t:url-ApplePodcasts {any_uri(apple)} ;", [":url-Spotify", ":url-Youtube"])
        changes.append(f"Apple:   {apple}  ({how})")

    num = fmt_number(data.g.value(ep, D.episodeNumber).toPython())
    print(f"{data.name(ep)} is already in the data, so this only fills in its podcast links.")
    if not changes:
        print(f"  Spotify: {have_spotify or 'missing. Pass --spotify <link>.'}")
        print(f"  Apple:   {have_apple or 'missing' + (f' ({how})' if how else '') + '. Pass --apple <link> or try later.'}")
        print("Nothing to change.")
        return 0
    for c in changes:
        print("  " + c)
    return finish(args, ppp_text, text, oe_text, oe_text, f"/ppp/episode/{num}/")


def report_description(summary, stopped_at, late):
    if summary:
        where = f', up to "{stopped_at[:50]}"' if stopped_at else ""
        n = len(summary)
        print(f"  Description: the first {n} paragraph{'s' if n != 1 else ''} of YouTube's{where}")
    else:
        print("  Description: none found. Add a :description by hand if you want one.")
    for _, title in late:
        print(f"  Warning: chapter {title!r} starts after the end. The build will stop until it's fixed.")


def report_cites(cites, skipped):
    if not cites:
        print("  Cites: nothing (no links in the description)")
    else:
        print(f"  Cites {len(cites)}:")
        for c in cites:
            if c["status"] == "known":
                print(f"    already in the data: {c['name'] or ':' + c['node']}")
            else:
                s = c["src"]
                named = f"{s['name']!r} (name from {s['named_from']})" if s["name"] else "NO NAME YET: add a :name"
                print(f"    new, minted from {s['how']}: {named}\n        {s['link']}")
                if s.get("channel"):
                    by = f", so :createdBy :{s['creator']}" if s["creator"] else ""
                    print(f"        on YouTube channel {s['channel']!r}{by}")
    for link, why in skipped:
        print(f"  Not cited ({why}): {link}")


def finish(args, ppp_old, ppp_new, oe_old, oe_new, page):
    """Check the new Turtle parses, then write it (or with --dry-run, show it) and run the build."""
    try:
        check = rdflib.Graph()
        check.parse(data=ppp_new, format="turtle")
        check.parse(data=oe_new, format="turtle")
    except Exception as e:
        sys.exit(f"\nThe new Turtle doesn't parse, so nothing was written. Please report this:\n{e}")
    changed = [(path, old, new) for path, old, new in ((DATA, ppp_old, ppp_new), (OE_DATA, oe_old, oe_new))
               if old != new]
    if args.dry_run:
        for path, old, new in changed:
            rel = str(path.relative_to(ROOT))
            sys.stdout.writelines(difflib.unified_diff(old.splitlines(True), new.splitlines(True), rel, rel, n=1))
        print("\n(--dry-run: nothing written)")
        return 0
    for path, _, new in changed:
        path.write_text(new, encoding="utf-8")
        print(f"\nWrote {path.relative_to(ROOT)}. (If it's open in Emacs: M-x revert-buffer.)")
    if args.no_build:
        print("Run the build when you're ready:  python3 _build/build.py")
        return 0
    print()
    result = subprocess.run([sys.executable, str(Path(__file__).with_name("build.py"))], cwd=ROOT,
                            capture_output=True, text=True)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    if result.returncode:
        if "Problems in the data:" in result.stdout:
            print("\nThe build found problems in the data (listed above). Fix the .ttl, then run:  "
                  "python3 _build/build.py")
        else:
            print("\nThe build crashed (see above). That's the Python setup or build.py, not the data: the .ttl "
                  "was written\nand is fine to review. Once the build runs, run:  python3 _build/build.py")
            if "pyparsing" in result.stderr:
                print("This one is an old pyparsing. Fix:  pip3 install -r _build/requirements.txt")
        return result.returncode
    print(f"""
Next:
  git diff                        check what was added; edit the .ttl if you like, then rebuild
  python3 -m http.server 8000     preview http://localhost:8000{page}
  git add _build ppp ontology-explained && git commit -m "Show pages: ..." && git push""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
