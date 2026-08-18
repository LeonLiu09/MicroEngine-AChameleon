# SkillSwap v5.3

SkillSwap 是一个中英双语技能交换社区。用户可以分享自己会的技能、寻找想学的内容、匹配互补伙伴、发起交换并与已连接的伙伴实时聊天。

公网版本：[https://skillswap-cefff752.demo.hack.microengine.org/](https://skillswap-cefff752.demo.hack.microengine.org/)

## 当前版本

- 正式前端入口统一为根目录 `index.html`，不再保留历史版本首页文件。
- HarmonyOS Sans SC 全站字体与统一的 `SkillSwap.` 品牌标识。
- 未登录首页、登录页和注册页会从中英双语标题词库中随机显示宣传语。
- 支持中文和英文切换，并保留用户界面偏好。
- 支持注册、登录、个人资料、技能管理、综合搜索与互补匹配。
- 支持交换请求的接受、拒绝、取消和双方确认完成。
- 支持已连接伙伴的 SQLite 消息历史与 HTTP 长轮询实时聊天。
- 提供仅限服务器本机访问的独立管理员后台。

## 技术结构

- 前端：单文件 React 18、ReactDOM 18 与 Babel Standalone，无构建步骤。
- 后端：Python 3.10+ 标准库 HTTP 服务。
- 数据：SQLite 账号数据库与技能目录数据库。
- 字体：仓库内置 HarmonyOS Sans SC Regular、Medium 和 Bold。
- 管理后台：原生 HTML、CSS 与 JavaScript。

## 本地运行

项目无需安装第三方 Python 依赖：

```bash
python server.py
```

打开以下地址：

- 用户端：`http://127.0.0.1:4173/`
- 管理后台：`http://127.0.0.1:4173/admin`
- 前端自测：`http://127.0.0.1:4173/?selftest=1`

首次启动会创建：

```text
data/skillswap.db
data/skills.db
```

普通演示账号：

```text
邮箱：daniel@example.com
密码：SkillSwap123!
```

## 管理员配置

管理员后台只接受服务器本机请求，不通过公网隧道开放。复制 `.env.example` 为不会提交的 `.env`，然后设置：

```dotenv
SKILLSWAP_ADMIN_EMAIL=admin@example.com
SKILLSWAP_ADMIN_PASSWORD=请替换为至少12位的安全密码
SKILLSWAP_ADMIN_NAME=超级管理员
SKILLSWAP_ADMIN_SYNC=1
```

真实密码、数据库和运行时部署文件不应提交到仓库。生产环境启用 HTTPS 时请设置：

```dotenv
SKILLSWAP_SECURE_COOKIE=1
```

## 主要接口

- 认证：`POST /api/auth/register`、`POST /api/auth/login`、`POST /api/auth/logout`、`GET /api/auth/me`
- 个人资料：`GET/PUT /api/users/me/profile`
- 用户技能：`GET/PUT /api/users/me/skills`
- 技能目录：`GET /api/skills`、`GET /api/skills/{id}`
- 搜索：`GET /api/search`
- 交换请求：`GET/POST /api/swap-requests` 及状态操作接口
- 聊天：`GET /api/chat/conversations`、`GET /api/chat/messages`、`GET /api/chat/events`、`POST /api/chat/messages`
- 社区统计：`GET /api/community/stats`
- 健康检查：`GET /api/health`

除注册、登录、健康检查和社区统计外，用户接口均要求有效会话。管理员写操作额外验证本机访问、独立会话、同源请求与 CSRF Token。

## 测试

```bash
python -m unittest discover -s tests -v
```

测试覆盖认证、会话隔离、资料与技能、搜索、交换请求、聊天、管理员权限、安全校验和数据库迁移。

## 文件结构

```text
index.html          当前正式用户端
admin.html          本机管理员后台
server.py           Python HTTP 服务与业务接口
assets/fonts/       HarmonyOS Sans SC 字体
tests/              自动化测试
data/               本地 SQLite 数据（运行时生成）
CHANGELOG.md        版本记录
```

## 说明

GitHub Pages 只能展示静态前端，不能运行 Python/SQLite 后端。需要完整的账号、搜索、交换和聊天功能时，请使用公网版本或在本地运行 `server.py`。
