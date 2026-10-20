#!/usr/bin/env python3
"""Static validation for the Seedance AI Studio single-page site.

Dependency-free checks (Python 3.9+, no pip installs) that fail only on real
problems:

  1. required files exist
  2. index.html is well-formed: balanced tags, required head elements,
     no duplicate ids
  3. every local file reference (href/src) resolves to a file in the repo
  4. ids referenced from the inline JavaScript exist in the markup
  5. inline <script> blocks pass `node --check` (skipped if node is absent)

Usage:  python .github/scripts/validate_site.py
Exit code 0 = site is valid.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REQUIRED_FILES = ("index.html", "README.md")
HTML_FILE = "index.html"

VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}
# Elements whose end tag may legally be omitted in HTML5.
OPTIONAL_END_TAGS = {
    "p", "li", "dt", "dd", "option", "tr", "td", "th",
    "thead", "tbody", "tfoot", "rt", "rp",
}
EXTERNAL_PREFIXES = (
    "http://", "https://", "//", "mailto:", "tel:", "data:",
    "javascript:", "blob:", "#",
)

errors: list[str] = []
warnings: list[str] = []


class SiteParser(HTMLParser):
    """Collects ids, references, scripts and reports tag-balance problems."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, int]] = []
        self.ids: list[str] = []
        self.refs: list[tuple[str, str, int]] = []
        self.meta: list[dict[str, str | None]] = []
        self.html_attrs: dict[str, str | None] = {}
        self.title = ""
        self.scripts: list[tuple[int, str]] = []
        self._script: list[str] | None = None
        self._script_line = 0
        self._in_title = False

    # -- tags ------------------------------------------------------------
    def handle_starttag(self, tag, attrs):
        attrs_d = {k: (v if v is not None else "") for k, v in attrs}
        line = self.getpos()[0]

        for key in ("href", "src", "poster", "data"):
            value = attrs_d.get(key)
            if value:
                self.refs.append((key, value, line))

        if "id" in attrs_d:
            self.ids.append(attrs_d["id"])

        if tag == "html":
            self.html_attrs = attrs_d
        elif tag == "meta":
            self.meta.append(attrs_d)
        elif tag == "title":
            self._in_title = True
        elif tag == "script" and not attrs_d.get("src"):
            self._script = []
            self._script_line = line

        if tag in VOID_TAGS:
            return
        if tag in OPTIONAL_END_TAGS:
            while self.stack and self.stack[-1][0] == tag:
                self.stack.pop()
        self.stack.append((tag, line))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS and self.stack and self.stack[-1][0] == tag:
            self.stack.pop()

    def handle_endtag(self, tag):
        line = self.getpos()[0]
        if tag == "title":
            self._in_title = False
        if tag == "script" and self._script is not None:
            self.scripts.append((self._script_line, "".join(self._script)))
            self._script = None
        if tag in VOID_TAGS:
            return
        if tag in OPTIONAL_END_TAGS:
            if self.stack and self.stack[-1][0] == tag:
                self.stack.pop()
            return
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                for open_tag, open_line in self.stack[index + 1:]:
                    message = f"{HTML_FILE}:{open_line}: <{open_tag}> is not closed"
                    (warnings if open_tag in OPTIONAL_END_TAGS else errors).append(message)
                del self.stack[index:]
                return
        errors.append(f"{HTML_FILE}:{line}: stray closing tag </{tag}>")

    # -- text ------------------------------------------------------------
    def handle_data(self, data):
        if self._script is not None:
            self._script.append(data)
        elif self._in_title:
            self.title += data.strip()

    def close(self):
        super().close()
        for tag, line in self.stack:
            message = f"{HTML_FILE}:{line}: <{tag}> is never closed"
            (warnings if tag in OPTIONAL_END_TAGS else errors).append(message)
        self.stack.clear()


def check_required_files() -> None:
    for name in REQUIRED_FILES:
        if not (ROOT / name).is_file():
            errors.append(f"missing required file: {name}")


def check_html() -> SiteParser | None:
    path = ROOT / HTML_FILE
    if not path.is_file():
        return None

    parser = SiteParser()
    try:
        parser.feed(path.read_text(encoding="utf-8"))
        parser.close()
    except Exception as exc:  # noqa: BLE001 - report any parse failure
        errors.append(f"{HTML_FILE}: could not be parsed as HTML: {exc}")
        return None

    if not parser.html_attrs:
        errors.append(f"{HTML_FILE}: missing <html> element")
    if not (parser.html_attrs.get("lang") or "").strip():
        errors.append(f"{HTML_FILE}: <html> element has no lang attribute")
    if not parser.title:
        errors.append(f"{HTML_FILE}: missing or empty <title>")
    if not any((m.get("charset") or "").strip() for m in parser.meta):
        errors.append(f"{HTML_FILE}: missing <meta charset>")
    if not any((m.get("name") or "").lower() == "viewport" for m in parser.meta):
        errors.append(f"{HTML_FILE}: missing <meta name=\"viewport\">")

    seen: set[str] = set()
    for value in parser.ids:
        if value in seen:
            errors.append(f"{HTML_FILE}: duplicate id=\"{value}\"")
        seen.add(value)

    return parser


def check_references(parser: SiteParser) -> None:
    for attr, raw, line in parser.refs:
        value = raw.strip()
        if not value or value.lower().startswith(EXTERNAL_PREFIXES):
            continue
        if value.startswith("/"):
            warnings.append(f"{HTML_FILE}:{line}: root-relative reference \"{value}\" may break on GitHub Pages")
            continue
        target = value.split("#", 1)[0].split("?", 1)[0]
        if not target:
            continue
        if not (ROOT / target).is_file():
            errors.append(f"{HTML_FILE}:{line}: broken local reference \"{value}\"")


def check_js_ids(parser: SiteParser) -> None:
    known = set(parser.ids)
    pattern = re.compile(
        r"""(?:getElementById\(\s*['"]([A-Za-z][\w:.-]*)['"]"""
        r"""|querySelector(?:All)?\(\s*['"]#([A-Za-z][\w:.-]*)['"])"""
    )
    for line, body in parser.scripts:
        for match in pattern.finditer(body):
            element_id = match.group(1) or match.group(2)
            if element_id not in known:
                errors.append(
                    f"{HTML_FILE}: inline script (line {line}) references "
                    f"missing element id=\"{element_id}\""
                )


def check_inline_js(parser: SiteParser) -> None:
    node = shutil.which("node")
    if not node:
        warnings.append("node not found - skipped JavaScript syntax check")
        return
    for line, body in parser.scripts:
        if not body.strip():
            continue
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "inline.js"
            script.write_text(body, encoding="utf-8")
            result = subprocess.run(
                [node, "--check", str(script)],
                capture_output=True,
                text=True,
            )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip().splitlines()
            tail = detail[-3:] if detail else ["unknown error"]
            errors.append(
                f"{HTML_FILE}: inline script (line {line}) has a syntax error: "
                + " / ".join(tail)
            )


def main() -> int:
    check_required_files()
    parser = check_html()
    if parser is not None:
        check_references(parser)
        check_js_ids(parser)
        check_inline_js(parser)

    for message in warnings:
        print(f"warning: {message}")
    for message in errors:
        print(f"error: {message}", file=sys.stderr)

    if errors:
        print(f"\nFAILED: {len(errors)} error(s), {len(warnings)} warning(s)")
        return 1
    print(f"OK: site is valid ({len(warnings)} warning(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
