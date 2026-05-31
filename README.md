# 🎬 TikTok & Douyin 无水印高清视频解析下载引擎 (All-in-One)

[![FastAPI](https://img.shields.io/badge/FastAPI-0.100.0+-009688.svg?style=flat&logo=FastAPI&logoColor=white)](https://fastapi.tiangolo.com)
[![Python](https://img.shields.io/badge/Python-3.9+-3776AB.svg?style=flat&logo=Python&logoColor=white)](https://www.python.org)
[![yt--dlp](https://img.shields.io/badge/yt--dlp-Active-blue.svg?style=flat)](https://github.com/yt-dlp/yt-dlp)
[![Telegram Bot](https://img.shields.io/badge/Telegram--Bot-Active-26A5E4.svg?style=flat&logo=Telegram)](https://core.telegram.org/bots)

这是一个专为个人及频道管理员设计的 **TikTok (抖音国际版) & Douyin (抖音)** 无水印高清视频解析与下载的开源集成系统。包含 **高性能 FastAPI 后端 API**、**现代化毛玻璃网页 UI 客户端** 以及 **多功能的 Telegram 机器人**。

---

## 🌟 核心特性

- ⚡ **超凡速度 & 免 Cookie 解析（首选优先）**：
  * 重构了基于抖音 Reflow 移动页面的 `window._ROUTER_DATA` 结构化爬取器。
  * **99% 别人的公开视频解析完全无需配置任何 Cookie 或签名验证**，并在 2 秒内极速获取无水印地址，极大延长个人 Cookie 的生存期。
- 🍪 **私密视频支持 (Cookie Web 备份模式)**：
  * 集成 yt-dlp & 抖音 Web 作品列表 API 作为二级备份，配合您配置的 Cookie，可稳定下载您本人的“仅自己可见”等私密视频。
- ⚙️ **极简便捷的 Telegram 机器人交互**：
  * **常驻底部物理键盘**：无需打字输入 `/settings`，直接点击聊天框下方的 `📥 直接返回给您` 或 `📤 发送到频道` 大按钮，即可**一键无缝切换并记住您的视频发送模式**。
  * **智能容灾自动降级**：
    * 当视频文件小于 50MB 时，机器人会直接把去水印的高清 MP4 文件发送到您的对话或频道中，支持 Telegram 内置流媒体直接播放。
    * 当视频过大（触发 Telegram 50MB 上传限制）时，机器人将自动降级为生成并发送带防盗链的本地 `/stream` 代理下载链接。
- 🖥️ **现代化毛玻璃 Web 客户端**：
  * 内置极其精美的 Glassmorphism 网页 UI，支持直接粘贴视频分享文本，一键解析下载，并且自带媒体播放预览。
- 🛡️ **视频流媒体中转代理**：
  * 内置专门的 `/stream` 代理分发接口，完美解决抖音/TikTok CDN 连接的 Referer 防盗链防跨域问题，支持在任何网络环境下直接播放和流畅下载。
- 🔄 **全自动 Token 刷新后台服务**：
  * 后台每 15 分钟自动请求一次抖音接口以刷新 `msToken`，尽可能自动保持 Cookie 的状态健康。
- 📲 **自动化 Chrome Cookie 一键推送脚本**：
  * 内置 `update_cookies_render.sh` 脚本，可一键读取本地 Chrome 的抖音登录 Session 信息并自动化加密推送到部署好的 Render 服务器中。

---

## 📂 项目结构

```
.
├── main.py                    # 核心引擎 (包含 FastAPI 路由、解析逻辑与 Telegram 机器人定义)
├── index.html                 # 现代化的毛玻璃网页前端 UI
├── requirements.txt           # 依赖包列表
├── render.yaml                # Render 自动化一键部署配置
├── update_cookies_render.sh   # macOS 下 Chrome 浏览器 Cookie 提取与同步脚本
└── user_preferences.json      # 用户设置本地持久化文件 (自动生成)
```

---

## 🚀 快速开始

### 方式一：一键部署到 Render (推荐)

项目已完美适配 Render 的基础设施。您可以直接利用项目中的 `render.yaml` 进行自动化部署：

1. 将本项目 Fork 到您的 GitHub。
2. 登录 [Render](https://render.com/)，在 Dashboard 中选择 **Blueprints**。
3. 连接您的 GitHub 仓库并部署。
4. **配置环境变量**（在 Service 的 Environment 中设置）：

| 环境变量 | 是否必填 | 描述 |
| :--- | :---: | :--- |
| `TELEGRAM_BOT_TOKEN` | 否 | 如果需要激活 Telegram Bot，请填入从 `@BotFather` 处获取的 Token。 |
| `TELEGRAM_CHANNEL` | 否 | 默认推送的 Telegram 频道用户名或 ID（例如 `@my_channel`），必须将 Bot 设为该频道的管理员。 |
| `RENDER_EXTERNAL_URL` | 否 (生产推荐) | 服务在 Render 上的外部公开 URL（例如 `https://your-app.onrender.com`），用于配置 Bot 的 Webhook 以及流媒体代理下载链接。 |
| `COOKIES_UPDATE_TOKEN` | 否 | 用于保护 `/update-cookies` 接口的密钥 Token，可防止他人恶意上传垃圾 cookie。配置后推送脚本须带上此 Token。 |
| `COOKIES_CONTENT` | 否 | 初始的 Netscape 格式的 Cookie 文本内容（选填）。 |

---

### 方式二：本地运行调试

1. **克隆项目并进入目录**：
   ```bash
   git clone <your-repo-url>
   cd douyin
   ```

2. **创建并激活虚拟环境**：
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. **安装依赖**：
   ```bash
   pip install -r requirements.txt
   ```

4. **启动服务**：
   ```bash
   uvicorn main:app --host 127.0.0.1 --port 8000 --reload
   ```
   启动后：
   * 网页客户端：`http://127.0.0.1:8000`
   * API 文档：`http://127.0.0.1:8000/docs`

---

## 🍪 抖音登录 Cookie 提取与自动推送 (针对私密视频)

若需要下载**您自己的私密视频**，需要把您 Chrome 浏览器里的抖音登录状态 Cookie 上传到服务器。我们编写了非常方便的自动化脚本：

1. **前提条件**：
   * 确保您的电脑是 macOS 且安装了 Google Chrome。
   * 打开 Chrome 登录 `https://www.douyin.com/`，确保处于登录状态（能看到自己头像）。

2. **配置并运行同步脚本**：
   * 打开 `update_cookies_render.sh`，将第 7 行的 `RENDER_URL` 改为您实际部署的服务器 URL。
   * 如果配置了 `COOKIES_UPDATE_TOKEN` 环境变量，请在本地终端运行：
     ```bash
     export COOKIES_UPDATE_TOKEN="您设置的密钥"
     ./update_cookies_render.sh
     ```
   * 脚本会自动从您的 Chrome `Default` 或各 `Profile` 中解密出 `sessionid`，并通过 API 安全地上传到您的 Render 服务器上。

---

## 🤖 Telegram 机器人使用指南

1. 启动机器人后，发送 `/start`。
2. 机器人会弹出带有 **`📥 直接返回给您`** 和 **`📤 发送到频道`** 的底部常驻键盘。
3. **点击按钮即可即时切换发送接收模式**。
4. 直接向机器人粘贴发送抖音或 TikTok 的分享文案或链接（例如：`8.02 YZM:/ 11/15 l@P.KJ :7pm 欣赏的眼光看世界 #美女日常 https://v.douyin.com/xxxx/`）。
5. 机器人会自动识别链接、极速解析，并根据您的设置推送无水印视频。

---

## 🛠️ 开放接口 (API Endpoints)

### 1. 解析视频信息
* **路径**：`POST /parse`
* **请求体**：
  ```json
  {
    "url": "https://v.douyin.com/xxxx/"
  }
  ```
* **返回**：`VideoMetadata` 格式 JSON 数据。

### 2. 流媒体代理分发接口
* **路径**：`GET /stream`
* **参数**：
  * `url`: 原始视频 CDN 直连链接
  * `cookies`: 用于中转的 Cookie 字符串
  * `referer`: 引用页地址
  * `download`: 设为 `1` 将直接触发浏览器下载保存，否则为在线播放流媒体。

### 3. 查看 Cookie 状态
* **路径**：`GET /cookie-status`
* **返回**：当前服务器端 `cookies.txt` 的健康度、有效期和包含哪些 Token 的状态报告。

---

## 📄 开源许可证

本项目基于 MIT 许可证开源。请勿将本项目用于任何商业或非法侵权用途，因使用本项目产生的任何民事争议或法律责任由使用者自行承担。
