# wxscraper

> **English**: A CLI tool (Python 3.12, pure `requests` + `beautifulsoup4`, no browser required) that archives all obtainable articles from a WeChat Official Account to local folders — per-article HTML/Markdown/metadata/images plus an account-level index. **For personal learning and research only** — respect WeChat's Terms of Service and robots rules; scraped content belongs to its original authors and must not be used commercially or redistributed publicly. Use at your own risk (anti-scraping / account risk applies). See the full disclaimer below.

微信公众号文章抓取 CLI 工具（Python 3.12，纯 `requests` + `beautifulsoup4`，不依赖浏览器/Playwright）。

把某个公众号**全部可获取的文章**抓到本地：每篇文章一个子目录（保留排版的 HTML、Markdown、meta.json、图片），并生成账号级索引。

## 输出结构

```
output/<公众号名>/
├── account.json              # 公众号名称、__biz、简介、头像、fakeid
├── index.json                # 所有文章：标题/时间/链接/本地路径（按时间倒序）
├── .checkpoint.json          # 断点续爬记录（重跑自动跳过已抓文章）
└── 20240115_文章标题/
    ├── 文章标题.html         # 保留排版的正文（图片已本地化）
    ├── 文章标题.md           # Markdown 版正文
    ├── meta.json             # 标题/作者/发布时间/封面/__biz 等元数据
    └── images/               # 正文图片（001.jpg ...）
```

## 安装

```bash
cd wxscraper
pip install -r requirements.txt
python -m wxscraper --help
```

建议在**仓库根目录**运行命令，也就是包含 `README.md` 和 `wxscraper/` 子目录的这一层。
如果你误进入 `wxscraper/wxscraper` 再执行 `python -m wxscraper`，当前版本也会自动兼容。

## 使用方法（三种后端）

### A 线 · 单篇（免登录，零配置）

```bash
python -m wxscraper --url "https://mp.weixin.qq.com/s/xxxx"
```

只抓这一篇文章，同时会尝试进入 C 线（见下）抓取该号合集内的文章。

### B 线 · 全量历史（推荐，真全量群发记录，需你自己的公众号后台凭证）

```bash
python -m wxscraper --name "刘润" --mp-cookie "你的Cookie" --token "123456789"
# 或
python -m wxscraper --url "https://mp.weixin.qq.com/s/xxxx" --mp-cookie "..." --token "..."
```

流程：`searchbiz` 按名称搜号拿 `fakeid` → `appmsgpublish` 翻页拉全量群发列表 → 逐篇抓正文。

### C 线 · 合集（免登录，覆盖度 = 号主建立的合集）

无需任何凭证。工具会从已抓文章页中自动发现合集链接（`appmsgalbum`）并翻页拉取其中全部文章。

**⚠️ 覆盖度局限**：只有号主把文章加入了"合集/专辑"，才能被这条线抓到。没建合集、或合集没收录的文章抓不到。要真全量请用 B 线。

### D 线 · 自部署 RSS（可选）

如果你部署了 [Wechat2RSS](https://github.com/ttttmr/Wechat2RSS) / wewe-rss：

```bash
python -m wxscraper --url "https://mp.weixin.qq.com/s/xxxx" \
    --rss-base http://localhost:4001 --rss-token 你的token
```

直接取全文 JSON（`GET {base}/api/query?k={token}&bid={biz}&content=1`）。

### 配置文件

```bash
cp config.example.json config.json   # 填入 cookie/token 等
python -m wxscraper --name "刘润" --config config.json
```

命令行参数优先于配置文件。**请勿把含真实 Cookie 的 config.json 提交到 git。**

### 常用选项

| 参数 | 说明 |
|---|---|
| `-o, --output` | 输出根目录，默认 `output/` |
| `--no-images` | 不下载正文图片 |
| `--albums-only` | 即使有 cookie/token 也只走 C 线合集 |
| `--max-pages N` | 列表翻页上限（调试用，0=不限） |
| `-v, --verbose` | 调试日志 |

## 如何获取公众号后台 Cookie 和 token（B 线）

> 前提：你**自己拥有一个微信公众号**（订阅号即可），抓取目标号是任意公开账号，二者无关。

1. 浏览器打开 <https://mp.weixin.qq.com>，扫码登录**你自己的**公众号后台。
2. 登录后看地址栏 URL，形如 `.../cgi-bin/home?t=home/index&lang=zh_CN&token=123456789`，把 `token=` 后面的数字抄下来 → 即 `--token`。
3. 按 `F12` 打开开发者工具 → **Network（网络）** 面板。
4. 在后台页面随便点一下（如刷新），在 Network 里点任意一个发往 `mp.weixin.qq.com` 的请求 → **Headers → Request Headers → Cookie**，右键复制**完整** Cookie 字符串 → 即 `--mp-cookie`。
5. Cookie 有效期通常几小时到一天，失效后重新登录复制即可。token 一般随登录会话变化，建议同时更新。

## 反爬与限速设计

- 桌面 Chrome UA；下载 `mmbiz.qpic.cn` 图片自动带 `Referer: https://mp.weixin.qq.com/`。
- 文章正文每篇间隔 3~5 秒随机；B 线列表翻页 5~10 秒；C 线翻页 3~5 秒。
- 微信会返回 HTTP 200 的"未知错误/环境异常"提示页：工具检测后按 10s/30s/60s 指数退避重试，最多 3 次。
- 命中频率限制（errcode 200013 等）立即停止翻页，已抓进度保存在 `.checkpoint.json`，重跑自动续爬。

## 技术说明

- **发布时间**：文章页 `#publish_time` 由 JS 填充，原始 HTML 为空，必须解析内嵌 `var ct = "..."`（Unix 秒）。
- **图片懒加载**：正文 `<img>` 真实地址在 `data-src` 属性。
- **sn 过期**：文章列表里的链接带临时 `sn` 参数会过期，因此抓到链接后**立即**抓正文，不能只存链接。
- **publish_page**：B 线接口返回的是 JSON-in-JSON，需二次解析。

## 免责声明

本工具**仅供个人学习与研究使用**：

- 请遵守目标网站的 robots 协议与《微信软件许可及服务协议》，不得高频抓取、不得干扰微信正常服务。
- 抓取的内容版权归原作者与公众号所有，**不得用于商业用途或公开分发**。
- Cookie/token 属于你的账号凭证，泄露风险自负；请勿将含凭证的配置文件提交到公开仓库。
- 因使用本工具产生的一切后果由使用者自行承担。

## Disclaimer (English)

This tool is provided **for personal learning and research purposes only**:

- Comply with the target site's robots rules and the WeChat Software License & Service Agreement. Do not scrape at high frequency or disrupt WeChat's services.
- Scraped content is copyrighted by its original authors and account owners. **Do not use it commercially or redistribute it publicly.**
- Your Cookie/token are your own account credentials — protect them and never commit config files containing them.
- Web scraping involves legal and account risks (rate limits, bans, ToS violations). All consequences of using this tool are borne by the user.
