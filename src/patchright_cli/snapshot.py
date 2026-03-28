"""Generate YAML accessibility tree snapshots from Patchright pages.

Uses a two-pass approach:
1. TreeWalker in strict document order to assign data-patchright-ref attributes
2. Recursive tree builder to produce a nested YAML structure

This guarantees ref order matches DOM order (unlike recursive walk which
can assign refs depth-first, causing mismatches).
"""

from __future__ import annotations

import time
from pathlib import Path

_SNAPSHOT_JS = r"""
(() => {
    const SKIP = new Set(["SCRIPT","STYLE","NOSCRIPT","SVG","LINK","META","BR","HR"]);
    const INTERACTIVE = new Set(["A","BUTTON","INPUT","TEXTAREA","SELECT","DETAILS","SUMMARY","LABEL"]);
    const SEMANTIC = new Set(["H1","H2","H3","H4","H5","H6","NAV","MAIN","HEADER","FOOTER","ARTICLE","SECTION","ASIDE","FORM","TABLE","UL","OL","LI","IMG","VIDEO","AUDIO","IFRAME"]);

    document.querySelectorAll("[data-patchright-ref]").forEach(el => el.removeAttribute("data-patchright-ref"));

    function getRole(el) {
        const ar = el.getAttribute && el.getAttribute("role");
        if (ar) return ar;
        const t = el.tagName;
        const m = {A:"link",BUTTON:"button",TEXTAREA:"textbox",SELECT:"combobox",IMG:"img",TABLE:"table",FORM:"form",NAV:"navigation",MAIN:"main",HEADER:"banner",FOOTER:"contentinfo"};
        if (m[t]) return m[t];
        if (t === "INPUT") { const tp = (el.type||"text").toLowerCase(); return tp==="checkbox"?"checkbox":tp==="radio"?"radio":(tp==="submit"||tp==="button")?"button":"textbox"; }
        if (t === "UL" || t === "OL") return "list";
        if (t === "LI") return "listitem";
        if (/^H[1-6]$/.test(t)) return "heading";
        return t.toLowerCase();
    }

    function getName(el) {
        const t = el.tagName;
        const ar = el.getAttribute && el.getAttribute("aria-label");
        if (ar) return ar.substring(0, 120);
        if (t === "IMG") return (el.getAttribute("alt") || "").substring(0, 120);
        if (t === "INPUT" || t === "TEXTAREA") return (el.getAttribute("placeholder") || "").substring(0, 80);
        if (t === "A" || t === "BUTTON" || /^H[1-6]$/.test(t) || t === "LABEL" || t === "LI" || t === "SUMMARY")
            return (el.innerText || "").replace(/\s+/g, " ").trim().substring(0, 120);
        const ti = el.getAttribute && el.getAttribute("title");
        if (ti) return ti.substring(0, 80);
        return "";
    }

    function isVis(el) {
        if (!el.offsetParent && el.tagName !== "BODY" && el.tagName !== "HTML") {
            try { const pos = getComputedStyle(el).position; if (pos !== "fixed" && pos !== "sticky") return false; }
            catch(e) { return false; }
        }
        try { const s = getComputedStyle(el); return s.display !== "none" && s.visibility !== "hidden" && s.opacity !== "0"; }
        catch(e) { return false; }
    }

    function shouldTag(el) {
        return INTERACTIVE.has(el.tagName) || SEMANTIC.has(el.tagName) || !!el.getAttribute("role") || !!el.getAttribute("data-testid") || !!getName(el);
    }

    // Pass 1: TreeWalker in strict document order — assign refs
    const tw = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT, {
        acceptNode(n) {
            if (SKIP.has(n.tagName)) return NodeFilter.FILTER_REJECT;
            if (!isVis(n)) return NodeFilter.FILTER_REJECT;
            return NodeFilter.FILTER_ACCEPT;
        }
    });

    let counter = 0;
    const flatList = [];
    let node;
    while (node = tw.nextNode()) {
        if (shouldTag(node)) {
            counter++;
            const ref = "e" + counter;
            node.setAttribute("data-patchright-ref", ref);
            const role = getRole(node);
            const name = getName(node);
            const entry = { ref, role, name, tag: node.tagName };
            if (node.tagName === "INPUT" || node.tagName === "TEXTAREA" || node.tagName === "SELECT") {
                if (node.value) entry.value = node.value.substring(0, 200);
                if (node.type === "checkbox" || node.type === "radio") entry.checked = node.checked;
                if (node.disabled) entry.disabled = true;
            }
            if (node.tagName === "A" && node.href) entry.url = node.href.substring(0, 200);
            if (/^H[1-6]$/.test(node.tagName)) entry.level = parseInt(node.tagName[1]);
            flatList.push(entry);
        }
    }

    // Pass 2: Build nested tree reading refs from DOM attributes
    function buildTree(el, depth) {
        if (depth > 12 || !el || !el.tagName) return null;
        if (SKIP.has(el.tagName)) return null;
        const ref = el.getAttribute("data-patchright-ref");
        const children = [];
        for (const ch of el.children) {
            if (!isVis(ch)) continue;
            const c = buildTree(ch, depth + 1);
            if (c) children.push(c);
        }
        if (!ref && !children.length) return null;
        const out = {};
        if (ref) {
            const f = flatList.find(x => x.ref === ref);
            if (f) Object.assign(out, f);
            delete out.tag;
        }
        if (children.length) out.children = children;
        return out;
    }

    const tree = buildTree(document.body, 0) || { role: "document", name: document.title, children: [] };
    return { tree, flatList };
})()
"""

# Variant that takes a root element argument for partial snapshots
_SNAPSHOT_ELEMENT_JS = r"""
(rootEl) => {
    const SKIP = new Set(["SCRIPT","STYLE","NOSCRIPT","SVG","LINK","META","BR","HR"]);
    const INTERACTIVE = new Set(["A","BUTTON","INPUT","TEXTAREA","SELECT","DETAILS","SUMMARY","LABEL"]);
    const SEMANTIC = new Set(["H1","H2","H3","H4","H5","H6","NAV","MAIN","HEADER","FOOTER","ARTICLE","SECTION","ASIDE","FORM","TABLE","UL","OL","LI","IMG","VIDEO","AUDIO","IFRAME"]);

    rootEl.querySelectorAll("[data-patchright-ref]").forEach(el => el.removeAttribute("data-patchright-ref"));

    function getRole(el) {
        const ar = el.getAttribute && el.getAttribute("role");
        if (ar) return ar;
        const t = el.tagName;
        const m = {A:"link",BUTTON:"button",TEXTAREA:"textbox",SELECT:"combobox",IMG:"img",TABLE:"table",FORM:"form",NAV:"navigation",MAIN:"main",HEADER:"banner",FOOTER:"contentinfo"};
        if (m[t]) return m[t];
        if (t === "INPUT") { const tp = (el.type||"text").toLowerCase(); return tp==="checkbox"?"checkbox":tp==="radio"?"radio":(tp==="submit"||tp==="button")?"button":"textbox"; }
        if (t === "UL" || t === "OL") return "list";
        if (t === "LI") return "listitem";
        if (/^H[1-6]$/.test(t)) return "heading";
        return t.toLowerCase();
    }

    function getName(el) {
        const t = el.tagName;
        const ar = el.getAttribute && el.getAttribute("aria-label");
        if (ar) return ar.substring(0, 120);
        if (t === "IMG") return (el.getAttribute("alt") || "").substring(0, 120);
        if (t === "INPUT" || t === "TEXTAREA") return (el.getAttribute("placeholder") || "").substring(0, 80);
        if (t === "A" || t === "BUTTON" || /^H[1-6]$/.test(t) || t === "LABEL" || t === "LI" || t === "SUMMARY")
            return (el.innerText || "").replace(/\s+/g, " ").trim().substring(0, 120);
        const ti = el.getAttribute && el.getAttribute("title");
        if (ti) return ti.substring(0, 80);
        return "";
    }

    function isVis(el) {
        if (!el.offsetParent && el.tagName !== "BODY" && el.tagName !== "HTML") {
            try { const pos = getComputedStyle(el).position; if (pos !== "fixed" && pos !== "sticky") return false; }
            catch(e) { return false; }
        }
        try { const s = getComputedStyle(el); return s.display !== "none" && s.visibility !== "hidden" && s.opacity !== "0"; }
        catch(e) { return false; }
    }

    function shouldTag(el) {
        return INTERACTIVE.has(el.tagName) || SEMANTIC.has(el.tagName) || !!el.getAttribute("role") || !!el.getAttribute("data-testid") || !!getName(el);
    }

    const tw = document.createTreeWalker(rootEl, NodeFilter.SHOW_ELEMENT, {
        acceptNode(n) {
            if (SKIP.has(n.tagName)) return NodeFilter.FILTER_REJECT;
            if (!isVis(n)) return NodeFilter.FILTER_REJECT;
            return NodeFilter.FILTER_ACCEPT;
        }
    });

    let counter = 0;
    const flatList = [];
    let node;
    while (node = tw.nextNode()) {
        if (shouldTag(node)) {
            counter++;
            const ref = "e" + counter;
            node.setAttribute("data-patchright-ref", ref);
            const role = getRole(node);
            const name = getName(node);
            const entry = { ref, role, name, tag: node.tagName };
            if (node.tagName === "INPUT" || node.tagName === "TEXTAREA" || node.tagName === "SELECT") {
                if (node.value) entry.value = node.value.substring(0, 200);
                if (node.type === "checkbox" || node.type === "radio") entry.checked = node.checked;
                if (node.disabled) entry.disabled = true;
            }
            if (node.tagName === "A" && node.href) entry.url = node.href.substring(0, 200);
            if (/^H[1-6]$/.test(node.tagName)) entry.level = parseInt(node.tagName[1]);
            flatList.push(entry);
        }
    }

    function buildTree(el, depth) {
        if (depth > 12 || !el || !el.tagName) return null;
        if (SKIP.has(el.tagName)) return null;
        const ref = el.getAttribute("data-patchright-ref");
        const children = [];
        for (const ch of el.children) {
            if (!isVis(ch)) continue;
            const c = buildTree(ch, depth + 1);
            if (c) children.push(c);
        }
        if (!ref && !children.length) return null;
        const out = {};
        if (ref) {
            const f = flatList.find(x => x.ref === ref);
            if (f) Object.assign(out, f);
            delete out.tag;
        }
        if (children.length) out.children = children;
        return out;
    }

    const tree = buildTree(rootEl, 0) || { role: "element", name: "", children: [] };
    return { tree, flatList };
}
"""


def _walk_tree(node: dict, depth: int = 0) -> list[str]:
    """Recursively walk the parsed tree and produce YAML lines."""
    lines: list[str] = []
    ref = node.get("ref", "")
    role = node.get("role", "")
    name = node.get("name", "")

    if ref:
        indent = "  " * depth
        lines.append(f"{indent}- ref: {ref}")
        lines.append(f"{indent}  role: {role}")
        if name:
            lines.append(f"{indent}  name: {_yaml_escape(name)}")
        for key in ("value", "url"):
            val = node.get(key, "")
            if val:
                lines.append(f"{indent}  {key}: {_yaml_escape(str(val))}")
        if node.get("checked") is not None:
            lines.append(f"{indent}  checked: {str(node['checked']).lower()}")
        if node.get("disabled"):
            lines.append(f"{indent}  disabled: true")
        if node.get("level") is not None:
            lines.append(f"{indent}  level: {node['level']}")

        children = node.get("children", [])
        if children:
            lines.append(f"{indent}  children:")
            for child in children:
                lines.extend(_walk_tree(child, depth + 1))
    else:
        for child in node.get("children", []):
            lines.extend(_walk_tree(child, depth))

    return lines


def _yaml_escape(s: str) -> str:
    if not s:
        return '""'
    if any(
        c in s
        for c in (
            "\\",
            ":",
            "#",
            "'",
            '"',
            "\n",
            "\r",
            "[",
            "]",
            "{",
            "}",
            ",",
            "&",
            "*",
            "?",
            "|",
            "-",
            "<",
            ">",
            "=",
            "!",
            "%",
            "@",
            "`",
        )
    ):
        escaped = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")
        return f'"{escaped}"'
    return s


async def take_snapshot(page, root_element=None) -> tuple[str, dict[str, dict]]:
    """Take a DOM snapshot. Returns (yaml_text, ref_map).

    If root_element is provided, only snapshot the subtree under that element.
    """
    result = None
    if root_element:
        try:
            result = await root_element.evaluate(_SNAPSHOT_ELEMENT_JS)
        except Exception:
            pass
    else:
        try:
            result = await page.evaluate(_SNAPSHOT_JS, isolated_context=False)
        except TypeError:
            try:
                result = await page.evaluate(_SNAPSHOT_JS)
            except Exception:
                pass
        except Exception:
            pass

    if not result or not result.get("tree"):
        return "# Empty page - no accessible elements found\n", {}

    flat_list = result.get("flatList", [])
    tree = result.get("tree", {})

    refs: dict[str, dict] = {}
    for item in flat_list:
        refs[item["ref"]] = item

    lines = _walk_tree(tree)
    yaml_text = "\n".join(lines) + "\n" if lines else "# Empty page - no accessible elements found\n"
    return yaml_text, refs


def save_snapshot(yaml_text: str, cwd: str | None = None) -> str:
    """Save snapshot YAML to .patchright-cli/ directory."""
    base = Path(cwd) if cwd else Path.cwd()
    snap_dir = base / ".patchright-cli"
    snap_dir.mkdir(parents=True, exist_ok=True)

    timestamp = int(time.time() * 1000)
    filename = f"page-{timestamp}.yml"
    filepath = snap_dir / filename
    filepath.write_text(yaml_text, encoding="utf-8")
    return str(filepath)
