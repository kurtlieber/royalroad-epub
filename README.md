# rr2epub

Download a [Royal Road](https://www.royalroad.com) fiction, or any range of its
chapters, as a clean EPUB 3 book.

It started as a workaround for browser extensions like WebToEpub stalling a few
chapters into a long download. Royal Road rate-limits clients that fetch pages
too quickly. `rr2epub` fetches slowly, backs off when the site says so, and
caches what it has already downloaded, so a long book finishes and later runs
only fetch new chapters.

## What you get

- **Chapter ranges by chapter number**: `--from 201` means "Chapter 201", not
  the 201st entry in the list. That matters for books where early chapters were
  removed from Royal Road (for example, after an Amazon release).
- **No author's notes**: the boxes above and below each chapter are dropped.
  Use `--keep-author-notes` to keep them.
- **No hidden watermark text**: Royal Road inserts invisible anti-piracy lines
  (*"If you discover this narrative on Amazon, report the violation"*) into
  chapter text. Many scrapers carry them into the EPUB, where e-readers show
  them. `rr2epub` removes them.
- **Polite fetching**: a fixed delay between requests (4 s by default), plus
  exponential backoff that honors `Retry-After` on HTTP 429 and 5xx responses.
- **Incremental updates**: raw chapter pages are cached in `~/.cache/rr2epub/`.
  Rerunning after new chapters are posted fetches only the new ones.
- **A standard EPUB 3**: cover, table of contents and metadata, with chapter
  text rewritten as well-formed XHTML.

## Install

Requires Python 3.10+.

```bash
pipx install git+https://github.com/kurtlieber/royalroad-epub
# or
pip install git+https://github.com/kurtlieber/royalroad-epub
```

## Usage

```bash
# Whole book
rr2epub https://www.royalroad.com/fiction/92144/the-legend-of-william-oh

# See chapter numbers first
rr2epub 92144 --list

# Chapter 201 onward, to a specific file
rr2epub 92144 --from 201 -o ~/Books/william-oh-201+.epub

# A fixed range, keeping author's notes
rr2epub 92144 --from 201 --to 250 --keep-author-notes
```

| Option | Default | Description |
| --- | --- | --- |
| `fiction` | (required) | Fiction URL or numeric id |
| `-f`, `--from N` | first available | First chapter number to include |
| `-t`, `--to N` | latest | Last chapter number to include |
| `-o`, `--output PATH` | `<Title>[_<from>-<to>].epub` | Output file |
| `-d`, `--delay SECS` | `4` | Delay between chapter requests |
| `--cache-dir PATH` | `~/.cache/rr2epub` | Where raw chapter pages are cached |
| `--refresh` | off | Re-download cached chapters (e.g. after the author edits them) |
| `--keep-author-notes` | off | Keep author's notes |
| `--list` | | Print chapter numbers and titles, then exit |

A chapter's number comes from its title (`Chapter 42: …`, `Ch. 42 …`). Chapters
without a number in their title use their position in the chapter list.

## Limitations

- Images inside chapter text are left out, so the book doesn't depend on
  remote links. The cover is included.
- Only public chapters are downloaded. Patreon-locked or premium chapters are
  out of scope.
- Author's notes are detected by Royal Road's note boxes. A note the author
  typed into the chapter text itself stays in.
- This depends on Royal Road's page structure (`window.chapters`,
  `.chapter-content`, `.author-note`). If the site changes, it may break.

## Be a good citizen

Use this for personal offline reading of stories you can already read for free.
Don't redistribute the books it produces: the stories belong to their authors.
Royal Road authors make a living from Patreon, premium chapters and published
editions, so if you enjoy a story, support the author. Keep the request delay
reasonable; lowering it is how you get rate-limited in the first place.

## License

[MIT](LICENSE)
