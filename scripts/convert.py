#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将 MD / DOCX / XLSX / CSV 转换为微信兼容的内联样式 HTML。

仅使用 Python 标准库（zipfile + xml.etree + csv + re），零第三方依赖。
转换产物为带 <body> 的 HTML 片段，供 wechat_push_draft.py 的 extract_body 提取。

图片处理（已支持自动提取）：
- DOCX / XLSX 内的嵌入图片会自动提取，落地为 <源文件目录>/wechat_extracted_media/ 下的本地文件，
  并在正文 HTML 中以相对路径 <img> 引用。推送时由 wechat_push_draft.py 自动上传为微信永久素材并替换为 url。
- DOCX 图片按文档顺序精确嵌入；XLSX 图片统一附于表格之后（XLSX 不支持图片与单元格精确绑定）。
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
    # 先把 HTML 式删除线标签 <s>/<del> 保留为带样式的占位符，避免被 _esc 破坏
    placeholders = []
    def stash_html_strike(m):
        placeholders.append(m.group(2))
        return f"\x00STRIKE{len(placeholders)-1}\x00"
    text = re.sub(r"</?(s|del)(?:\s[^>]*)?>([^<]+)</\1>", stash_html_strike, text, flags=re.I)

    text = _esc(text)

    # 恢复 HTML 式删除线为统一样式
    def restore_strike(m):
        idx = int(m.group(1))
        return f'<del style="text-decoration:line-through;color:#999;">{placeholders[idx]}</del>'
    text = re.sub(r"\x00STRIKE(\d+)\x00", restore_strike, text)

    text = re.sub(
        r"`([^`]+)`",
        r'<code style="background:#f5f5f5;padding:2px 4px;border-radius:3px;font-size:13px;">\1</code>',
        text,
    )
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(
        r"~~([^~]+)~~",
        r'<del style="text-decoration:line-through;color:#999;">\1</del>',
        text,
    )
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


def _md_parse_list(lines, start, n):
    """解析从 start 开始的列表块（支持基于缩进的嵌套 ul/ol），返回 (html, next_index)。"""

    def build(i, base_indent):
        nodes = []  # 每项: (tag, content, children)
        while i < n:
            line = lines[i]
            m = re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$", line)
            if not m:
                # 允许列表项之间有空行（下一行仍是列表则跳过空行）
                if line.strip() == "" and i + 1 < n and re.match(
                    r"^(\s*)([-*+]|\d+[.)])\s+", lines[i + 1]
                ):
                    i += 1
                    continue
                break
            indent = len(m.group(1))
            tag = "ol" if re.match(r"^\d+[.)]$", m.group(2)) else "ul"
            content = m.group(3)
            # 任务列表：已勾选 [x]/[X] 加删除线，未勾选 [ ] 显示为未勾选框
            tm = re.match(r"^\[([xX ])\]\s*(.*)", content)
            if tm:
                mark = "☑" if tm.group(1).strip().lower() == "x" else "☐"
                rest = tm.group(2)
                if tm.group(1).strip().lower() == "x":
                    content = f"{mark} ~~{rest}~~"
                else:
                    content = f"{mark} {rest}"
            if nodes and indent < base_indent:
                break
            if not nodes:
                base_indent = indent
            i += 1
            children = []
            if i < n:
                nm = re.match(r"^(\s*)([-*+]|\d+[.)])\s+", lines[i])
                if nm and len(nm.group(1)) > indent:
                    children, i = build(i, len(nm.group(1)))
            nodes.append((tag, content, children))
        return nodes, i

    def render(nodes):
        if not nodes:
            return ""
        cur_tag = nodes[0][0]
        buf = [f'<{cur_tag} style="padding-left:22px;margin:8px 0;">']
        for tag, content, children in nodes:
            if tag != cur_tag:
                buf.append(f"</{cur_tag}>")
                cur_tag = tag
                buf.append(f'<{tag} style="padding-left:22px;margin:8px 0;">')
            buf.append(f'<li style="margin:4px 0;">{_md_inline(content)}')
            if children:
                buf.append(render(children))
            buf.append("</li>")
        buf.append(f"</{cur_tag}>")
        return "".join(buf)

    tree, ni = build(start, -1)
    return render(tree), ni


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
        if re.match(r"^(\s*)([-*+]|\d+[.)])\s+", line):
            html, i = _md_parse_list(lines, i, n)
            out.append(html)
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
def _docx_run_text(r):
    """从单个 w:r 提取带加粗/斜体的文本。"""
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
    return txt


def _docx_extract_drawing(drawing, media_dir):
    """从 w:drawing 提取内嵌图片，落地到 media_dir，返回 <img> 标签或 ''。"""
    emb = None
    for el in drawing.iter():
        tag = el.tag.split("}")[-1]
        if tag == "blip":
            emb = el.get(rns("embed"))
            if emb:
                break
    if not emb:
        return ""
    # relmap 在 convert_docx 中已把 rId 映射为 media 文件名
    fname = RELMAP.get(emb)
    if not fname:
        return ""
    src = os.path.join(media_dir, fname)
    if not os.path.exists(src):
        return ""
    rel = "wechat_extracted_media/" + os.path.basename(fname)
    return (
        f'<p style="margin:10px 0;line-height:0;">'
        f'<img src="{rel}" style="width:100%;max-width:100%;display:block;" '
        f'data-type="png"></p>'
    )


def _docx_block(container, media_dir):
    """处理段落或单元格内的 run 文本与图片，按文档顺序串联。"""
    parts = []
    for child in container:
        tag = child.tag.split("}")[-1]
        if tag == "r":
            parts.append(_docx_run_text(child))
        elif tag == "drawing":
            parts.append(_docx_extract_drawing(child, media_dir))
    return "".join(parts)


def _docx_paragraph(p, media_dir):
    style = None
    ppr = p.find(w("pPr"))
    if ppr is not None:
        pstyle = ppr.find(w("pStyle"))
        if pstyle is not None:
            style = pstyle.get(w("val"))
    text = _docx_block(p, media_dir)
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


def _docx_table(tbl, media_dir):
    html_rows = []
    for ri, tr in enumerate(tbl.findall(w("tr"))):
        tds = []
        for tc in tr.findall(w("tc")):
            txts = []
            for p in tc.findall(w("p")):
                txts.append(_docx_block(p, media_dir))
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


def _docx_build_relmap(z):
    """读取 word/_rels/document.xml.rels，返回 {rId: media文件名}。"""
    try:
        rels_xml = z.read("word/_rels/document.xml.rels").decode("utf-8")
    except KeyError:
        return {}
    try:
        rels = ET.fromstring(rels_xml)
    except ET.ParseError:
        return {}
    relmap = {}
    for rel in rels:
        rid = rel.get("Id")
        target = rel.get("Target", "")
        if target:
            relmap[rid] = os.path.basename(target)
    return relmap


def _docx_extract_media(z, media_dir):
    """解压 word/media/* 到 media_dir。"""
    os.makedirs(media_dir, exist_ok=True)
    for name in z.namelist():
        if name.startswith("word/media/") and not name.endswith("/"):
            data = z.read(name)
            with open(os.path.join(media_dir, os.path.basename(name)), "wb") as f:
                f.write(data)


# 模块级缓存：convert_docx 调用期间存放 rId→media 文件名映射，供 _docx_extract_drawing 使用
RELMAP = {}


def convert_docx(path):
    media_dir = os.path.join(os.path.dirname(os.path.abspath(path)), "wechat_extracted_media")
    with zipfile.ZipFile(path) as z:
        relmap = _docx_build_relmap(z)
        _docx_extract_media(z, media_dir)
        xml = z.read("word/document.xml").decode("utf-8")
    global RELMAP
    RELMAP = relmap
    root = ET.fromstring(xml)
    body = root.find(w("body"))
    if body is None:
        return wrap_html("")
    out = []
    for child in body:
        tag = child.tag.split("}")[-1]
        if tag == "p":
            out.append(_docx_paragraph(child, media_dir))
        elif tag == "tbl":
            out.append(_docx_table(child, media_dir))
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
    media_dir = os.path.join(os.path.dirname(os.path.abspath(path)), "wechat_extracted_media")
    media_imgs = []
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        # 提取内嵌图片（统一附于表格之后）
        for name in names:
            if name.startswith("xl/media/") and not name.endswith("/"):
                os.makedirs(media_dir, exist_ok=True)
                data = z.read(name)
                bname = os.path.basename(name)
                with open(os.path.join(media_dir, bname), "wb") as f:
                    f.write(data)
                media_imgs.append(bname)
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
            table = (
                f'<p style="font-weight:bold;margin:14px 0 6px;">{_esc(name)}</p>'
                '<table style="border-collapse:collapse;width:100%;margin:8px 0 16px;'
                'font-size:14px;word-break:break-word;">'
                + "".join(rows_html)
                + "</table>"
            )
            # 宽表防护：用横向滚动容器包裹，避免撑破手机端页面布局
            scroll = (
                '<div style="overflow-x:auto;-webkit-overflow-scrolling:touch;'
                'width:100%;max-width:100%;">'
                + table
                + "</div>"
            )
            tables.append(scroll)
    parts = list(tables)
    if media_imgs:
        parts.append('<p style="font-weight:bold;margin:14px 0 6px;">内嵌图片</p>')
        for bname in media_imgs:
            rel = "wechat_extracted_media/" + bname
            parts.append(
                f'<p style="margin:10px 0;line-height:0;">'
                f'<img src="{rel}" style="width:100%;max-width:100%;display:block;" '
                f'data-type="png"></p>'
            )
    return wrap_html("\n".join(parts))


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
    table = (
        '<table style="border-collapse:collapse;width:100%;margin:12px 0;'
        'font-size:14px;word-break:break-word;"><thead><tr>'
        + th
        + "</tr></thead><tbody>"
        + "".join(trs)
        + "</tbody></table>"
    )
    # 宽表防护：用横向滚动容器包裹，避免撑破手机端页面布局
    scroll = (
        '<div style="overflow-x:auto;-webkit-overflow-scrolling:touch;'
        'width:100%;max-width:100%;">'
        + table
        + "</div>"
    )
    return wrap_html(scroll)


# ================= 提取作者 =================
def extract_author(path):
    """从源文件提取作者名，提取不到返回 None。

    支持：
    - MD：文首/文末 `author: xxx` 或 `作者：xxx`（大小写/全半角冒号均可）
    - HTML：<meta name="author" content="xxx">，或文中 `作者：xxx`
    - DOCX：在 document.xml 中搜索 `作者：xxx`
    - XLSX / CSV：无作者概念，返回 None
    """
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".md":
            with open(path, encoding="utf-8") as f:
                text = f.read()
            m = re.search(r"^(?:author|作者)\s*[:：]\s*(.+)$", text, re.I | re.M)
            if m:
                return m.group(1).strip().strip("*").strip()
        elif ext in (".html", ".htm"):
            with open(path, encoding="utf-8") as f:
                text = f.read()
            m = re.search(
                r'<meta\s+name=["\']author["\']\s+content=["\']([^"\']+)["\']',
                text, re.I,
            )
            if not m:
                m = re.search(r"^(?:author|作者)\s*[:：]\s*(.+)$", text, re.I | re.M)
            if m:
                return m.group(1).strip()
        elif ext == ".docx":
            with zipfile.ZipFile(path) as z:
                xml = z.read("word/document.xml").decode("utf-8")
            m = re.search(r"作者\s*[:：]\s*([^<>\n]{1,30})", xml)
            if m:
                return m.group(1).strip()
    except Exception:
        return None
    return None


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
