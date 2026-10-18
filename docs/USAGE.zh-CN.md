# Worklog Frontier v0.2 使用指南

## 核心规则

只保存「目前做到哪里、下一步做什么、仍有效的注意事项」，再加上整个 Workspace
共用的 **最多 3 个历史关键节点**。不是每个任务 3 个，也不是每个 Session 3 个。

历史关键节点用固定槽位 1、2、3；写入同一槽位会替换旧内容。三格满了，就选择最不重要
的一格替换或压缩成一个教训，不会悄悄新增第 4 格。不强迫填满 3 格。

每条当前工作线只有一张状态卡。换模型、换账号、换 Session 仍然更新原来的卡。
完成后删除，不归档。正在阻塞工作的错误仍属于当前 frontier，不需要挤进历史教训。

## 安装与升级

Python 3.11+，面向 macOS/Linux。解压源代码后：

```sh
cd worklog
uv tool install --force .
worklog --version
```

也可以在独立虚拟环境安装 wheel：

```sh
python3 -m venv "$HOME/.venvs/worklog"
"$HOME/.venvs/worklog/bin/python" -m pip install --no-deps --force-reinstall \
  /实际路径/workspace_worklog-0.2.0-py3-none-any.whl
export PATH="$HOME/.venvs/worklog/bin:$PATH"
```

不要把新的虚拟环境放在目标项目里。输出版本应为 `0.2.0`。

未使用过 Worklog 的项目：

```sh
cd /path/to/project
worklog init
worklog install claude
worklog install codex
```

安装器只替换 Worklog 自己的 Hook，保留其他设置，清除 v0.1 的旧 Worklog 回调。
重新打开 Agent；在宿主要求时审核并信任新 Hook。项目 Hook 与原生插件两种方式选一种，
不要同时启用。`install --dry-run` 可以先看配置变更，不保存新配置。

`worklog launch --harness claude --account claude-a -- claude` 提供独立的 Session 标识；
Codex 同理。`--account` 只是记录别名，不会登录或切换认证账号，也不会复制密钥。
这一版没有心跳线程、常驻守护进程或额外模型 API。

## Agent 应怎样维护当前卡

新增一条真正的新工作线：

```sh
worklog add replay --title '修复重复成交计数' \
  --current '已定位重复分支，尚未验证修复' \
  --next '添加回归测试'
worklog show replay
```

`show` 返回卡片的 `revision`。以下 `CARD_REV` 等都是占位符，必须换成实际返回的完整值，
不能猜测或只用前几位。认领时使用 Hook 提供的 Session ID：

```sh
worklog claim replay --expect CARD_REV --session SESSION_ID --harness claude
worklog show replay
```

每次有实质进展后，读取最新版本并原地更新：

```sh
worklog update replay --expect NEW_CARD_REV --session SESSION_ID \
  --current '修复已写入；小样本通过，全量回放结果还没检查' \
  --next '先检查已在运行的回放结果，不要重启一份' \
  --caution '乱序事件不能只按时间戳去重' \
  --reference 'tmux:replay:0'
```

`--current`、`--next` 覆盖该字段，不追加。重复的 `--caution` 构成新的**完整注意事项列表**，
会替换旧列表；`--reference` 同理。未传入的字段保留。已解决事项用 `--clear-cautions` 或新的
完整列表移除，引用可用 `--clear-references` 清空。

`checkpoint` 与 `update` 是同一件事，不生成历史检查点。不要在 current 内继续追加
“第 1 次、第 2 次、第 3 次……”的 Session 日记。

更新遇到 conflict 时，重新读取并合并当前状态，不要把旧内容直接套上最新 revision 再覆盖。
不同卡片独立进行版本检查，不会因为别人更新了另一张卡就拒绝你的更新。

## 交接与结束

```sh
worklog handoff replay --expect CARD_REV --session SESSION_A \
  --current '实现已完成，等待检查全量回放' \
  --next '查看已有任务结果，再决定是否需要继续改动'

# 接班者
worklog show replay
worklog resume replay --expect NEW_CARD_REV --session SESSION_B --harness codex
```

handoff 更新同一张卡并释放负责人，不生成交接档案。接班者应核对真实代码和现有运行任务。
同一个 checkout 只允许一个协作写入者；并行改代码时使用不同 Git worktree，它们共享 frontier。
Worklog 不会阻止绕开工具的任意文件修改。

Session 结束（SessionEnd Hook 或 `worklog launch` 退出）时，会在一个事务里释放它在本 checkout
认领的卡：清除负责人，`working` 改为 `needs_check`。进程结束不等于后台 Job 结束，接手者要先核对。
用 `WORKLOG_SESSION_ID` 固定身份的 launch 会话在 `/clear`、resume 之后保留卡片。
异常崩溃的会话不会触发 Hook，卡片仍被占用：认领被拒时，错误信息会指出占用的卡与负责人，并给出完整的
`worklog takeover CARD --expect CARD_REV --session 你的_SESSION --confirm-old-writer-stopped` 命令
（只有在 Git 仓库里才建议改用独立 worktree）。确实核对旧写入者已停止后再执行。
它不会替你停止旧进程，也不会自动重启任何任务。

彻底完成并检查未解决事项、外部任务与依赖后：

```sh
worklog finish replay --expect CARD_REV --session SESSION_B --confirm
```

这会直接删除当前卡，不留历史。需要继续影响其他工作的结果，先写进相应的当前卡；
真正值得记住的错误或决策，先放入 3 个历史槽位之一。finish 不会自动执行测试或证明验收成功。

## 三个历史槽位

```sh
worklog status
worklog note 1 --expect WORKSPACE_REV \
  --text '曾只按时间戳去重，误删乱序成交；应使用稳定事件标识。'
```

这里必须用 `status` 顶层的 Workspace revision，不是某张卡的 revision。写入 1、2、3 中
已经占用的槽位，会覆盖旧关键节点；旧节点不会备份。`worklog note 4 ...` 会直接拒绝。

```sh
worklog note 1 --expect NEW_WORKSPACE_REV --clear
```

## 自动 Hook 做什么，不做什么

启动与压缩后读取当前 frontier；后续新消息只有出现本会话没见过的变化才重新注入。带 `--session` 的
本会话 CLI 写入（claim/update/handoff/finish/add/note/overview）算作已见，除非在它之前还有其他会话的写入。
注入内容开头是说明、本会话身份和每张卡一行的索引（id | 状态 | 负责人 | 标题）。Claude Code 的 Hook
上下文上限是 10,000 字符（超出后只显示前 2,000 字符预览）：完整内容超过约 9,500 字符时，只完整显示你自己的卡，
保留索引、概览和历史节点，其他卡用 `worklog context --card ID` 查看。Codex 与 `worklog context` 仍是完整内容。可能改动文件的工具调用
只设置一个临时 dirty 布尔值，不记录命令、回复或完整工具输出；其他工具不会触发 Hook。
每段可能改动文件的工具使用之后，Stop 最多提醒一次并清除标记；续写期间若再调用可能改动文件的工具
（包括用 Bash 读取 `worklog status`）且未执行 update/ack，下一轮 Stop 仍会提醒。
已有语义更新会清除提醒；若只是只读操作、没有实质变化，执行：

```sh
worklog ack --session SESSION_ID
```

不要为了消除提醒编造工作进展。Hook 本身不会推断根因、下一步或测试成功；仍需要 Agent
遵循 Skill。中途也要更新当前卡，异常退出后无法保证保住上一次更新以来的进展。

Hook 出错时一律放行（退出码 0）：存储损坏、尚未迁移或输入异常只写到 stderr，并在会话开始／
提交消息时显示一条警告，不会阻塞提问、工具调用或停止。超过 1 MiB 的输入会被读完并忽略（该次工具调用
不会标记 dirty）。生成的 Hook 命令使用 `python -P`，不会自动把会话目录加入 sys.path，项目里的
`worklog.py`、`json.py` 不会被当作 Hook 代码导入（PYTHONPATH 中的条目，包括空条目，仍然生效）。
升级后重新运行 `worklog install`。

临时 Hook 标记最多 32 个、总共 16 KiB、24 小时过期，SessionEnd 清除；超量只淘汰这些缓存，
不会淘汰当前工作卡或历史关键节点。`worklog context/status/show` 本身不写入任何状态。
它们只以只读方式打开锁文件并加共享锁，所以存储目录不可写时也能读取（例如 Codex workspace-write
沙箱中只读的 `.git/worklog`）；在那里写入会返回 `read_only` 错误，并给出存储目录路径。

## Codex 沙箱

Codex 的 workspace-write 沙箱（`:workspace` 权限配置）让 `<repo>/.git` 只读，而 Git 仓库的存储在
`<git-common-dir>/worklog`；在链接工作树里，它更是完全在可写工作区之外。读取（`status`、`show`、
`context`）照常可用。`worklog install codex` 还会写入 `.codex/rules/worklog.rules`：这条 Codex 规则让
frontier 写入命令（`add`、`update`、`checkpoint`、`handoff`、`claim`、`start`、`resume`、`takeover`、
`finish`、`note`、`overview`、`ack`）不经询问在沙箱外运行。`launch`、`install`、`init`、`migrate`、
`clear-runtime` 以及带 `--root` 的写法仍走 Codex 的正常审批。Codex 只在受信任的项目里加载项目规则
（与 `.codex/hooks.json` 相同），并且规则功能仍属实验性。被放行的命令会在沙箱外运行 PATH 上第一个
`worklog`，Hook 也是如此：请把 Worklog 安装在项目之外（不要以可编辑方式从项目安装，也不要装在项目内的
虚拟环境里），否则 Agent 可以改动这段代码从而离开沙箱。

没有这条规则时（项目未受信任、有托管策略限制，或使用不带规则文件的原生插件），写入会返回 `read_only`，
Stop 提醒会让 Agent 报告存储路径和本来要保存的更新，而不是反复重试。备选做法是让存储目录可写：用
`worklog stats` 的 `"store"` 取得绝对路径（链接工作树的存储在主检出的 `.git` 里），在
`~/.codex/config.toml` 中授权。用权限配置时，只有这个目录变为可写，`.git` 其余部分仍然只读
（已用 codex-cli 0.163.0 验证）：

```toml
default_permissions = "worklog"

[permissions.worklog]
extends = ":workspace"

[permissions.worklog.filesystem]
"/path/to/repo/.git/worklog" = "write"
```

若任何配置层设置了 `sandbox_mode`，Codex 会忽略 `default_permissions` 而使用旧设置：在
`[sandbox_workspace_write]` 下写 `writable_roots = ["/path/to/repo/.git/worklog"]`，或单次会话用
`codex --add-dir /path/to/repo/.git/worklog`。这两种是 Codex 文档中的做法，本项目未实际验证。

## 不会无限增长的边界

| 对象 | 硬上限 |
|---|---|
| 当前状态文件（包含结构与校验） | 32 KiB，UTF-8 字节 |
| 当前工作卡 | 最多 16 张，每张最多 4 KiB |
| 历史节点 | 整个 Workspace 最多 3 个，每个文本最多 1 KiB |
| 每卡注意事项／引用 | 各最多 8 条，每条 384 字节 |
| 临时 Hook 状态 | 最多 16 KiB／32 个标记 |
| 永久事件／旧版快照／Session 档案／产物副本 | 不保存 |

中文按 UTF-8 字节计费，不是“1 字符 = 1 字节”。容量满了明确拒绝该次写入，要求整理当前卡，
不会偷偷截掉未完成工作。这里的 16 张卡是防止 current 也变成无限积累列表的边界，不是任务排程器。

Git 仓库使用 `<git-common-dir>/worklog/`；普通目录使用 `.worklog/`，`worklog init` 会在其中写入
固定的 `.gitignore`（`*`），之后执行 `git init` 也不会把它提交（已有目录可重新运行 `worklog init` 补上）。
正常核心文件只有 workspace.json、frontier.json、worklog.lock、可选 runtime.json；普通目录的 `.worklog/` 另有上述固定 `.gitignore`。
写入用固定临时文件与原子替换；反复中断不会留下越来越多随机临时文件。没有旧 frontier 备份。
checksum 用于检测意外损坏，不是签名。损坏会报错，不会自动当作空项目重新开始。

字节上限仅覆盖 Worklog 管理的状态，不替你清理 Claude/Codex 自身对话记录、Git 历史、
你手工导出的文件，或 references 指向的外部日志。

## 从 v0.1 迁移

先停掉所有旧版 Agent/launcher。外部训练或回放进程可以继续，但要确认没有旧 Worklog 写入者。

```sh
worklog migrate --plan > /tmp/worklog-frontier-review.json
```

打开该 JSON，检查 `frontier.cards`：只保留未完成工作当前有用的状态；默认不会凭空推断历史教训。
需要的最多 3 个历史节点写入 `frontier.notes`，键为字符串 `"1"`、`"2"`、`"3"`。
超限内容必须人工或由 Agent 整理，迁移不会截断。若某张旧卡已被明确合并/解决并因此不再保留，
在 `acknowledged_omissions` 列明其旧 ID，防止静默遗漏未完成任务。

```sh
worklog migrate --apply /tmp/worklog-frontier-review.json \
  --discard-history --confirm-agents-stopped
worklog init   # 普通目录的 .worklog/ 会补上 .gitignore
worklog install claude
worklog install codex
worklog doctor
```

应用会校验旧账本是否在预览后变化，然后保存有界 frontier，删除旧 events、artifacts、views。
**不会另存一份完整历史。** 迁移中断时，正常使用会暂停；使用同一份已审阅 proposal 重试即可完成。
成功后别拿旧 proposal 覆盖后来的新进展。审阅导出文件不再需要时自行移除，它不属于运行时记录。

## 验证

```sh
worklog stats
worklog doctor
# 源码包内
python -m pytest -q
PYTHONPATH=src python examples/demo.py
PYTHONPATH=src python tools/soak.py --iterations 10000
```

测试结果、实际执行平台与未实测部分以附带 TEST_REPORT.md 为准。
