"""Accessibility regression tests for the frontend (§15).

Scope, honestly stated: this is a **static** check of `static/index.html`,
`static/app.js` and `static/style.css`. It is not axe-core - running axe needs a
real browser, and this project must test offline with no browser download. What
it does do is pin every invariant §15 established, so the specific regressions
that were present before it (zero aria attributes, `outline: none` on focusable
controls, icon-only buttons with no accessible name, a modal with no dialog role
or focus trap) cannot come back unnoticed.

Contrast is checked arithmetically against the tokens in style.css, which is
exact - no browser required.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")
JS = (STATIC / "app.js").read_text(encoding="utf-8")

VOID = {"input", "img", "br", "hr", "meta", "link", "use", "path", "circle", "rect"}


class Node:
    def __init__(self, tag: str, attrs: dict):
        self.tag = tag
        self.attrs = attrs
        self.children: list = []
        self.text = ""

    def all_text(self) -> str:
        return (self.text + " " + " ".join(c.all_text() for c in self.children)).strip()

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()

    def find_all(self, tag: str):
        return [n for n in self.walk() if n.tag == tag]

    def has_class(self, name: str) -> bool:
        return name in (self.attrs.get("class") or "").split()


class Tree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {})
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(Node(tag, dict(attrs)))

    def handle_endtag(self, tag):
        if tag not in VOID and len(self.stack) > 1:
            for i in range(len(self.stack) - 1, 0, -1):
                if self.stack[i].tag == tag:
                    del self.stack[i:]
                    break

    def handle_data(self, data):
        if data.strip():
            self.stack[-1].text += data.strip() + " "


@pytest.fixture(scope="module")
def dom() -> Node:
    t = Tree()
    t.feed(HTML)
    return t.root


def _accessible_name(node: Node) -> str:
    """Roughly WAI's accname: aria-label wins, else visible text, else .sr-only."""
    if node.attrs.get("aria-label"):
        return node.attrs["aria-label"]
    text = node.text.strip()
    for child in node.walk():
        if child is node:
            continue
        if child.has_class("sr-only"):
            text += " " + child.all_text()
        elif child.tag != "svg":
            text += " " + child.text.strip()
    return text.strip()


# ---------------------------------------------------------------------------
# Names and semantics
# ---------------------------------------------------------------------------

def test_every_button_has_an_accessible_name(dom):
    nameless = [b.attrs.get("id") or b.attrs.get("class") or "?"
                for b in dom.find_all("button")
                if not _accessible_name(b) and "display:none" not in (b.attrs.get("style") or "")]
    assert not nameless, f"buttons with no accessible name: {nameless}"


def _visible_text(node: Node) -> str:
    """Text a sighted user reads, ignoring <svg> and .sr-only content."""
    parts = [node.text.strip()]
    for c in node.children:
        if c.tag == "svg" or c.has_class("sr-only"):
            continue
        parts.append(_visible_text(c))
    return " ".join(p for p in parts if p).strip()


def test_icon_only_buttons_carry_screen_reader_text(dom):
    """A button that renders only an <svg> must name itself some other way."""
    for b in dom.find_all("button"):
        svgs = [c for c in b.children if c.tag == "svg"]
        if not svgs or _visible_text(b):
            continue
        has_sr = any(c.has_class("sr-only") for c in b.walk() if c is not b)
        assert b.attrs.get("aria-label") or has_sr, \
            f"icon-only button {b.attrs.get('id')} has no accessible name"


def test_every_decorative_icon_is_hidden_from_assistive_tech(dom):
    for svg in dom.find_all("svg"):
        if svg.has_class("sprite"):
            continue
        assert svg.attrs.get("aria-hidden") == "true" or svg.attrs.get("aria-label"), \
            f"svg without aria-hidden or aria-label: {svg.attrs}"


def test_every_form_control_is_labelled(dom):
    label_targets = {n.attrs["for"] for n in dom.find_all("label") if "for" in n.attrs}
    # a control nested inside a <label> is labelled by it
    nested = {c.attrs.get("id") for lbl in dom.find_all("label")
              for c in lbl.walk() if c.tag in ("input", "select", "textarea")}

    for tag in ("input", "select", "textarea"):
        for ctl in dom.find_all(tag):
            if ctl.attrs.get("type") in ("checkbox", "radio", "file", "hidden"):
                continue                       # checkboxes/radios sit inside their label
            if "display:none" in (ctl.attrs.get("style") or ""):
                continue                       # JS-compat shims, hidden + aria-hidden
            cid = ctl.attrs.get("id")
            assert (cid in label_targets or cid in nested or ctl.attrs.get("aria-label")), \
                f"<{tag} id={cid}> has no label"


def test_ids_are_unique(dom):
    ids = [n.attrs["id"] for n in dom.walk() if "id" in n.attrs]
    dupes = {i for i in ids if ids.count(i) > 1}
    assert not dupes, f"duplicate ids: {dupes}"


def test_document_declares_a_language(dom):
    assert 'lang="en"' in HTML


# ---------------------------------------------------------------------------
# Focus
# ---------------------------------------------------------------------------

def test_no_rule_removes_a_focus_indicator_without_replacing_it():
    """`outline: none` is only legitimate where a box-shadow ring replaces it.

    Before §15 this caught `.course-switcher:focus` and `.course-field input:focus`,
    which blanked the indicator outright.
    """
    css = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)      # comments aren't selectors
    offenders = []
    for block in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        selector, body = block.group(1).strip(), block.group(2)
        if "outline: none" not in body and "outline:none" not in body:
            continue
        replaced = "box-shadow: var(--focus)" in body or "box-shadow: none" in body
        allowed = selector in (":focus-visible", ":focus:not(:focus-visible)")
        if not (replaced or allowed):
            offenders.append(selector)
    assert not offenders, f"these strip the focus ring: {offenders}"


def test_a_visible_focus_ring_is_defined():
    assert ":focus-visible" in CSS and "--focus:" in CSS


def test_skip_link_targets_a_real_element(dom):
    link = next(n for n in dom.find_all("a") if n.has_class("skip-link"))
    target = link.attrs["href"].lstrip("#")
    assert any(n.attrs.get("id") == target for n in dom.walk()), \
        f"skip link points at #{target}, which does not exist"


# ---------------------------------------------------------------------------
# Live regions and dialogs
# ---------------------------------------------------------------------------

def test_toast_is_a_live_region(dom):
    toast = next(n for n in dom.walk() if n.attrs.get("id") == "toast")
    assert toast.attrs.get("aria-live") == "polite"
    assert toast.attrs.get("role") == "status"


def test_modals_are_dialogs_with_a_focus_trap():
    assert 'role: "dialog"' in JS and '"aria-modal": "true"' in JS
    assert "FOCUSABLE" in JS, "focus trap needs a focusable-element query"
    assert 'e.key === "Escape"' in JS
    assert "opener.focus()" in JS, "focus must return to the element that opened the dialog"


def test_no_modal_is_built_outside_the_shared_primitive():
    """Exactly one .modal-overlay is constructed - the one inside openModal(),
    which supplies the dialog role, the focus trap, Escape and focus return.
    Any second construction is a dialog that skipped all of that."""
    creations = re.findall(r'class:\s*"modal-overlay', JS)
    assert len(creations) == 1, \
        f"{len(creations)} .modal-overlay constructions; all dialogs must use openModal()"
    body = JS[JS.index("function openModal("):]
    assert 'class: "modal-overlay"' in body[:body.index("\n}")], \
        "the sole .modal-overlay must live inside openModal()"


# ---------------------------------------------------------------------------
# Icons: every <use> resolves to a symbol in the sprite
# ---------------------------------------------------------------------------

def test_every_icon_reference_resolves(dom):
    symbols = set(re.findall(r'<symbol id="(i-[a-z-]+)"', HTML))
    assert symbols, "sprite is missing"

    # strip comments: they document the pattern rather than reference an icon
    js = re.sub(r"//.*", "", re.sub(r"/\*.*?\*/", "", JS, flags=re.S))

    used = {n.attrs["href"].lstrip("#") for n in dom.find_all("use") if "href" in n.attrs}
    used |= set(re.findall(r'"#(i-[a-z-]+)"', js))
    used |= {"i-" + m for m in re.findall(r'\bicon\("([a-z-]+)"', js)}
    # icons named indirectly through a lookup table, e.g. TOAST_ICON
    used |= {"i-" + m for m in re.findall(r'^\s*\w+:\s*"([a-z-]+)",?\s*$', js, re.M)
             if "i-" + m in symbols}

    missing = used - symbols
    assert not missing, f"icons referenced but not in the sprite: {sorted(missing)}"


def test_no_emoji_is_used_as_an_icon():
    """Emoji were the old icon system (§14). They must not creep back."""
    def emoji(src: str):
        return {c for c in src
                if ord(c) > 0x2190 and c not in "–—’‘“”…·×→↗↑"}
    assert not emoji(HTML), f"emoji in index.html: {emoji(HTML)}"
    # app.js: allow arrows inside prose, flag pictographs
    pict = {c for c in JS if 0x1F000 <= ord(c) <= 0x1FAFF or 0x2600 <= ord(c) <= 0x27BF}
    assert not pict, f"emoji in app.js: {pict}"


# ---------------------------------------------------------------------------
# Contrast (arithmetic, exact - no browser needed)
# ---------------------------------------------------------------------------

def _tokens(scope: str) -> dict:
    """Pull custom properties out of a :root / [data-theme=dark] block."""
    m = re.search(re.escape(scope) + r"\s*\{(.*?)\n\}", CSS, re.S)
    assert m, f"no {scope} block in style.css"
    return {k: v for k, v in re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})\s*;", m.group(1))}


def _lum(hexs: str) -> float:
    def lin(c: float) -> float:
        c /= 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    h = hexs.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def _ratio(a: str, b: str) -> float:
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


# (foreground, background, minimum) - AA: 4.5 body text, 3.0 large/graphical
PAIRS = [
    ("ink", "paper", 4.5), ("ink", "surface", 4.5), ("ink", "surface-2", 4.5),
    ("muted", "paper", 4.5), ("muted", "surface", 4.5), ("muted", "surface-2", 4.5),
    ("brand", "surface", 4.5), ("brand", "paper", 4.5),
    ("brand-d", "brand-soft", 4.5),
    ("ok", "surface", 4.5), ("warn", "surface", 4.5), ("err", "surface", 4.5),
    ("ok", "ok-soft", 4.5), ("warn", "warn-soft", 4.5), ("err", "err-soft", 4.5),
    ("sidebar-ink", "sidebar", 4.5),
    ("sidebar-ink-dim", "sidebar", 3.0),
    ("sidebar-accent", "sidebar", 3.0),
    # text drawn on a filled accent - this is what white-on-teal got wrong in dark mode
    ("on-brand", "brand", 4.5), ("on-err", "err", 4.5),
    ("on-ok", "ok", 4.5), ("on-warn", "warn", 4.5),
]


@pytest.mark.parametrize("scope", [":root", '[data-theme="dark"]'])
def test_palette_meets_wcag_aa(scope):
    t = _tokens(scope)
    if scope != ":root":                     # dark overrides :root; inherit the rest
        base = _tokens(":root")
        base.update(t)
        t = base
    failures = [(fg, bg, round(_ratio(t[fg], t[bg]), 2), need)
                for fg, bg, need in PAIRS if _ratio(t[fg], t[bg]) < need]
    assert not failures, f"{scope} contrast failures: {failures}"


def test_toast_error_reports_via_toast_not_recursion():
    """toastError used to call itself and blow the stack on every API failure."""
    assert "function toastError(e) { toastError(e); }" not in JS
    assert "toast(errorText(e), \"err\")" in JS


def test_reduced_motion_is_honoured():
    assert "prefers-reduced-motion: reduce" in CSS


def test_simple_mode_hides_advanced_only_markup():
    assert '[data-level="simple"] [data-adv-only]' in CSS
    assert 'data-tab="semester" data-adv-only' in HTML
    # Leaving an Advanced-only panel must navigate home in Simple mode.
    assert 'tab?.hasAttribute("data-adv-only")' in JS or 'hasAttribute("data-adv-only")' in JS
    assert 'showTab("home")' in JS
    # Command palette hides Advanced-only actions in Simple mode.
    assert "advOnly: true" in JS
    assert "paletteActions()" in JS
    # Moodle step 2 stays a thin Speech handoff (no recommend overwrite).
    assert 'r.textContent = mqRecommend.ready' not in JS
    assert 'id="stt-overwrite"' in HTML
    assert 'id="mq-open-speech"' in HTML
    # Mid-width topbar reflow before the mobile drawer breakpoint.
    assert "@media (max-width: 1100px)" in CSS
    assert "@media (max-width: 900px)" in CSS
