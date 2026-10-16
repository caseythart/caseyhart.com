# Show pages build (PPP and Ontology Explained)

The pages for both shows on caseyhart.com are generated from two data files. Edit the data
or the templates, run one command, then commit. For a new episode or video, one command
writes the data for you from its YouTube page: see [Adding an episode or video](#adding-an-episode-or-video).

```
ppp/podcast_ontology_data.ttl                       PPP data, edited by hand (published, so it's downloadable)
ppp/ppp.css                                         styling for the PPP pages, edited by hand
ontology-explained/ontology_explained_data.ttl      Ontology Explained data, edited by hand (published)
ontology-explained/oe.css                           styling for the Ontology Explained pages, edited by hand
ontology-explained/oe-mark.png, oe-icon-*.png       the "oe" mark, cropped from images/logo.png

_build/build.py                                     the build script (one script for both shows)
_build/add.py                                       adds a new episode or video to the data from its YouTube link
_build/queries/ppp/*.rq                             SPARQL queries for the PPP pages
_build/queries/oe/*.rq                              SPARQL queries for the Ontology Explained pages
_build/queries/shared/*.rq                          queries that look across both shows
_build/templates/ppp/*.html                         PPP page templates (Jinja2)
_build/templates/oe/*.html                          Ontology Explained page templates (Jinja2)
_build/requirements.txt                             Python packages the build needs

ppp/index.html                                      GENERATED: don't edit
ppp/episode/<number>/index.html                     GENERATED: don't edit
ppp/sources/index.html                              GENERATED: don't edit
ontology-explained/index.html                       GENERATED: don't edit
ontology-explained/<video-slug>/index.html          GENERATED: don't edit
ontology-explained/sources/index.html               GENERATED: don't edit
```

The build loads both `.ttl` files into one graph. A source cited by both shows has the same
IRI in both files, so it is one node: its page on each show says where else it's cited, and
an Ontology Explained video cited on PPP (or a PPP episode cited on Ontology Explained) links
to its page on this site.

GitHub Pages skips folders whose names start with `_`, so nothing in `_build/` is published.
(This relies on Pages' default Jekyll processing. If you ever add a `.nojekyll` file, these
files get published too. That's harmless, just clutter.) The same rule is why video pages are
named after the video's title rather than its YouTube ID: three IDs start with `_`.


## One-time setup

From the repo root:

```bash
pip install -r _build/requirements.txt
```

(Use a virtualenv if you prefer. The build needs `rdflib` and `jinja2`; `add.py` also uses
`yt-dlp` to read YouTube.)


## Every time you change either data file, a template, or the CSS

From the repo root:

```bash
python3 _build/build.py
```

- **Success:** one line per show (`PPP: built 16 episode pages...`,
  `Ontology Explained: built 105 video pages...`) and the number of triples read.
- **Data problems:** it prints `Problems in the data:` with a list, writes nothing, and
  exits. Fix the `.ttl` and run it again.

Preview locally before you push:

```bash
python3 -m http.server 8000      # still from the repo root
# open http://localhost:8000/ppp/ and http://localhost:8000/ontology-explained/
```

Then commit the data and the regenerated pages together:

```bash
git add _build/ ppp/ ontology-explained/
git commit -m "Show pages: <what changed>"
git push
```

If you edit a `.ttl` in Emacs while something else is also changing it, revert the
buffer (`M-x revert-buffer`) before saving, so you don't save over the other changes.


## What to edit for common changes

| You want to change...                               | Edit                                              |
|-----------------------------------------------------|---------------------------------------------------|
| Add a new episode or video                          | run `python3 _build/add.py <YouTube link>`        |
| A PPP episode, chapter or source                    | `ppp/podcast_ontology_data.ttl`                   |
| An Ontology Explained video, series or source       | `ontology-explained/ontology_explained_data.ttl`  |
| PPP landing-page copy, hero, host bios              | `_build/templates/ppp/index.html`                 |
| PPP episode page / sources page                     | `_build/templates/ppp/episode.html`, `sources.html` |
| PPP header, footer, "Listen" band                   | `_build/templates/ppp/base.html`                  |
| Ontology Explained landing page (hero, series, list)| `_build/templates/oe/index.html`                  |
| Ontology Explained video page / sources page        | `_build/templates/oe/video.html`, `sources.html`  |
| Ontology Explained header and footer                | `_build/templates/oe/base.html`                   |
| Colors, fonts, spacing                              | `ppp/ppp.css` or `ontology-explained/oe.css`      |
| What data the pages get                             | `_build/queries/**/*.rq`, then `assemble()`, `assemble_oe()` or `connect()` in `build.py` |

Run the build after any of these, including CSS-only changes.


## Adding an episode or video

From the repo root, with the YouTube link:

```bash
python3 _build/add.py https://www.youtube.com/watch?v=VIDEO_ID
```

The same command works for both shows: it tells them apart by the YouTube channel. It reads
the video from YouTube, writes it into that show's `.ttl` (exactly the blocks described in the
next two sections), then runs the build. What it fills in:

- **Title, length, release date** (and upload time, for Ontology Explained).
- **Description:** the paragraphs at the top of the YouTube description, stopping at the first
  chapter list, link, heading (`Chapters`, `Links:` …), call to action or hashtag line.
- **Chapters:** every timestamp line, in any of the usual forms: `0:00 Cold Open`,
  `(1:05:14) Title`, `🔹 12:30 - Title`, `Title - 12:30`. If it can't read the lines, it takes
  YouTube's own chapter list instead, but only when the description has timestamps or a
  `Chapters` heading, so YouTube's automatic chapters never get in.
- **Sources:** every link in the description.
  - A link to something already in either file cites that node: a source (matched by its
    DOI, arXiv ID, ISBN or link), one of your videos, a PPP episode (by its YouTube, Spotify,
    Apple or caseyhart.com link), or a series.
  - Anything else becomes a new source, with its IRI minted by [the source rule](#adding-a-source-either-show).
    An Amazon link to a book counts as its ISBN (a book's Amazon ID is its ISBN-10).
    Its name is the text before the link on the same line (or the line above it); failing
    that, the video's YouTube title or the page's title. A cited video by Carl or you gets
    `:createdBy` (channels are listed in `PEOPLE_BY_CHANNEL` at the top of `add.py`).
  - Profile, support, newsletter and site links are skipped and listed (the rules are
    `NOT_SOURCES` at the top of `add.py`).
- **PPP:** the episode number from `| Ep 15` at the end of the YouTube title (which is then
  dropped from the name), else the next number; `--number 15.5` to choose. The Apple Podcasts
  link, looked up on Apple. The Spotify link, if you pass `--spotify <link>`.
- **Ontology Explained:** each of your series (playlists) the video is in, by checking them
  on YouTube; or name them with `--series "Ethics Explained"` (repeatable).

It prints what it added. **Read the names of the new sources**, since those are its best guesses.
Then:

```bash
git diff                        # what was added; change anything in Emacs, then python3 _build/build.py
python3 -m http.server 8000     # preview
git add _build/ ppp/ ontology-explained/
git commit -m "PPP: Episode 15"
git push
```

- **Spotify or Apple not up yet?** Run the same command again later, adding
  `--spotify <link>` once you have it. For an episode already in the data it only fills in
  the missing podcast links (and replaces one if you pass a different link).
- **A new source with no name** (a PDF with no label, say) gets a `# TODO` line instead of
  `:name`, and the build stops until you add one.
- `--dry-run` shows the Turtle it would add and changes nothing. `--no-build` skips the build.
  `--offline` looks nothing up besides the video itself (no source titles, Apple link or
  playlists).
- **If YouTube refuses:** `pip install -U yt-dlp` (YouTube changes often). If it says to sign
  in or confirm you're not a bot, add `--cookies-from-browser safari` (or `chrome`,
  `firefox`). Last resort: `yt-dlp -J <link> > video.json`, then add `--from-json video.json`.

Descriptions it reads best: summary paragraphs first, then the chapters, then one link per
line with a label (`Coherent Extrapolated Volition (MIRI): https://…`). The label becomes the
source's name, so a good label saves an edit.


## Adding a PPP episode by hand

`add.py` writes this block for you. To write one by hand, copy it into
`ppp/podcast_ontology_data.ttl` next to the other episodes and fill it in. The build needs everything above `:description`. Use the YouTube upload date as the
release date.

```turtle
:PPP-Episode15 a :PodcastEpisode ;
	:name "Title as on YouTube, without the | Ep 15 suffix" ;
	:hasDuration [:duration "PT1H23M45S"^^xsd:duration] ;
	:episodeNumber "15"^^xsd:decimal ;
	:releaseDate [:universalDate "2026-10-15"^^xsd:date] ;
	:partOf :PhilosophyProgramsAndPrompts ;
	:url-Youtube "https://www.youtube.com/watch?v=VIDEO_ID"^^xsd:anyURI ;
	:url-Spotify "https://open.spotify.com/episode/EPISODE_ID"^^xsd:anyURI ;
	:url-ApplePodcasts "https://podcasts.apple.com/us/podcast/SLUG/id6793816529?i=EPISODE_ID"^^xsd:anyURI ;
	:description """The summary paragraphs from the YouTube description.""" ;
	:hasPart :PPP-Episode15-Segment01,
		:PPP-Episode15-Segment02 ;
	:cites :Source-... ;
.

:PPP-Episode15-Segment01 a :PodcastSegment ;
	:name "Cold Open" ;
	:startingTimestamp "PT0S"^^xsd:duration ;
.

:PPP-Episode15-Segment02 a :PodcastSegment ;
	:name "Welcome to Philosophy, Programs, and Prompts" ;
	:startingTimestamp "PT32S"^^xsd:duration ;
.
```

- **Episode numbers** are decimals, so in-between and split episodes sort correctly:
  `3.5`, `6.1`, `6.2`. The page URL uses the same number: `/ppp/episode/6.1/`.
- **Chapters:** one segment per YouTube chapter. Their order comes from
  `:startingTimestamp`, so the order you write them in doesn't matter. Write times as
  durations: `PT0S`, `PT1M9S`, `PT1H5M14S`.
- **Links:** store clean links only.
  - YouTube: `https://www.youtube.com/watch?v=…` (not `youtu.be`)
  - Spotify: no `?si=…`
  - Apple: the `/us/` store link, without `l=…`
- **Latest episode:** whichever has the highest number is marked "New" on the landing page.


## Adding an Ontology Explained video by hand

`add.py` writes this block for you. To write one by hand: a video's IRI follows the source rule below (`Source-` + uuid5 of its watch URL), so that a
PPP episode citing the video and this file describe the same node. Work it out first:

```bash
python3 -c 'import uuid,sys; print("Source-" + str(uuid.uuid5(uuid.NAMESPACE_URL, sys.argv[1])))' \
  "https://www.youtube.com/watch?v=VIDEO_ID"
```

Then copy this block into `ontology-explained/ontology_explained_data.ttl` after the other
videos (oldest first). Start it with a `# VIDEO_ID · Title` comment so the file stays readable.

```turtle
# VIDEO_ID · Title as on YouTube
:Source-xxxxxxxx-xxxx-5xxx-xxxx-xxxxxxxxxxxx a :Video ;
	:name "Title as on YouTube" ;
	:hasDuration [:duration "PT27M47S"^^xsd:duration] ;
	:releaseDate [:universalDate "2026-10-15"^^xsd:date ; :universalDateTime "2026-10-15T09:30:00-07:00"^^xsd:dateTime] ;
	:partOf :OntologyExplained, :Source-<playlist> ;	# name the playlists in a comment
	:createdBy :CaseyHart ;
	:url-Youtube "https://www.youtube.com/watch?v=VIDEO_ID"^^xsd:anyURI ;
	:description """The summary paragraphs from the YouTube description.""" ;
	:cites :Source-... ,	# what it is
		:Source-... ;	# what it is
.
```

- **Release date:** the YouTube upload date. `:universalDateTime` (the upload time) is
  optional; it orders videos uploaded on the same day. YouTube shows it in the page source
  as `publishDate`.
- **Series:** add the playlist's IRI to `:partOf`. Playlists are near the top of the file. A
  new playlist is a `:Playlist` block like the others, `:partOf :OntologyExplained`, named
  with the source rule applied to `https://www.youtube.com/playlist?list=PLAYLIST_ID`.
  Within a series, videos are listed in release order.
- **Description:** summary paragraphs only: no chapters, links, hashtags or calls to action.
  Lines starting with `- ` become a bulleted list.
- **Chapters:** only the ones you wrote, as `:VideoSegment`s named `<video IRI>-Segment01`
  and so on, exactly like PPP's (`:name`, `:startingTimestamp`), listed in `:hasPart`.
- **Links in the show notes:** cite another of your videos by its IRI, a PPP episode by its
  episode IRI (`:PPP-Episode3.5`), anything else as a source (next section).
- **Page URL:** `/ontology-explained/<slug>/`, where the slug comes from the title
  ("What is ontology?" → `what-is-ontology`). **Renaming a video changes its URL.** The
  build then lists the old folder; delete it if nobody links to it, or keep it.


## Adding a source (either show)

Source IRIs are made from the source's best identifier, so the same source always gets
the same IRI, in either file. Each episode or video that cites it then points at the same
node without you having to look anything up.

Use the best identifier available:

1. DOI (`https://doi.org/…`)
2. arXiv (`https://arxiv.org/abs/…`)
3. ISBN (`urn:isbn:…`)
4. the cleaned-up URL (no `si=`, `utm_…` or `t=`, and no trailing `/`)
5. for something with no link: `urn:x-title:` plus the lowercased title

Then (or let `add.py` do all of this for the links in a description):

```bash
python3 -c 'import uuid,sys; print("Source-" + str(uuid.uuid5(uuid.NAMESPACE_URL, sys.argv[1])))' \
  "https://www.youtube.com/watch?v=F5ajyr5VzS0"
# Source-b17168c4-4868-5adb-b342-6ca813e2ade2
```

```turtle
:Source-b17168c4-4868-5adb-b342-6ca813e2ade2 a :Video ;     # :Video for YouTube, :Content otherwise
	:createdBy :CarlBrown ;                                    # only when one of us made it
	:name "Definitions of AI and How Companies Use Them to Lie" ;
	:url-Youtube "https://www.youtube.com/watch?v=F5ajyr5VzS0"^^xsd:anyURI ;   # :uri for non-YouTube links
.
```

If the IRI is already in either file, just cite it. If it's in the other show's file and you
also describe it in this one, give it exactly the same `:name`: the build stops if one node
has two names. Don't re-mint an existing source when you find a better identifier later. Add
that identifier as another `:uri` instead.


## What the build checks

The build stops instead of publishing broken pages if:

- an episode or video has no release date, length, or YouTube link
- a chapter starts after its episode or video ends
- a cited source has no `:name`
- anything has more than one `:name` (usually the same source named differently in the two files)

It also lists old episode or video folders that are no longer in the data, for example after
you renumber an episode or rename a video. Delete those yourself.


## How it works

`build.py` loads both `.ttl` files into one in-memory graph with rdflib and runs the queries:

| Query                  | Returns                                                         |
|------------------------|-----------------------------------------------------------------|
| `ppp/show.rq`          | the podcast's links and description                             |
| `ppp/hosts.rq`         | the people who `:createdBy` the podcast                         |
| `ppp/episodes.rq`      | each episode's fields                                           |
| `ppp/segments.rq`      | episode chapters (follows `:hasPart` or `:partOf`, since there's no OWL reasoning) |
| `ppp/cites.rq`         | what each episode cites                                         |
| `ppp/related.rq`       | pairs of episodes that cite the same sources                    |
| `oe/channel.rq`        | the channel's name, description and link                        |
| `oe/playlists.rq`      | the channel's playlists (its series)                            |
| `oe/videos.rq`         | each video's fields, oldest first                               |
| `oe/membership.rq`     | which series each video is in                                   |
| `oe/segments.rq`       | video chapters                                                  |
| `shared/cites.rq`      | every citation either show makes                                |
| `shared/related.rq`    | pairs of episodes/videos, from either show, that cite the same sources |
| `shared/names.rq`      | anything with two different names (a data problem)              |

It then fills the templates and writes the HTML. The graph exists only while the script
runs; there is no triplestore to keep running.

`add.py` loads the same two files to see what's already there, then inserts Turtle text into
them (so your formatting, comments and ordering stay as they are): episodes after the last
episode, PPP sources under a `#### First cited in Episode N` heading above `##### TOPICS`,
videos after the last video, and Ontology Explained sources in A-Z order in their section. It
checks that both files still parse before it saves anything.
