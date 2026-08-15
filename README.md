# obs139 —— 139 云盘图片上传 + 本地直链工具

把本地图片一键上传到中国移动云盘(139)，生成**本地直链**，在 Obsidian 笔记里内嵌显示、点击可打开。
图片本体不进 git 仓库，笔记里只有一行链接。纯 Python 标准库，零 pip 依赖。

## 特性

- **本地直链**：`![](http://127.0.0.1:8740/img/<file_id>)`
  - 永久有效（基于 139 云端 file_id）、**无分享数量限制**、Obsidian 直接渲染
  - 经本地代理 `139img.py` 实时取图，天然仅本机可见
- 上传时显示实时进度；默认文件夹可配置；支持多级目录自动创建

## 目录结构

```
obs139/
├── 139uplink.py          # 上传 CLI：python 139uplink.py <图片>
├── 139img.py             # 本地图片代理服务：python 139img.py（127.0.0.1:8740）
├── yidong.py             # 139 API 封装（复制自 casgen 项目，自包含依赖）
├── config.json           # token / 默认文件夹 / 端口（含敏感 token，勿提交 git）
├── start_server.bat      # 双击启动本地代理
├── obsidian-plugin/      # Obsidian 插件源码（安装时复制到库的 .obsidian/plugins/）
└── README.md
```

## 安装与配置

### 1. 环境

- Windows + Python 3（任意发行版，Anaconda 或官网版均可）

### 2. 获取 token（登录态）

1. 浏览器打开并登录 `https://yun.139.com`
2. 按 `F12` → **Network** 面板
3. 点任意一个请求 → 请求头里复制 `Authorization` 的值（`Basic xxxx` 那串）
4. 写入 `config.json` 的 `token` 字段

> ⚠️ token 等同账号密码，有效期约 30 天。失效时 `python 139uplink.py --check` 会提示刷新方法。

### 3. 验证

```bash
cd D:/obs139
python 139uplink.py --check     # 应显示 "token 有效" 和剩余天数
```

## 用法

```bash
# 上传图片，输出本地直链（默认，Obsidian 可直接显示）
python 139uplink.py photo.png

# 指定上传文件夹（多级路径不存在会自动创建）
python 139uplink.py photo.png --folder "/知识库/2026/图片"

# 只上传不输出链接（调试用）
python 139uplink.py photo.png --no-link
```

输出示例：

```markdown
![photo.png](http://127.0.0.1:8740/img/FqZxAbCdEf1234567890_ABC)
```

### 本地代理

图片直链依赖代理服务运行：

```bash
python D:/obs139/139img.py      # 或双击 start_server.bat
```

验证：浏览器打开 `http://127.0.0.1:8740/health` 应返回 `{"ok": true}`。
Obsidian 插件会在加载时**自动拉起**代理，一般无需手动启动。

## Obsidian 插件

插件在 `obsidian-plugin/`，安装方法：

1. 把 `obsidian-plugin/` 里的 `manifest.json` 和 `main.js` 复制到
   `你的库/.obsidian/plugins/obs139-uploader/`
2. 重启 Obsidian → 设置 → 第三方插件 → 打开「139 Uploader」
3. 使用：`Ctrl+P` → 「上传图片到 139 并插入链接」，或点左侧边栏图片图标
4. 选图 → 自动上传（状态栏实时进度）→ 链接自动插入光标处，图片直接显示

> 插件内部调用 `python D:/obs139/139uplink.py <图片> --link-only`，
> token / 默认上传文件夹 都在 `config.json` 配置。

## 换电脑迁移

直链里的 `file_id` 存在 139 云端，`127.0.0.1` 每台机器都指本机，所以**旧笔记链接跨机器通用**。
新机器只需：

1. 安装 Python
2. 复制本 `obs139/` 文件夹到同样位置（含 config.json，token 是账号级的）
3. 安装 Obsidian 插件（同上）
4. 完成，旧直链照常显示

> 手机上看不了本地直链（无代理）。手机上的图请直接用 139 App 在云盘里查看。

## 配置项（config.json）

| 字段 | 说明 |
|---|---|
| `token` | 登录态 Authorization（敏感，已 gitignore） |
| `default_folder` | 默认上传文件夹，不带 `--folder` 时用 |
| `port` | 本地代理端口（默认 8740） |

## 常见问题

**上传慢？** 本机开着 Clash/V2Ray 等代理时，urllib 会把 139 直连流量绕道代理。工具已内置"绕开系统代理直连"，无需干预。

**直链图片不显示？**
1. 确认代理在运行：`http://127.0.0.1:8740/health`
2. 插件会自动拉起；手动 `python D:/obs139/139img.py`
3. token 失效时代理返回 401，`python 139uplink.py --check` 看刷新方法

**token 失效？** 见上方"获取 token"，`--check` 查看剩余天数。

## 致谢

`yidong.py` 复制自 [tianjian518/casgen](https://github.com/tianjian518/casgen)（139 云盘 API 封装，含 mcloud-sign 签名、上传、下载直链），本项目在其基础上做上传 + 本地代理直链的封装。
