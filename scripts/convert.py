#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将 MD / DOCX / XLSX / CSV 转换为微信兼容的内联样式 HTML。

仅使用 Python 标准库（zipfile + xml.etree + csv + re），零第三方依赖。
转换产物为带 <body> 的 HTML 片段，供 wechat_push_draft.py 的 extract_body 提取。

注意（第一批范围）：
- DOCX / XLSX 内的嵌入图片暂不自动提取上传，仅转换文字与表格。
- 若源文件含重要图片，请先告诉我，我们再扩展图片上传环节。
"""

import csv
import os
import re
import zipfile
import xml.etree.ElementTree as ET

# ---------- 命名空间 ----------
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
SP = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
RNS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def w(t):
    return "{%s}%s" % (W, t)


def sp(t):
    return "{%s}%s" % (SP, t)


def rns(t):
    return "{%s}%s" % (RNS, t)


HEADING_SIZES = {1: 22, 2: 20, 3: 18, 4: 16, 5: 15, 6: 14}


def _esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def wrap_html(inner):
    return (
        '<body style="margin:0;padding:0;">'
        '<section style="font-size:15px;line-height:1.9;color:#333;'
        'max-width:100%;word-break:break-word;padding:0 4px;">'
        + inner
        + "</section></body>"
    )


# ================= Markdown =================
def _md_inline(text):
    text = _esc(text)
    text = re.sub(
        r"`([^`]+)`",
        r'<code style="background:#f5f5f5;padding:2px 4px;border-radius:3px;font-size:13px;">\1</code>',
        text,
    )
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(r"~~([^~]+)~~", r"<del>\1</del>", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    return text


def _md_is_special(line):
    s = line.strip()
    if not s:
        return True
    if re.match(r"^#{1,6}\s", line):
        return True
    if re.match(r"^\s*[-*+]\s+", line):
        return True
    if re.match(r"^\s*\d+\.\s+", line):
        return True
    if s.startswith(">"):
        return True
    if s.startswith("```"):
        return True
    if re.match(r"^\s*([-*_])\1{2,}\s*$", line):
        return True
    return False


def _build_table(header, rows):
    th = "".join(
        f'<th style="border:1px solid #ddd;padding:8px 10px;background:#f3f6fb;'
        f'font-weight:bold;text-align:left;">{_md_inline(h)}</th>'
        for h in header
    )
    trs = []
    for r in rows:
        tds = "".join(
            f'<td style="border:1px solid #ddd;padding:8px 10px;">{_md_inline(c)}</td>'
            for c in r
        )
        trs.append(f"<tr>{tds}</tr>")
    return (
        '<table style="border-collapse:collapse;width:100%;margin:12px 0;'
        'font-size:14px;word-break:break-word;"><thead><tr>'
        + th
        + "</tr></thead><tbody>"
        + "".join(trs)
        + "</tbody></table>"
    )


def convert_md(text):
    lines = text.split("\n")
    out = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        if line.strip().startswith("```"):
            i += 1
            buf = []
            while i < n and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            code = "\n".join(buf)
            out.append(
                '<pre style="background:#f5f5f5;padding:12px;border-radius:6px;'
                'overflow:auto;font-size:13px;line-height:1.6;"><code>'
                + _esc(code)
                + "</code></pre>"
            )
            continue
        if re.match(r"^\s*([-*_])\1{2,}\s*$", line):
            out.append('<hr style="border:none;border-top:1px solid #eee;margin:18px 0;">')
            i += 1
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            lvl = len(m.group(1))
            size = HEADING_SIZES[lvl]
            out.append(
                f'<h{lvl} style="font-size:{size}px;font-weight:bold;'
                f'margin:20px 0 10px;line-height:1.4;">{_md_inline(m.group(2))}</h{lvl}>'
            )
            i += 1
            continue
        if line.strip().startswith(">"):
            buf = []
            while i < n and lines[i].strip().startswith(">"):
                buf.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            out.append(
                '<blockquote style="border-left:4px solid #d0d0d0;padding:8px 14px;'
                'color:#666;background:#fafafa;margin:12px 0;">'
                + _md_inline(" ".join(buf))
                + "</blockquote>"
            )
            continue
        if (
            "|" in line
            and i + 1 < n
            and "-" in lines[i + 1]
            and re.match(r"^\s*\|?[\s:|-]+\|?\s*$", lines[i + 1])
        ):
            header = [c.strip() for c in line.strip().strip("|").split("|")]
            i += 2
            rows = []
            while i < n and "|" in lines[i] and lines[i].strip():
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            out.append(_build_table(header, rows))
            continue
        if re.match(r"^\s*[-*+]\s+", line):
            buf = []
            while i < n and re.match(r"^\s*[-*+]\s+", lines[i]):
                item = re.sub(r"^\s*[-*+]\s+", "", lines[i])
                buf.append(f'<li style="margin:4px 0;">{_md_inline(item)}</li>')
                i += 1
            out.append('<ul style="padding-left:22px;margin:10px 0;">' + "".join(buf) + "</ul>")
            continue
        if re.match(r"^\s*\d+\.\s+", line):
            buf = []
            while i < n and re.match(r"^\s*\d+\.\s+", lines[i]):
                item = re.sub(r"^\s*\d+\.\s+", "", lines[i])
                buf.append(f'<li style="margin:4px 0;">{_md_inline(item)}</li>')
                i += 1
            out.append('<ol style="padding-left:22px;margin:10px 0;">' + "".join(buf) + "</ol>")
            continue
        if not line.strip():
            i += 1
            continue
        buf = [line]
        i += 1
        while i < n and not _md_is_special(lines[i]):
            buf.append(lines[i])
            i += 1
        out.append(
            '<p style="margin:10px 0;line-height:1.9;">'
            + _md_inline(" ".join(buf))
            + "</p>"
        )
    return wrap_html("\n".join(out))


# ================= DOCX =================
def _docx_runs(p):
    parts = []
    for r in p.findall(w("r")):
        rpr = r.find(w("rPr"))
        b = i = False
        if rpr is not None:
            if rpr.find(w("b")) is not None:
                b = True
            if rpr.find(w("i")) is not None:
                i = True
        t = r.find(w("t"))
        txt = _esc(t.text) if (t is not None and t.text) else ""
        if b and i:
            txt = f"<strong><em>{txt}</em></strong>"
        elif b:
            txt = f"<strong>{txt}</strong>"
        elif i:
            txt = f"<em>{txt}</em>"
        parts.append(txt)
    return "".join(parts)


def _docx_paragraph(p):
    style = None
    ppr = p.find(w("pPr"))
    if ppr is not None:
        pstyle = ppr.find(w("pStyle"))
        if pstyle is not None:
            style = pstyle.get(w("val"))
    text = _docx_runs(p)
    if not text.strip():
        return ""
    if style and re.search(r"(\d)", style):
        lvl = min(int(re.search(r"(\d)", style).group(1)), 6)
        size = HEADING_SIZES[lvl]
        return (
            f'<h{lvl} style="font-size:{size}px;font-weight:bold;margin:20px 0 10px;">'
            f"{text}</h{lvl}>"
        )
    return f'<p style="margin:10px 0;line-height:1.9;">{text}</p>'


def _docx_table(tbl):
    html_rows = []
    for ri, tr in enumerate(tbl.findall(w("tr"))):
        tds = []
        for tc in tr.findall(w("tc")):
            txts = []
            for p in tc.findall(w("p")):
                txts.append(_docx_runs(p))
            cell = " ".join(txts)
            st = "border:1px solid #ddd;padding:8px 10px;"
            if ri == 0:
                st += "background:#f3f6fb;font-weight:bold;"
            tds.append(f'<td style="{st}">{cell}</td>')
        html_rows.append("<tr>" + "".join(tds) + "</tr>")
    return (
        '<table style="border-collapse:collapse;width:100%;margin:12px 0;'
        'font-size:14px;word-break:break-word;">' + "".join(html_rows) + "</table>"
    )


def convert_docx(path):
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    root = ET.fromstring(xml)
    body = root.find(w("body"))
    if body is None:
        return wrap_html("")
    out = []
    for child in body:
        tag = child.tag.split("}")[-1]
        if tag == "p":
            out.append(_docx_paragraph(child))
        elif tag == "tbl":
            out.append(_docx_table(child))
    return wrap_html("\n".join(out))


# ================= XLSX =================
def _split_ref(ref):
    m = re.match(r"([A-Z]+)(\d+)", ref)
    letters, row = m.group(1), int(m.group(2))
    col = 0
    for ch in letters:
        col = col * 26 + (ord(ch) - 64)
    return col, row


def convert_xlsx(path):
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        shared = []
        if "xl/sharedStrings.xml" in names:
            sx = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in sx.findall(sp("si")):
                shared.append("".join(t.text or "" for t in si.iter(sp("t"))))
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        sheets = []
        for s in wb.iter(sp("sheet")):
            name = s.get("name")
            rid = s.get(rns("id"))
            sheets.append((name, rid))
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        relmap = {r.get("Id"): r.get("Target") for r in rels}
        tables = []
        for name, rid in sheets:
            target = relmap.get(rid)
            if not target:
                continue
            if not target.startswith("xl/"):
                target = "xl/" + target
            if target not in names:
                continue
            sx = ET.fromstring(z.read(target))
            data = {}
            maxr = maxc = 0
            for c in sx.iter(sp("c")):
                ref = c.get("r")
                if not ref:
                    continue
                col, row = _split_ref(ref)
                t = c.get("t")
                val = ""
                if t == "s":
                    v = c.find(sp("v"))
                    if v is not None and v.text is not None:
                        val = shared[int(v.text)]
                else:
                    v = c.find(sp("v"))
                    if v is not None:
                        val = v.text or ""
                    is_ = c.find(sp("is"))
                    if is_ is not None:
                        val = "".join(tt.text or "" for tt in is_.iter(sp("t")))
                data[(row, col)] = val
                maxr, maxc = max(maxr, row), max(maxc, col)
            rows_html = []
            for r in range(1, maxr + 1):
                tds = []
                for c in range(1, maxc + 1):
                    v = _esc(data.get((r, c), ""))
                    st = "border:1px solid #ddd;padding:8px 10px;"
                    if r == 1:
                        st += "background:#f3f6fb;font-weight:bold;"
                    tds.append(f'<td style="{st}">{v}</td>')
                rows_html.append("<tr>" + "".join(tds) + "</tr>")
            tbl = (
                f'<p style="font-weight:bold;margin:14px 0 6px;">{_esc(name)}</p>'
                '<table style="border-collapse:collapse;width:100%;margin:8px 0 16px;'
                'font-size:14px;word-break:break-word;">'
                + "".join(rows_html)
                + "</table>"
            )
            tables.append(tbl)
    return wrap_html("\n".join(tables))


# ================= CSV =================
def convert_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    if not rows:
        return wrap_html("")
    header, body = rows[0], rows[1:]
    th = "".join(
        f'<th style="border:1px solid #ddd;padding:8px 10px;background:#f3f6fb;'
        f'font-weight:bold;text-align:left;">{_esc(h)}</th>'
        for h in header
    )
    trs = []
    for r in body:
        tds = "".join(
            f'<td style="border:1px solid #ddd;padding:8px 10px;">{_esc(c)}</td>' for c in r
        )
        trs.append(f"<tr>{tds}</tr>")
    tbl = (
        '<table style="border-collapse:collapse;width:100%;margin:12px 0;'
        'font-size:14px;word-break:break-word;"><thead><tr>'
        + th
        + "</tr></thead><tbody>"
        + "".join(trs)
        + "</tbody></table>"
    )
    return wrap_html(tbl)


# ================= 调度 =================
def convert_to_html(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".html", ".htm"):
        with open(path, encoding="utf-8") as f:
            return f.read()
    if ext == ".md":
        with open(path, encoding="utf-8") as f:
            return convert_md(f.read())
    if ext == ".docx":
        return convert_docx(path)
    if ext in (".xlsx", ".xlsm"):
        return convert_xlsx(path)
    if ext == ".csv":
        return convert_csv(path)
    raise ValueError("不支持的格式: " + ext)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        print(convert_to_html(sys.argv[1]))
