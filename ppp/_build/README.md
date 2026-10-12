# PPP website build

The PPP pages on caseyhart.com are generated from one data file. Edit the data or the
templates, run one command, then commit.

```
ppp/podcast_ontology_data.ttl      the data, edited by hand (published, so it's downloadable)
ppp/ppp.css                        styling for every PPP page, edited by hand
ppp/_build/build.py                the build script
ppp/_build/queries/*.rq            SPARQL queries the build runs over the data
ppp/_build/templates/*.html        page templates (Jinja2)
ppp/_build/requirements.txt        Python packages the build needs

ppp/index.html                     GENERATED: don't edit
ppp/episode/<number>/index.html    GENERATED: don't edit
ppp/sources/index.html             GENERATED: don't edit
```

GitHub Pages skips folders whose names start with `_`, so nothing in `ppp/_build/` is
published. (This relies on Pages' default Jekyll processing. If you ever add a
`.nojekyll` file, these files get published too. That's harmless, just clutter.)


## One-time setup

From the repo root:

```bash
pip install -r ppp/_build/requirements.txt
```

(Use a virtualenv if you prefer. The build only needs `rdflib` and `jinja2`.)


## Every time you change the data, a template, or the CSS

From the repo root:

```bash
python3 ppp/_build/build.py
```

- **Success:** `Built 16 episode pages, the landing page and the sources page (...)`
- **Data problems:** it prints `Problems in the data:` with a list, writes nothing, and
  exits. Fix the `.ttl` and run it again.

Preview locally before you push:

```bash
python3 -m http.server 8000      # still from the repo root
# open http://localhost:8000/ppp/
```

Then commit the data and the regenerated pages together:

```bash
git add ppp/
git commit -m "PPP: <what changed>"
git push
```

If you edit the `.ttl` in Emacs while something else is also changing it, revert the
buffer (`M-x revert-buffer`) before saving, so you don't save over the other changes.


## What to edit for common changes

| You want to change...                            | Edit                                   |
|--------------------------------------------------|----------------------------------------|
| An episode, chapter or source                    | `ppp/podcast_ontology_data.ttl`        |
| Landing-page copy, hero, host bios               | `ppp/_build/templates/index.html`      |
| Episode page layout                              | `ppp/_build/templates/episode.html`    |
| Sources page                                     | `ppp/_build/templates/sources.html`    |
| Header, footer, "Listen" band (every page)       | `ppp/_build/templates/base.html`       |
| Colors, fonts, spacing                           | `ppp/ppp.css`                          |
| What data the pages get                          | `ppp/_build/queries/*.rq`, then `assemble()` in `build.py` |

Run the build after any of these, including CSS-only changes. Run it anyway, even if
it's not strictly needed.


## Adding a new episode

Copy this block into the `.ttl` next to the other episodes and fill it in. The build needs
everything above `:description`. Use the YouTube upload date as the release date.

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


## Adding a source

Source IRIs are made from the source's best identifier, so the same source always gets
the same IRI. Each episode that cites it then points at the same node without you
having to look anything up.

Use the best identifier available:

1. DOI (`https://doi.org/…`)
2. arXiv (`https://arxiv.org/abs/…`)
3. ISBN (`urn:isbn:…`)
4. the cleaned-up URL (no `si=`, `utm_…` or `t=`, and no trailing `/`)
5. for something with no link: `urn:x-title:` plus the lowercased title

Then:

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

If the IRI is already in the file, just add it to the new episode's `:cites`. Don't
re-mint an existing source when you find a better identifier later. Add that
identifier as another `:uri` instead.

To cite one of our own episodes, use its episode IRI: `:cites :PPP-Episode3.5`.


## What the build checks

The build stops instead of publishing broken pages if:

- an episode has no release date, length, or YouTube link
- a chapter starts after the episode ends
- a cited source has no `:name`

It also lists old episode folders that are no longer in the data, for example after you
renumber an episode. Delete those yourself.


## How it works

`build.py` loads the `.ttl` into an in-memory graph with rdflib and runs each query in
`queries/`:

| Query          | Returns                                                         |
|----------------|-----------------------------------------------------------------|
| `show.rq`      | the show's links and description                                |
| `hosts.rq`     | the people who `:createdBy` the show                            |
| `episodes.rq`  | each episode's fields                                           |
| `segments.rq`  | chapters (follows `:hasPart` or `:partOf`, since there's no OWL reasoning) |
| `cites.rq`     | what each episode cites                                         |
| `related.rq`   | pairs of episodes that cite the same sources                    |

It then fills the templates and writes the HTML. The graph exists only while the script
runs; there is no triplestore to keep running.
