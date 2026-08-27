# 微信草稿推送接口要点

## 接口清单
1. 获取 access_token
   GET https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential&appid=APPID&secret=APPSECRET
   返回 {"access_token":"...","expires_in":7200}

2. 上传永久图片素材（封面）
   POST https://api.weixin.qq.com/cgi-bin/material/add_material?access_token=TOKEN&type=image
   multipart/form-data，字段 name="media"
   返回 {"media_id":"...","url":"..."}

3. 添加草稿
   POST https://api.weixin.qq.com/cgi-bin/draft/add?access_token=TOKEN
   body JSON:
   {
     "articles":[{
       "title":"标题",
       "author":"作者",
       "digest":"摘要",
       "content":"HTML 片段（保留内联 style）",
       "thumb_media_id":"永久素材 media_id",
       "need_open_comment":0,
       "only_fans_can_comment":0
     }]
   }
   返回 {"media_id":"草稿 media_id"}

## 关键约束
- thumb_media_id 必须是**永久素材** media_id（material/add_material），临时素材会被 40007 拒绝。
- content 为 HTML 字符串，建议传 <body> 内片段，内联 style 保留最佳。
- 调用方 IP 须加入公众号 IP 白名单，否则 40164。
- 封面图由 AI 代理在运行本脚本前，用 ImageGen 按 `cover_prompt`（默认价值投资风格）生成并落地为 `cover_image`；若已存在则直接复用，不重复生成。

## 常见错误码
- 40007 invalid media_id：封面用了临时素材，改用永久素材。
- 40164 invalid ip：调用 IP 不在白名单。
- 40013 invalid appid：AppID 错误。
- 40001 invalid credential：AppSecret 错误或已重置。
- 45009 api freq out of limit：接口调用超限，稍后重试。
