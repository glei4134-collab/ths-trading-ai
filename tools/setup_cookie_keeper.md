# Cookie云端续签配置指南

## 原理

Cookie有效期30天。本工具通过云函数每8小时自动调用同花顺API，保持会话活跃，**永不过期**。

---

## 方案一：Vercel 云函数（推荐）

### 前置条件

- 安装 Node.js（下载：https://nodejs.org）
- 注册 Vercel 账号（https://vercel.com，可用 GitHub 登录）

### 部署步骤

#### 1. 安装 Vercel CLI

```bash
npm install -g vercel
```

#### 2. 登录 Vercel

```bash
vercel login
```

#### 3. 进入项目目录并部署

```bash
cd ths-trading-ai
vercel deploy --prod
```

按提示操作：
- Set up and deploy? → `Y`
- Which scope? → 选择你的账号
- Link to existing project? → `N`
- Project name? → 回车（默认即可）
- Directory where code is located? → `./`

#### 4. 设置环境变量

部署完成后，打开 https://vercel.com/dashboard → 点击项目 → Settings → Environment Variables：

- Name: `THS_COOKIE`
- Value: 粘贴你的Cookie
- Environments: 勾选 Production

#### 5. 验证

访问 `https://你的项目名.vercel.app/api/keepalive`，应返回：
```json
{"ok": true, "time": "2026-05-05 01:42:13", "balance": "749527.87"}
```

Vercel Cron 会每8小时自动执行。

---

## 方案二：GitHub Actions

### 配置步骤

#### 1. Fork项目

在GitHub上Fork本项目到自己的账号下。

#### 2. 获取Cookie

在浏览器打开 https://mncg.10jqka.com.cn ，按F12 → Console，执行：

```javascript
copy(document.cookie); console.log('Cookie已复制，长度:', document.cookie.length);
```

#### 3. 添加Secret

在Fork后的仓库中：Settings → Secrets and variables → Actions → New repository secret

- Name: `THS_COOKIE`
- Value: 粘贴上一步复制的Cookie

#### 4. 启用Actions

进入Actions标签页，点击"I understand my workflows, go ahead and enable them"。

#### 5. 验证

- 等待下一次定时执行，或在Actions页面点击"Run workflow"手动触发
- 查看执行日志，确认显示 `OK - balance: xxx`

---

## Cookie更新

当Cookie过期或手动重新登录后，需要更新：

- **Vercel**: Dashboard → Settings → Environment Variables → `THS_COOKIE` → Edit
- **GitHub**: Settings → Secrets → `THS_COOKIE` → Update

## 费用

| 平台 | 费用 |
|------|------|
| Vercel | 免费（Hobby计划，100GB带宽/月） |
| GitHub Actions（公开仓库） | 完全免费 |
| GitHub Actions（私有仓库） | 每月2000分钟免费 |
