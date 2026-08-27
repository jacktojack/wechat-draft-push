---
name: wechat-draft-push
description: 通过微信公众号官方草稿接口（draft/add）将文章自动推送进公众号草稿箱，支持 MD/WORD/EXCEL/CSV/HTML 多格式自动转换与封面图自动生成。当用户想把 Markdown/Word/Excel/CSV/HTML 文章推送进自己的公众号草稿箱、用 CLI 方式自动发布微信图文草稿、或需要保留内联样式（表格/清单/配色）而非被 Markdown 工具重排版覆盖时使用。涵盖 AppID/AppSecret 获取、IP 白名单、多格式自动转换、封面图自动生成（ImageGen，按文章内容风格匹配、极简美观）、永久素材封面、内联样式保留等关键要点。
agent_created: true
---

# 微信公众号草稿自动推送

## 概述
将本地文章（MD/WORD/EXCEL/CSV/HTML）通过微信官方 `draft/add` 接口推送到指定公众号的草稿箱，正文完整保留内联 CSS 样式（表格、清单、配色、字体），封面使用永久素材。适用于已拥有公众号 AppID/AppSecret 的用户，希望以 CLI 方式一次性或批量推送图文。非 HTML 格式由 `scripts/convert.py` 自动转为内联样式 HTML。

## 触发场景
- 用户说"把这篇文章推送到我的公众号草稿箱""用 CLI 自动推送微信草稿""自动发布到公众号"
- 用户已有 HTML/Markdown 文章，希望样式不被 Markdown 工具（如 wenyan-cli）洗掉
- 需要可复用的自动化推送流程

## 前置条件（需用户自行准备）
1. 公众号 **AppID** 与 **AppSecret**（公众平台 → 设置与开发 → 开发 → 基本配置）
2. **IP 白名单**：调用方公网 IP 须加入「基本配置 → IP 白名单」。沙箱/云环境的出口 IP 需用户确认后添加。
3. 一篇已生成的**文章**（`.md`/`.docx`/`.xlsx`/`.csv`/`.html` 均可，脚本按后缀自动转换；HTML 建议全内联样式、适配手机宽度、表格不溢出）
4. 一张**封面图**（JPG/PNG，建议 < 2MB，如 900×383）——若未提供，技能会用 ImageGen **根据文章内容自动生成风格匹配、极简美观的封面**（无需你指定风格）

## 工作流程
1. 基于 `scripts/wechat_config.example.json` 创建 `wechat_config.json`，填入 `appid`/`appsecret`/`cover_image`/`title` 等。`author` 留空则自动从源文件提取（见下「作者自动提取」），`default_author` 为兜底署名（默认「龙猫爸爸」）。
2. 准备封面图（自动优先，缺失时按内容风格生成）：
   - 若配置 `cover_image` 指向的文件**已存在**，直接用；
   - 若**不存在**，由 AI 代理调用 **ImageGen 工具**自动生成：
     1. **读文章定风格**：读取 `title` / `digest` / `source_file` 正文，判断文章主题领域（财经、科技、美食、旅行、历史、健康、教育、商业、情感、通用等）；
     2. **构造匹配 prompt**：按下方「封面风格推断参考」选取与主题呼应的极简隐喻 + 配色，统一约束"极简克制、大量留白、无水印无文字、高级质感"；若配置 `cover_prompt` 非空则**优先用用户指定**（用户显式要求覆盖自动推断）；
     3. 按 `cover_size`（默认 900×383）调 ImageGen 生成，落地为 `cover_image`（如 `cover.png`）。
   - 若 ImageGen 不可用或生成失败，向用户索取一张本地封面图，复制到 `cover_image` 路径。
3. 确认**源文件**与封面图位于配置指定的路径（与脚本同目录或绝对路径）。源文件由 `source_file` 指定，脚本按后缀自动识别格式并转换：
   - `.md` → GFM 解析为内联样式 HTML（标题/列表/表格/引用/代码）
   - `.docx` → 提取段落与表格（标题样式、加粗/斜体保留）
   - `.xlsx` → 每张工作表转为一个表格
   - `.csv` → 转为表格
   - `.html`/`.htm` → 直接使用
4. 运行 `scripts/wechat_push_draft.py`（Python 3，零第三方依赖）：
   - 获取 access_token
   - 上传封面图为**永久素材**（`material/add_material?type=image`）拿 media_id
   - 自动转换 `source_file` 并提取 `<body>` 片段作为图文 content
   - 调用 `draft/add` 推送，返回草稿 media_id
5. 用户在公众平台「内容与互动 → 草稿箱」查看。

## 关键坑（务必遵守）
- **封面必须是永久素材**：草稿接口 `thumb_media_id` 只接受 `material/add_material` 返回的永久 media_id；用临时素材 `media/upload` 会报 `40007 invalid media_id`。
- **正文取 `<body>` 片段**：微信 content 接受 HTML 片段，保留内联 style 即可完整保样式；不要传完整 `<html>` 或纯 Markdown。
- **IP 白名单**：未加白名单会返回 `40164 invalid ip` 或 token 获取失败，需把调用方出口 IP 加入白名单。
- **凭证安全**：AppSecret 敏感，使用后建议用户在公众平台重置；本地 config 中的 secret 不应长期留存或提交到仓库。

## 资源
- `scripts/wechat_push_draft.py`：主推送脚本，仅标准库 urllib，自动识别封面图扩展名（jpg/png），并调用 convert 转换源文件。
- `scripts/convert.py`：零依赖格式转换器，支持 `.md`/`.docx`/`.xlsx`/`.csv`/`.html` → 微信内联样式 HTML（纯标准库：zipfile + xml.etree + csv + re）。
- `scripts/wechat_config.example.json`：配置模板，含 `source_file`、`cover_prompt`（**可选**，留空则按文章内容自动匹配封面风格）与 `cover_size`（默认 900×383）。
- `references/wechat_api.md`：微信接口细节与常见错误码。

## 支持的源格式（第一批）
| 格式 | 转换能力 | 当前限制 |
|------|----------|----------|
| `.md` | 标题、加粗/斜体、列表、表格、引用、代码块、链接 | 复杂嵌套列表未完全覆盖 |
| `.docx` | 段落、标题样式、加粗/斜体、表格 | **嵌入图片暂不提取上传**（第二批处理） |
| `.xlsx` | 全部工作表 → 表格（表头加粗） | 公式结果以存储值呈现；图表不渲染 |
| `.csv` | 整表 → 表格（首行表头加粗） | 宽表手机端可能需横向滚动 |
| `.html`/`.htm` | 直接使用，提取 `<body>` | — |

## 封面图自动生成（ImageGen 环节）
- 触发：配置 `cover_image` 指向的文件不存在时，由 AI 代理调用 ImageGen 工具生成。
- **风格匹配文章内容（默认行为）**：代理先读 `title`/`digest`/`source_file` 推断主题领域，再按「封面风格推断参考」构造极简美观的封面 prompt；`cover_prompt` 留空（`""`）即启用此自动匹配。
- **用户指定优先**：若 `cover_prompt` 非空，则直接用用户给定的提示词（覆盖自动推断）。
- 规格：按 `cover_size`（默认 900×383）生成，保存为 `cover_image` 路径（如 `cover.png`）。
- 兜底：ImageGen 不可用或失败时，向用户索取本地封面图并复制到位，再继续推送。

## 作者自动提取
- 优先级：`author`（配置显式指定） > 源文件提取 > `default_author`（兜底，默认「龙猫爸爸」）。
- 源文件提取规则（由 `convert.extract_author` 实现，仅文档模式生效）：
  - `.md`：文首/文末 `author: xxx` 或 `作者：xxx`（大小写、全/半角冒号均可）
  - `.html`：`<meta name="author" content="xxx">`，或文中 `作者：xxx`
  - `.docx`：document.xml 中 `作者：xxx`
  - `.xlsx` / `.csv`：无作者概念，跳过提取
- 图片列表模式（`image_files`）无源文件，直接使用 `author` 或 `default_author`。

## 封面风格推断参考
统一约束（所有主题）：极简克制、大量留白、无水印无文字无 logo、高级质感、抽象隐喻优先（避免写实大图）。比例按 `cover_size`（默认 900×383）。

| 文章主题领域 | 视觉隐喻（抽象） | 推荐配色（渐变/底色） |
|------|------|------|
| 财经 / 价值投资 | 护城河环绕城堡、稳健年轮、静水 | 深墨绿→墨黑 + 细金光点 |
| 科技 / AI | 发光网格、神经网络线、光点流 | 深蓝→墨黑 + 青色光 |
| 美食 / 餐饮 | 极简食材线条、碗碟弧线 | 暖米→焦糖 + 留白 |
| 旅行 / 风景 | 远山剪影、海岸线、云层 | 天青→暖白，舒展 |
| 历史 / 文化 | 水墨笔触、宣纸纹理、印章红点 | 宣纸米白→淡墨 |
| 健康 / 医疗 | 柔和叶片、DNA 双螺旋、脉搏线 | 青绿→白，干净 |
| 教育 / 知识 | 书页、灯塔、星图 | 暖灰→白 |
| 商业 / 创业 | 上升阶梯、远峰、罗盘 | 靛蓝→黑，克制 |
| 情感 / 生活 | 晨光、柔和涟漪、暖雾 | 暖橘→米白 |
| 通用 / 未识别 | 抽象几何渐变、流动金线 | 深蓝→墨黑 + 细金线 |

> 用法：代理读取文章后，挑最贴切的一行构造中文 ImageGen prompt。例：财经文 →「价值投资公众号封面图，深墨绿到墨黑垂直渐变背景，中央一座安静城堡被月牙形护城河环绕，细金光点点缀，极简克制，大量留白，无水印无文字，高清质感」。

## 安全与权限
- 仅操作用户自有公众号，绝不越权。
- 推送前确认 HTML 内容无误，草稿箱内容需用户自行预览核对。
