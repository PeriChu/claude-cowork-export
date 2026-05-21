# claude-cowork-export

> 导出、归档、迁移、续聊 **Claude Desktop / Cowork** chat。

[English](README.md) · [中文](README.zh-CN.md)

一个纯 Python 单文件工具，读取磁盘上每个 Cowork chat（在 Cowork 里叫 "task"），
转成三种东西：

- **人能读的归档** —— HTML / Markdown / JSON / CSV 四种渲染，外加用户上传文件、
  助手生成的输出、审计日志、以及助手 `Write` / `Edit` 过的每个文件，
- **能移植的备份** —— 把 bundle 拷到另一台机器，task 会出现在 Cowork 侧边栏，
  接着聊就行，
- **跨账号续聊的种子（seed）** —— 一份 Markdown，粘到全新 Cowork 对话首条消息里，
  哪怕换账号、换机器，新对话也能从上次停下的地方接着干。

零第三方依赖（纯 Python 3.9+ 标准库），一个 .py 文件，一个 CLI。

姊妹项目：[claude-code-export](https://github.com/PeriChu/claude-code-export)
针对 Claude Code CLI 的 `~/.claude/projects/` 会话。本项目针对 Claude 桌面端的
Cowork chat。

---

## 目录

- ["Cowork" 在这里指的是什么](#cowork-在这里指的是什么)
- [安装](#安装)
- [快速上手](#快速上手)
- [命令](#命令)
  - [`list`](#list)
  - [`export`](#export)
  - [`import`](#import)
  - [`seed`](#seed)
- [Bundle 结构](#bundle-结构)
- [Cowork 数据在哪](#cowork-数据在哪)
- [常见工作流](#常见工作流)
- [跨平台说明](#跨平台说明)
- [安全：`--include-auth` 注意事项](#安全)
- [故障排查](#故障排查)
- [分支](#分支)
- [License](#license)

---

## "Cowork" 在这里指的是什么

Claude 桌面端里每个 chat 叫一个 **task**，它生活在沙盒目录
`~/Library/Application Support/Claude/local-agent-mode-sessions/...`（macOS）
或 `%APPDATA%\Claude\local-agent-mode-sessions\...`（Windows）下。每个 task 携带：

- 用户上传的文件 `uploads/`，
- 助手生成的输出 `outputs/`（同时也是 chat 的工作目录），
- 审计日志 `audit.jsonl`，
- task 元数据 `local_<id>.json`（标题、模型、起止时间、initial brief、
  user-selected folders、archive 标志等），
- macOS 上有原生 JSONL transcript（`<task>/.claude/projects/<encoded-cwd>/<cli-id>.jsonl`）；
  Windows 上原生 transcript 可能缺席，工具会把 `audit.jsonl` 当作 transcript。

这个工具理解上述所有结构，会对 Windows audit 路径上的伪重复做去重，并为每个
task 产出一份自包含的 bundle。

---

## 安装

需要 Python 3.9 或更高。推荐使用 [pipx](https://pipx.pypa.io/)：

```bash
# macOS / Linux
pipx install "git+https://github.com/PeriChu/claude-cowork-export.git"

# Windows（PowerShell）—— 同一命令，但装的是 Windows 分支
pipx install "git+https://github.com/PeriChu/claude-cowork-export.git@windows"
```

装好后会注册两个等价的命令入口：

```bash
claude-cowork-export ...   # 主名
cowork-export ...          # 短别名
```

也可以不装，直接当脚本跑：

```bash
git clone https://github.com/PeriChu/claude-cowork-export.git
cd claude-cowork-export
python3 cowork_export.py --help
```

---

## 快速上手

```bash
# 列出磁盘上所有 Cowork task
claude-cowork-export list

# 把最近一次 task 导出到 ./exports/
claude-cowork-export export latest --output ./exports

# 导出所有 task
claude-cowork-export export all --output ./exports

# 把 bundle 拷到另一台机器后还原
claude-cowork-export import ./exports/<task-id>

# 或者新账号场景：把整个 task 压成一份可粘贴的续聊 prompt
claude-cowork-export seed ./exports/<task-id>
# → 输出 ./exports/<task-id>/seed-prompt.md
```

---

## 命令

CLI 有四个子命令。任何时候 `claude-cowork-export <cmd> --help` 都会列出当时的
完整 flag 清单。

### `list`

```
claude-cowork-export list [--source cowork|code|both] [--cowork-root PATH]
```

列出磁盘上发现的所有 Cowork task。在 Windows 上工具会**同时**扫描公开的
`%APPDATA%` 镜像和 MSIX 的 LocalCache 两个位置，按 task id 合并——因为两边各自
只保存了 workspace 的一个子集（一开始我也觉得离谱）。

| Flag | 默认值 | 作用 |
|---|---|---|
| `--source cowork\|code\|both` | `cowork` | 读哪种存储。`code` 回退到老的 `~/.claude/projects/` Claude Code CLI 会话。 |
| `--cowork-root PATH` | _(自动检测)_ | 覆盖自动检测，比如指到一份归档备份。 |

**示例：**

```bash
# 全部
claude-cowork-export list

# 同时列 Claude Code CLI 老会话
claude-cowork-export list --source both
claude-cowork-export list --source code

# 强制指定 MSIX LocalCache
claude-cowork-export list --cowork-root \
  "$LOCALAPPDATA/Packages/Claude_*/LocalCache/Roaming/Claude/local-agent-mode-sessions"
```

### `export`

```
claude-cowork-export export <selector> [-o DIR] [--formats LIST]
                                       [--no-files]
                                       [--include-auth] [--yes-i-know-this-is-risky]
                                       [--source cowork|code|both]
                                       [--cowork-root PATH]
```

把一个或多个 task 导出成独立 bundle 目录（每个 task 一个）。

| Selector | 含义 |
|---|---|
| `latest` | 最近一次活动的 task。 |
| `all` | 所有 task。 |
| `<prefix>` | UUID 以 `<prefix>` 开头的唯一 task；歧义时报错。 |

| Flag | 默认值 | 作用 |
|---|---|---|
| `-o`, `--output`, `--out` | `./exports` | bundle 输出目录，每个 task 一个子目录。 |
| `--formats` | `html,md,json,csv` | 逗号分隔的子集。 |
| `--no-files` | _(关)_ | 不拷贝 uploads / outputs / 触碰文件，bundle 更小更快。 |
| `--include-auth` | _(关)_ | **高风险**。同时把 Cowork 的 auth 工件（Cookies / Local State / buddy-tokens 等）装进 bundle。**只能在同一 OS 家族还原**——详见 [安全](#安全)。 |
| `--yes-i-know-this-is-risky` | _(关)_ | 跳过 `--include-auth` 的交互式 `I UNDERSTAND` 确认，仅供 CI 使用。 |

**示例：**

```bash
# 全部 task 到 ./exports/
claude-cowork-export export all --output ./exports

# 单个 task，仅 HTML + JSON
claude-cowork-export export <task-id> --output ./exports --formats html,json

# 最小 bundle —— 只 transcript + manifest
claude-cowork-export export latest --no-files

# 带凭证的个人备份（仔细看一下警告再用）
claude-cowork-export export latest --output ./private-backup --include-auth
```

### `import`

```
claude-cowork-export import <bundle> [--cowork-root PATH]
                                     [--remap SRC=DST]...
                                     [--skip-auth] [--dry-run] [--force]
```

把 bundle 还原回本机 Cowork 沙盒，使 task 出现在 Cowork 侧边栏可续聊。
自动处理沙盒前缀改写和跨平台路径转换；**跨平台 `--include-auth` 会被拒绝**——
Cowork 的 auth 工件被源 OS 的 keystore 加密（macOS Keychain / Windows DPAPI），
对方解不开。

| Flag | 默认值 | 作用 |
|---|---|---|
| `<bundle>` | _(必填)_ | `export` 产出的 bundle 目录。 |
| `--cowork-root PATH` | _(自动检测)_ | 覆盖目标沙盒根。 |
| `--remap SRC=DST` | _(无；可重复)_ | 把源机器上的 `userSelectedFolders` / `userApprovedFileAccessPaths` 项 `SRC` 改写为目标机器上的 `DST`。任何在目标机器上不存在的源路径都需要 `--remap`。 |
| `--skip-auth` | _(关)_ | 即使 bundle 里有 `auth/` 也忽略；不动本机鉴权状态。 |
| `--dry-run` | _(关)_ | 只打印 rewrite 计划，不实际写入。 |
| `--force` | _(关)_ | 允许覆盖同 id 的已存在 task。 |

**示例：**

```bash
# 同 OS、原沙盒 —— 直接从 bundle 重建
claude-cowork-export import ./exports/<task-id>

# 把源机器的 user folders 映射到目标机器
claude-cowork-export import ./exports/<task-id> \
  --remap "/Users/<your-username>/Documents/Claude/Projects/<project>=D:\<project>" \
  --remap "/Users/<your-username>/code=C:\code"

# 先看会做什么，再决定要不要写
claude-cowork-export import ./exports/<task-id> --dry-run

# 跨平台：bundle 里有 auth/ 但目标平台还原不了——明确跳过
claude-cowork-export import ./exports/<task-id> --skip-auth
```

### `seed`

```
claude-cowork-export seed <bundle> [--mode brief|standard|full] [-o PATH]
```

从 bundle 渲染一份自包含的 Markdown 续聊 prompt。把它贴到一个**全新**的
Cowork 对话里当首条消息——无论是哪个账号、哪台机器——就能让新对话从上一次停下的
地方接着走。**不动鉴权、不依赖任何服务端状态**：新对话确实是全新的，只是被前一次的
上下文初始化了。

| Flag | 默认值 | 作用 |
|---|---|---|
| `<bundle>` | _(必填)_ | bundle 目录路径。 |
| `--mode brief\|standard\|full` | `standard` | 把多少历史装进 prompt。详见下表。 |
| `-o`, `--output`, `--out` | `<bundle>/seed-prompt.md` | prompt 输出位置。 |

**模式：**

| 模式 | 包含 | 30 轮 task 典型大小 |
|---|---|---|
| `brief` | 元数据 + initial brief + 文件清单 + 最后 3 轮**原文**。 | ~50 KB |
| `standard` | 以上 + 每个 user prompt + 助手回复**压缩到 500 字**+ 工具调用摘要；最后一轮**原文**。 | ~70 KB |
| `full` | 全部**原文**，含思考块和工具结果。 | ~225 KB |

相比 CC 的 seed，Cowork 这个版本多出来几个字段：
- task 的 `initialMessage` 单独成一个 "Initial brief" 块，对话漂偏了也能把长期目标
  拉回视线，
- task 的 `Model` / `Space` / `userSelectedFolders` / `isArchived` / 最后报错——
  Cowork 记下来的任何东西。

**示例：**

```bash
# 默认 —— 体积和保真度的平衡
claude-cowork-export seed ./exports/<task-id>

# 只要最尾巴几轮
claude-cowork-export seed ./exports/<task-id> --mode brief

# 一字不漏全装上
claude-cowork-export seed ./exports/<task-id> --mode full -o ./seed-full.md
```

---

## Bundle 结构

每次 `export` 都为每个 task 产出一个独立目录：

```
exports/<task-id>/
├── manifest.json        # 版本 + 源平台 + 沙盒前缀 + auth 信息
├── transcript.jsonl     # 原始 source（macOS 上是原生 .jsonl；Windows 是 audit.jsonl）
├── task.json            # Cowork 原始 task 元数据
├── audit.jsonl          # Cowork 审计日志（若它本身就是 transcript 则省略）
├── session.html         # 渲染版阅读视图（Marked + highlight.js）
├── session.md           # GitHub 风格 Markdown
├── session.json         # 结构化逐块 dump（给 LLM 喂）
├── session.csv          # 扁平表格（给电子表格用）
├── README.md            # 自动生成的 bundle 摘要
├── uploads/             # 用户上传的文件
├── outputs/             # 助手生成的文件（Cowork chat 工作目录）
├── assets/              # outputs/ 目录之外被 Write/Edit 的文件
│   └── <相对路径>
├── auth/                # 仅在用 --include-auth 时存在
│   ├── buddy-tokens.json
│   ├── Local State
│   ├── Cookies
│   └── ...
└── seed-prompt.md       # 仅在跑过 `seed` 后存在
```

### manifest.json 字段说明

| 字段 | 类型 | 含义 |
|---|---|---|
| `bundle_version` | int | bundle 格式版本，当前为 `1`。 |
| `tool` | string | 恒为 `"claude-cowork-export"`。 |
| `tool_version` | string | 导出时的工具 semver。 |
| `exported_at` | ISO-8601 string | 导出时刻（UTC）。 |
| `source_platform` | string | `"darwin"` / `"win32"` / `"linux"`。 |
| `source_path_sep` | string | `"/"` 或 `"\\"`。 |
| `source_home` | string | 源机器的 `$HOME`（信息字段）。 |
| `source_userdata` | string | 源端 Cowork 的 user-data 根（`local-agent-mode-sessions` 的父）。 |
| `source_sandbox_prefix` | string | task 所在的 `<userdata>/local-agent-mode-sessions/<acct>/<workspace>/`。`import` 会改写为目标的前缀。 |
| `source_task_id` | string | Cowork task UUID。 |
| `source_cli_session_id` | string | task 内部 Claude Code 会话 UUID。 |
| `source_cwd` | string | task 的 `outputs/` 目录（即 chat cwd）。 |
| `source_user_folders` | string[] | Cowork 记录的 `userSelectedFolders`（chat 外挂的项目目录）。 |
| `auth` | object | `{"included": false}` 或 `--include-auth` 时的详细信息。 |

`import` 完全靠 `manifest.json` 驱动改写；缺 manifest 的旧 bundle 会被明确拒绝，
并指引如何重新导出。

---

## Cowork 数据在哪

不同 OS Cowork 沙盒放的位置不一样。工具自动检测、并把重复合并。

| OS | 路径 |
|---|---|
| macOS | `~/Library/Application Support/Claude/local-agent-mode-sessions/` |
| Linux | `~/.config/Claude/local-agent-mode-sessions/` |
| Windows（原生安装） | `%APPDATA%\Claude\local-agent-mode-sessions\` |
| Windows（Microsoft Store / MSIX） | `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude\local-agent-mode-sessions\` |

每个 task 的结构：

```
<sandbox>/<account-uuid>/<workspace-uuid>/
  spaces.json                                 # space（项目）登记表
  local_<task-id>.json                        # task 元数据
  local_<task-id>/                            # task 沙盒目录
    .claude/projects/<encoded-cwd>/<cli-id>.jsonl   # macOS 原生 transcript
    audit.jsonl                               # Windows 上当作 transcript
    uploads/                                  # 用户上传文件
    outputs/                                  # 助手生成文件（= chat cwd）
```

Windows MSIX 安装下，公开的 `%APPDATA%\Claude\...` 视图只是 MSIX `LocalCache` 的
**部分镜像**。要看到所有 task 必须两边都扫。工具默认就这么做，需要时用
`--cowork-root` 强制指向某一边。

---

## 常见工作流

### 1. 个人备份

```bash
# 周期性 dump 所有 task 到备份盘
claude-cowork-export export all --output /Volumes/Backup/cowork-$(date +%F)
```

bundle 自包含、可直接在浏览器打开；`session.json` / `session.csv` 也很稳定，
可以 grep / diff / 喂给另一个 LLM。

### 2. 同 OS 迁机（新机器、同账号）

```bash
# 旧机器
claude-cowork-export export latest --output ./bundle --include-auth

# 把 ./bundle 拷到新机器（**当成 SSH 私钥那样对待**——见 [安全](#安全)）

# 新机器（同 OS 家族）
claude-cowork-export import ./bundle
# 打开 Cowork，task 在侧边栏，接着聊就行。
```

### 3. 跨 OS 迁移（macOS ↔ Windows）

```bash
# 源端：macOS
claude-cowork-export export latest --output ./bundle

# 拷过去。Windows 目标：
claude-cowork-export import .\bundle --skip-auth \
  --remap "/Users/<your-username>/Documents/Claude/Projects/<project>=D:\<project>"

# 然后在新 OS 上正常登录 Cowork——auth 不跨 keystore。
```

### 4. 跨账号 / 跨机器续聊（seed 模式）

目的地是**另一个 Anthropic 账号**时，服务端状态没法迁移。用 seed 模式把上下文
内联进新对话即可：

```bash
# 源机器
claude-cowork-export export latest --output ./bundle
claude-cowork-export seed ./bundle/<task-id>
# → ./bundle/<task-id>/seed-prompt.md

# 拷过去，目标端：
# 1. 用新账号开一个全新的 Cowork 对话。
# 2. 把 seed-prompt.md 的内容粘进首条消息。
# 3. 等助手确认它吸收完上下文。
# 4. 想让新机器也有文件，可以同时跑 `import`。
```

### 5. 在 Cowork 里删 task 之前先归档

```bash
claude-cowork-export export <task-id> --output ./archive
# 现在可以放心从 Cowork 删——bundle 是独立的。
```

---

## 跨平台说明

| 关注点 | 行为 |
|---|---|
| 沙盒路径 | `import` 把 `manifest.source_sandbox_prefix` 改写成目标端自动检测出的前缀。 |
| 路径分隔符 | 按 `manifest.source_path_sep` 和目标平台改写 `/` ↔ `\`。 |
| 外挂文件夹 | `userSelectedFolders` / `userApprovedFileAccessPaths` 在沙盒外，不会自动翻译，需要显式 `--remap`。 |
| 大小写敏感 | 源是 Windows 时前缀匹配走 `os.path.normcase`。 |
| `LongPathsEnabled` | Cowork 沙盒路径嵌套很深，Windows 可能需要在注册表打开 `LongPathsEnabled`。 |
| 控制台编码 | windows 分支在启动时把 `sys.stdout` / `sys.stderr` 重配为 UTF-8。 |
| Windows MSIX vs 原生 | `_cowork_roots()` 同时返回 `%APPDATA%\Claude\...` 和 `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\...` 两个根，按 task id 合并。 |

---

## 安全

`--include-auth` 会把 Cowork 的 auth 工件（Cookies、Local State、OAuth token 等）
打进 bundle。**像对待 SSH 私钥一样对待这个 bundle。**

- 谁拿到 bundle，谁就能在你 rotate 之前以你的身份行事（在别处登 Cowork、改密码、
  在账号页 revoke 设备等都算 rotate）。
- 工具会打印多行警告，并要求**交互式输入 `I UNDERSTAND`**才会真的产出含 auth
  的 bundle。`--yes-i-know-this-is-risky` 只为 CI 跳过这个 prompt。
- **跨平台 auth 还原会被拒绝**。Cowork 的 auth 工件在 macOS 上由 Keychain
  加密、在 Windows 上由 DPAPI 加密，两者不互通。`--include-auth` 在 macOS 导出
  的 bundle 只能在另一台 macOS 还原 auth，Windows ↔ Windows 同理。
- 不带 `--include-auth` 的 bundle（默认）只含对话文本、上传、输出和元数据，放
  未加密云盘也无所谓。
- `import` 覆盖同 id 已存在 task 必须显式 `--force`。

---

## 故障排查

**`list` 比 Cowork 侧边栏少几个 task**
Windows MSIX 安装下公开的 `%APPDATA%` 镜像不完整。工具默认会同时扫 MSIX
LocalCache 合并；如果还是缺，明确用 `--cowork-root` 指到 LocalCache 路径再
看一遍。

**`list` 出现重复 task**
两个 root 都有同 id 的记录；工具按 id 去重并挑更完整的一份。如果还是重复，
分别用 `--cowork-root` 指向两个 root 各跑一次确认。

**HTML 里 `User Prompts` 目录有重复行**
v0.1.3 起已经在 audit-mode 解析时按 record 内容签名去重了。如果还能看到，说明
你装的是更老版本——按当前分支重装。

**`error: cross-platform --include-auth is not supported`**
Cowork 的 auth 工件平台加密（macOS Keychain ↔ Windows DPAPI），换 OS 解不开。
不带 `--include-auth` 重导，目标端正常登录即可。

**`error: --remap is required for "<folder>" — it doesn't exist on this machine`**
`userSelectedFolders` 指向目标机器上不存在的路径。加 `--remap "/旧路径=/新路径"`
（可重复）来翻译。

**`seed-prompt.md` 太大粘不进一条消息**
用 `--mode brief` 出最小版，或者手动切分。

---

## 分支

仓库刻意维护两条长期分支：

| 分支 | 面向 | 备注 |
|---|---|---|
| `macos`（默认） | macOS / Linux | 干净的跨平台基础。 |
| `windows` | Windows | 加上 UTF-8 stdout 重配、大小写不敏感路径比较、MSIX LocalCache 发现；其它逻辑相同。 |

两条分支始终版本对齐，按目标平台装对应那条。

---

## License

MIT。详见 [LICENSE](LICENSE)。
