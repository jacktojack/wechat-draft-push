#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微信公众号草稿箱自动推送脚本（CLI）
直接调用微信官方接口，保留样式化 HTML 内联样式；支持文档模式与图片列表模式。

两种推送模式：
1. 文档模式：source_file 为 MD/DOCX/XLSX/CSV/HTML，自动转 HTML 后推送。
2. 图片模式：配置 image_files 列表，将多张图片上传为永久素材并嵌入正文。

使用前：在同目录放一个 wechat_config.json（含敏感凭证，勿外传）：
{
  "appid": "你的公众号AppID",
  "appsecret": "你的公众号AppSecret",
  "cover_image": "cover.png",
  "cover_size": "900x383",
  "cover_prompt": "",  # 留空=按文章内容自动匹配风格；非空=用指定提示词
  "title": "文章标题",
  "author": "龙猫爸爸",
  "digest": "摘要",

  "source_file": "article.md",
  "image_files": ["cover_clean.png", "news_01.png", "news_02.png"]
}

注意：
- cover_image 若缺失，由 AI 代理先根据文章内容生成匹配风格的封面（cover_prompt 留空则自动推断主题，非空则用指定提示词），再落地为 cover_image。
- image_files 与 source_file 同时存在时，优先使用 image_files（图片模式）。
- 图片模式下，每张图都会上传为微信永久素材，返回 url 后嵌入正文。

运行：python wechat_push_draft.py
"""

import json
import os
import sys
import urllib.request
import urllib.error

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "wechat_config.json")

sys.path.insert(0, BASE)
from convert import convert_to_html  # noqa: E402


def load_config():
    if not os.path.exists(CONFIG_PATH):
        print(f"[错误] 找不到配置文件：{CONFIG_PATH}")
        print("请先创建 wechat_config.json（含 appid/appsecret/cover_image/title）。")
        sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def http_get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_post_json(url, payload):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": "Mozilla/5.0",
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def upload_image(access_token, image_path):
    """上传图片为永久素材，返回包含 media_id 与 url 的 JSON。"""
    ext = os.path.splitext(image_path)[1].lower()
    mime = "image/png" if ext == ".png" else "image/jpeg"
    fname = "image" + ext
    url = f"https://api.weixin.qq.com/cgi-bin/material/add_material?access_token={access_token}&type=image"
    boundary = "----WeChatPushBoundary"
    with open(image_path, "rb") as f:
        raw = f.read()
    body = (
        b"--" + boundary.encode() + b"\r\n"
        + f'Content-Disposition: form-data; name="media"; filename="{fname}"\r\n'.encode()
        + f"Content-Type: {mime}\r\n\r\n".encode()
        + raw + b"\r\n"
        + b"--" + boundary.encode() + b"--\r\n"
    )
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "User-Agent": "Mozilla/5.0",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def extract_body(html):
    """提取 <body>...</body> 内容作为图文正文（微信 content 接受 HTML 片段）。"""
    import re
    m = re.search(r"<body[^>]*>(.*?)</body>", html, re.S | re.I)
    return m.group(1).strip() if m else html


def build_image_content(image_urls):
    """把多张微信素材图 URL 拼成公众号正文 HTML。"""
    parts = [
        '<section style="font-size:15px;line-height:1.9;color:#333;'
        'max-width:100%;word-break:break-word;padding:0;margin:0;">'
    ]
    for url in image_urls:
        parts.append(
            '<p style="margin:0 0 8px 0;padding:0;line-height:0;">'
            f'<img src="{url}" style="width:100%;max-width:100%;display:block;" data-type="png">'
            '</p>'
        )
    parts.append('</section>')
    return "\n".join(parts)


def main():
    cfg = load_config()
    appid = cfg["appid"]
    appsecret = cfg["appsecret"]
    cover_path = os.path.join(BASE, cfg["cover_image"])

    print("[1/4] 获取 access_token ...")
    token_url = (f"https://api.weixin.qq.com/cgi-bin/token"
                 f"?grant_type=client_credential&appid={appid}&secret={appsecret}")
    token_resp = http_get_json(token_url)
    if "access_token" not in token_resp:
        print(f"[失败] 获取 access_token 出错：{token_resp}")
        print("常见原因：AppID/AppSecret 错误，或调用方 IP 不在公众号 IP 白名单。")
        sys.exit(1)
    access_token = token_resp["access_token"]
    print(f"      access_token 获取成功（有效期 {token_resp.get('expires_in', '?')} 秒）")

    print("[2/4] 上传封面图 ...")
    if not os.path.exists(cover_path):
        print(f"[警告] 找不到封面图 {cover_path}，草稿接口需要 thumb_media_id。")
        sys.exit(1)
    up_resp = upload_image(access_token, cover_path)
    if "media_id" not in up_resp:
        print(f"[失败] 上传封面图出错：{up_resp}")
        sys.exit(1)
    thumb_media_id = up_resp["media_id"]
    print(f"      封面 media_id: {thumb_media_id}")

    print("[3/4] 准备正文 HTML ...")
    image_files = cfg.get("image_files")
    if image_files:
        print(f"      图片模式：共 {len(image_files)} 张图待上传")
        image_urls = []
        for idx, img in enumerate(image_files, 1):
            img_path = os.path.join(BASE, img)
            if not os.path.exists(img_path):
                print(f"[失败] 找不到图片 {img}：{img_path}")
                sys.exit(1)
            print(f"            [{idx}/{len(image_files)}] 上传 {img} ...", end=" ")
            img_resp = upload_image(access_token, img_path)
            if "url" not in img_resp:
                print(f"失败：{img_resp}")
                sys.exit(1)
            image_urls.append(img_resp["url"])
            print(f"url 获取成功")
        content = build_image_content(image_urls)
        print(f"      正文已生成（{len(content)} 字符，含 {len(image_urls)} 张图）")
    else:
        src = cfg.get("source_file") or cfg.get("html_file")
        if not src:
            print("[错误] 配置缺少 source_file（或 html_file）或 image_files，请指定待推送内容。")
            sys.exit(1)
        src_path = os.path.join(BASE, src)
        if not os.path.exists(src_path):
            print(f"[失败] 找不到源文件：{src_path}")
            sys.exit(1)
        print(f"      文档模式：源文件 {src}（自动识别后缀，MD/WORD/EXCEL/CSV/HTML 均可）")
        html_text = convert_to_html(src_path)
        content = extract_body(html_text)
        print(f"      正文长度：{len(content)} 字符")

    print("[4/4] 调用草稿箱接口 draft/add ...")
    draft_url = f"https://api.weixin.qq.com/cgi-bin/draft/add?access_token={access_token}"
    article = {
        "title": cfg.get("title", "未命名图文"),
        "author": cfg.get("author", ""),
        "digest": cfg.get("digest", ""),
        "content": content,
        "thumb_media_id": thumb_media_id,
        "need_open_comment": 0,
        "only_fans_can_comment": 0,
    }
    draft_resp = http_post_json(draft_url, {"articles": [article]})
    if draft_resp.get("errcode", 0) != 0:
        print(f"[失败] 草稿推送出错：{draft_resp}")
        sys.exit(1)
    media_id = draft_resp.get("media_id")
    print(f"[成功] 已推送到草稿箱！草稿 media_id = {media_id}")
    print("去 mp.weixin.qq.com → 内容与互动 → 草稿箱 查看。")


if __name__ == "__main__":
    main()
