"""Download Royal Road chapters and package them as an EPUB 3 book.

Fetches politely (fixed delay, backoff on 429/5xx), caches raw chapter pages
so reruns only fetch new chapters, and strips author's notes plus Royal
Road's hidden anti-piracy watermark lines.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path

from lxml import etree
from lxml import html as lhtml

from . import __version__

BASE = "https://www.royalroad.com"
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0 Safari/537.36"
)
RETRY_STATUS = {429, 500, 502, 503, 504}
KEEP_ATTRS = {"href", "colspan", "rowspan"}
CSS = """\
body { font-family: serif; line-height: 1.45; }
h2 { text-align: center; margin: 1em 0 1.5em; }
p { margin: 0 0 0.8em; }
hr { border: 0; border-top: 1px solid #888; margin: 1.5em 20%; }
table { border-collapse: collapse; margin: 1em auto; }
td, th { border: 1px solid #888; padding: 0.2em 0.5em; }
.author-note { border-top: 1px solid #888; border-bottom: 1px solid #888; margin: 1.5em 0; padding: 0.5em 0; font-style: italic; }
"""


@dataclass
class Chapter:
    id: int
    title: str
    url: str
    number: int  # "Chapter N" from the title, else 1-based position


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def fetch(url: str, *, binary: bool = False, retries: int = 8):
    delay = 30
    for _ in range(retries):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
                if binary:
                    return data, r.headers.get_content_type()
                return data.decode("utf-8")
        except urllib.error.HTTPError as e:
            if e.code not in RETRY_STATUS:
                raise
            wait = int(e.headers.get("Retry-After") or delay)
            log(f"  HTTP {e.code}; waiting {wait}s")
        except (urllib.error.URLError, TimeoutError) as e:
            wait = delay
            log(f"  {e}; waiting {wait}s")
        time.sleep(wait)
        delay = min(delay * 2, 300)
    raise RuntimeError(f"giving up on {url}")


def fiction_url(arg: str) -> tuple[int, str]:
    m = re.search(r"(?:/fiction/)?(\d+)(?:/([\w-]+))?", arg)
    if not m:
        raise SystemExit(f"not a Royal Road fiction URL or id: {arg}")
    fid, slug = m.group(1), m.group(2)
    return int(fid), f"{BASE}/fiction/{fid}" + (f"/{slug}" if slug else "")


def parse_fiction(page: str) -> tuple[dict, list[Chapter]]:
    raw = re.search(r"window\.chapters\s*=\s*(\[.*?\]);", page)
    if not raw:
        raise SystemExit("could not find the chapter list on the fiction page")
    chapters = []
    for pos, c in enumerate(json.loads(raw.group(1)), 1):
        title = re.sub(r"\s+", " ", c["title"]).strip()
        m = re.match(r"(?:chapter|ch\.?)\s*(\d+)", title, re.I)
        chapters.append(Chapter(c["id"], title, BASE + c["url"], int(m.group(1)) if m else pos))

    doc = lhtml.fromstring(page)

    def meta(prop: str) -> str | None:
        v = doc.xpath(f'//meta[@property="{prop}" or @name="{prop}"]/@content')
        return v[0].strip() if v else None

    title = meta("og:title") or doc.findtext(".//h1") or "Untitled"
    info = {
        "title": re.sub(r"\s*\|\s*Royal Road\s*$", "", title).strip(),
        "author": meta("books:author") or "Unknown",
        "description": meta("og:description") or "",
        "cover": meta("og:image"),
    }
    return info, chapters


def hidden_classes(doc) -> set[str]:
    """Classes that inline <style> blocks hide: Royal Road's watermark lines."""
    hidden = set()
    for css in doc.xpath("//style/text()"):
        for selectors, body in re.findall(r"([^{}]+)\{([^}]*)\}", css):
            if re.search(r"display\s*:\s*none|speak\s*:\s*never", body):
                hidden.update(re.findall(r"\.([\w-]+)", selectors))
    return hidden


def sanitize(el) -> str:
    """Strip scripts, images, and attributes; return well-formed XHTML."""
    for bad in el.xpath(".//script | .//style | .//img | .//iframe | .//form"):
        bad.drop_tree()
    for node in el.iter():
        if not isinstance(node.tag, str):
            continue
        for a in list(node.attrib):
            if a not in KEEP_ATTRS:
                del node.attrib[a]
    parts = [html.escape(el.text or "", quote=False)]
    parts += [etree.tostring(c, method="xml", encoding="unicode") for c in el]
    return "".join(parts)


def chapter_body(page: str, keep_notes: bool) -> str:
    doc = lhtml.fromstring(page)
    hidden = hidden_classes(doc)
    if hidden:
        for el in doc.xpath("//*[@class]"):
            if hidden.intersection(el.get("class").split()):
                el.drop_tree()

    content = doc.xpath('//div[contains(concat(" ", @class, " "), " chapter-content ")]')
    if not content:
        raise RuntimeError("chapter content not found")
    body = sanitize(content[0])
    if not keep_notes:
        return body

    # Author's notes live in separate boxes above/below the chapter text.
    order = {el: i for i, el in enumerate(doc.iter())}
    pivot = order[content[0]]
    before, after = [], []
    for note in doc.xpath('//div[contains(concat(" ", @class, " "), " author-note ")]'):
        block = f'<div class="author-note">{sanitize(note)}</div>'
        (before if order[note] < pivot else after).append(block)
    return "".join(before) + body + "".join(after)


def xhtml(title: str, body: str, *, css: bool = True, extra_ns: str = "") -> str:
    link = '<link rel="stylesheet" type="text/css" href="style.css"/>' if css else ""
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        f'<html xmlns="http://www.w3.org/1999/xhtml"{extra_ns} xml:lang="en" lang="en">'
        f"<head><title>{html.escape(title)}</title>{link}</head><body>{body}</body></html>"
    )


def build_epub(out: Path, info: dict, source: str, chapters: list[tuple[str, str]],
               cover: tuple[bytes, str] | None, subtitle: str) -> None:
    book_title = f"{info['title']} ({subtitle})" if subtitle else info["title"]
    book_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{source}#{subtitle}")
    modified = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    e = html.escape
    manifest, spine, toc = [], [], []

    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        put = lambda name, data: z.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)
        put("META-INF/container.xml",
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>')
        put("OEBPS/style.css", CSS)

        if cover:
            data, mime = cover
            ext = {"image/png": "png", "image/webp": "webp", "image/gif": "gif"}.get(mime, "jpg")
            mime = mime if ext != "jpg" else "image/jpeg"
            put(f"OEBPS/cover.{ext}", data)
            put("OEBPS/cover.xhtml", xhtml("Cover",
                f'<div style="text-align:center"><img src="cover.{ext}" alt="Cover" '
                'style="max-width:100%;max-height:100%"/></div>', css=False))
            manifest += [f'<item id="cover-img" href="cover.{ext}" media-type="{mime}" properties="cover-image"/>',
                         '<item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>']
            spine.append('<itemref idref="cover" linear="no"/>')

        for n, (title, body) in enumerate(chapters, 1):
            fn = f"c{n:04d}.xhtml"
            put(f"OEBPS/{fn}", xhtml(title, f"<h2>{e(title)}</h2>{body}"))
            manifest.append(f'<item id="c{n}" href="{fn}" media-type="application/xhtml+xml"/>')
            spine.append(f'<itemref idref="c{n}"/>')
            toc.append(f'<li><a href="{fn}">{e(title)}</a></li>')

        put("OEBPS/nav.xhtml", xhtml("Contents",
            f'<nav epub:type="toc" id="toc"><h1>Contents</h1><ol>{"".join(toc)}</ol></nav>',
            extra_ns=' xmlns:epub="http://www.idpf.org/2007/ops"'))
        desc = f"<dc:description>{e(info['description'])}</dc:description>" if info["description"] else ""
        put("OEBPS/content.opf", f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bid" xml:lang="en">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bid">urn:uuid:{book_id}</dc:identifier>
    <dc:title>{e(book_title)}</dc:title>
    <dc:creator>{e(info['author'])}</dc:creator>
    <dc:language>en</dc:language>
    <dc:source>{e(source)}</dc:source>
    <dc:publisher>Royal Road</dc:publisher>
    {desc}
    <meta property="dcterms:modified">{modified}</meta>
    {'<meta name="cover" content="cover-img"/>' if cover else ''}
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="css" href="style.css" media-type="text/css"/>
    {chr(10).join('    ' + m for m in manifest).strip()}
  </manifest>
  <spine>{"".join(spine)}</spine>
</package>
""")


def slugify(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_") or "book"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="rr2epub",
        description="Download a Royal Road fiction (or a chapter range of it) as a clean EPUB.",
    )
    p.add_argument("fiction", help="fiction URL (https://www.royalroad.com/fiction/12345/slug) or numeric id")
    p.add_argument("-f", "--from", dest="start", type=int, metavar="N",
                   help="first chapter number to include (default: first available)")
    p.add_argument("-t", "--to", dest="end", type=int, metavar="N",
                   help="last chapter number to include (default: latest)")
    p.add_argument("-o", "--output", type=Path, help="output .epub path (default: <Title>[_<range>].epub)")
    p.add_argument("-d", "--delay", type=float, default=4.0,
                   help="seconds between chapter requests (default: 4)")
    p.add_argument("--cache-dir", type=Path, default=Path.home() / ".cache" / "rr2epub",
                   help="where raw chapter pages are cached (default: ~/.cache/rr2epub)")
    p.add_argument("--refresh", action="store_true", help="re-download chapters even if cached")
    p.add_argument("--keep-author-notes", action="store_true", help="keep author's notes (dropped by default)")
    p.add_argument("--list", action="store_true", help="list chapters with their numbers and exit")
    p.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    a = p.parse_args(argv)

    fid, url = fiction_url(a.fiction)
    info, chapters = parse_fiction(fetch(url))

    if a.list:
        for c in chapters:
            print(f"{c.number:>5}  {c.title}")
        return 0

    picked = [c for c in chapters
              if (a.start is None or c.number >= a.start) and (a.end is None or c.number <= a.end)]
    if not picked:
        raise SystemExit("no chapters match that range (try --list)")

    subtitle = ""
    if a.start is not None or a.end is not None:
        subtitle = f"Ch. {picked[0].number}–{picked[-1].number}"
    out = a.output or Path(slugify(info["title"]) + (
        f"_{picked[0].number}-{picked[-1].number}" if subtitle else "") + ".epub")

    cache = a.cache_dir / str(fid)
    cache.mkdir(parents=True, exist_ok=True)
    log(f"{info['title']} by {info['author']}: {len(picked)} chapters")

    built = []
    for i, c in enumerate(picked, 1):
        path = cache / f"{c.id}.html"
        if a.refresh or not path.exists():
            log(f"[{i}/{len(picked)}] {c.title}")
            path.write_text(fetch(c.url), encoding="utf-8")
            if i < len(picked):
                time.sleep(a.delay)
        try:
            built.append((c.title, chapter_body(path.read_text(encoding="utf-8"), a.keep_author_notes)))
        except RuntimeError as e:
            raise SystemExit(f"{c.title}: {e} (cached page: {path})")

    cover = None
    if info["cover"] and info["cover"].startswith("http"):
        try:
            cover = fetch(info["cover"], binary=True)
        except Exception as e:  # cover is nice-to-have
            log(f"cover download failed: {e}")

    build_epub(out, info, url, built, cover, subtitle)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
