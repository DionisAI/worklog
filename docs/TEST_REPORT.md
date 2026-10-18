# Worklog Frontier v0.2.0 — 测试与交付验证

日期：2026-10-16。以下为实际执行结果，不是计划中的测试。英文摘要见 [TEST_SUMMARY.md](TEST_SUMMARY.md)。

## 2026-10-18 更新

"完整测试"及之后各章是 0.2.0（2026-10-16）的原始记录。此后已合入 Hook 失效开放、
上下文预算、会话结束释放卡片等改动。当前情况：

- GitHub Actions（`.github/workflows/ci.yml`）在 ubuntu-latest 与 macos-latest ×
  Python 3.11 / 3.12 / 3.13 上运行。2026-10-18 在 main（`cdf6c44`）上的首次运行六项全部通过
  （197 项 pytest + demo）。
- CI 每项另外执行：统计子进程的覆盖率（门槛 90%）、1,000 轮 `tools/soak.py`、
  构建 wheel 并运行 `tools/smoke_release.py --wheel`（不含 v0.1 迁移部分）。
- 覆盖率现在把 CLI/Hook 子进程计算在内（`[tool.coverage.run] patch = ["subprocess"]`）。
  2026-10-18 本地 macOS（Apple Silicon）上 Python 3.11 / 3.12 / 3.13 多次运行实测 91.29%–91.38%
  （1,160 条语句），cli.py 89%；不统计子进程时 cli.py 约 65%（0.2.0 记录为 57%）。
- 普通目录 store（`.worklog/`）现在带有固定的 `.gitignore`（内容为 `*`），所以当前 soak 统计
  5 个文件（4 个核心文件 + `.gitignore`），`tools/soak.py` 也按 5 断言；下表中的"4"是 0.2.0 的值。

## 完整测试

**131 项通过，0 失败，0 错误，0 跳过。**
最后一次完整 pytest 执行显示耗时 23.77 秒。
实测平台：`Linux-6.18.44-x86_64-with-glibc2.41`，Python `3.13.5`。
语句覆盖率：**83.67%**，覆盖 871 / 1041 条语句。
这不是分支覆盖率；许多 CLI/Hook 子进程路径通过功能测试，但不包含在主测试进程覆盖率统计内。

```sh
python -m pytest -q --cov=worklog --cov-report=term \
  --cov-report=json:docs/evidence/0.2.0/coverage.json --junitxml=docs/evidence/0.2.0/test-results.xml
python -m compileall -q src tests tools examples
```

覆盖范围包括：当前状态原地替换、全局 3 槽位、UTF-8 字节上限、整卡与全局容量上限、
完成后删除、只读无写入、相同更新不落盘、旧版本拒绝、删除重建 ABA、未知字段拒绝、
8 个真实独立进程竞争仅一人认领成功、8 张独立卡并发更新无丢失、跨 Git worktree 共享、
从普通目录初始化 Git 后继续共享、重复崩溃只留一个固定临时文件、发布前失败不改旧状态、
发布后响应丢失可重读、损坏检测、临时文件符号链接保护、临时 Session 状态有界/过期/移除、
Hook 去重/一次提醒/子 Agent 身份分离、配置合并与旧回调清理、迁移中断恢复和显式遗漏检查。

## 一万轮增长测试

执行 `tools/soak.py --iterations 10000`，使用真实本机文件写入、fsync 与原子替换。
这是加速的合成负载，不是让真实 Agent 连续运行数月。

一轮包括一次当前卡替换、一次历史槽位替换，以及一个新的临时 Hook Session 更新。
共 **20,000 次语义状态写入、10,000 个不同临时 Session**。
固定长度内容用于检验时间/历史数量增长本身是否增加存储，不代表真实项目通常只有 576 字节。

| 已执行轮数 | 当前 frontier 字节 | 临时 Hook 状态字节 | 核心文件数 | 总文件字节 |
|---:|---:|---:|---:|---:|
| 32 | 576 | 3,538 | 4 | 4,246 |
| 100 | 576 | 3,538 | 4 | 4,246 |
| 1,000 | 576 | 3,538 | 4 | 4,246 |
| 10,000 | 576 | 3,538 | 4 | 4,246 |

实际最大 frontier：576 字节；最大临时 Hook 状态：3538 字节。
当前卡一直为 1 张，历史节点始终不超过 3 个；没有事件历史、产物副本或旧状态快照。
另执行 1,000 次 context 读取和 1,000 次状态读取，文件内容和 mtime 均无变化。
再创建并完成/删除 200 张短任务卡，仍只剩 1 张当前卡与 4 个核心文件。
该次增长测试耗时 33.529 秒。

生产约束是 frontier <=32 KiB、临时 Hook 状态 <=16 KiB、最多16张当前卡、全局最多3个历史节点；
这些边界均在写入前检查。溢出明确拒绝，不把旧卡静默截掉。

## 独立安装包验证

通过离线 `pip wheel --no-deps --no-build-isolation --no-index` 构建 wheel，
在**全新独立虚拟环境**中安装，删除 PYTHONPATH 后执行。验证实际从 site-packages 导入，
而不是碰巧使用源码目录。`worklog --version` 返回 0.2.0。

实际跑通：创建当前卡 → 认领 → 更新并交接 → 另一个身份接班 → 填充三个历史槽位 →
替换一个槽位 → 删除完成卡。另跑通独立安装环境中的 demo。

分别安装 Claude/Codex 项目 Hook，并真实执行安装器生成的 SessionStart 命令，
返回有效 JSON 与当前 frontier。这验证了安装路径、打包 Skill、命令与接口形状；
**没有登录或调用真实 Claude/Codex 账户。** Agent 身份标签是模拟的。

## 真正 v0.1 wheel 的迁移验证

另建独立虚拟环境安装原始 v0.1.0 wheel，由它生成含 14 个真实事件文件的工作目录，
包括当前语义检查点和旧版两套 Hook。再用已安装的 0.2 wheel 预览并应用迁移。

结果：保留实际最新语义进度；迁移后仅 3 个核心文件；不建立历史档案；
旧 events/artifacts/views 被删除；两套旧 Hook 被替换；旧版程序拒绝打开新版 schema。
实际升级测试发现并修正了旧版检查点使用 observed_at、交接使用 created_at 的时间字段差异，
补充回归测试，避免最新进度回退为原始任务目标或更旧交接。

复现命令：

```sh
python tools/smoke_release.py \
  --wheel /path/to/workspace_worklog-0.2.0-py3-none-any.whl \
  --legacy-wheel /path/to/workspace_worklog-0.1.0-py3-none-any.whl
```

旧版 wheel 不随新版源码包重复打包；测试新版独立安装时可省略 --legacy-wheel。

## 未声称完成的验证

未在 Windows、网络文件系统或真实断电情境下测试。未通过真实登录的 Claude/Codex UI
完整运行工作会话；也未把原生插件发布到任何 marketplace。
0.2.0 记录中的"未在 macOS 实机、Python 3.11/3.12 上测试"和"未实际运行远端 GitHub CI"
已不再成立：2026-10-18 起 CI 在 macOS 与 Linux 上覆盖 Python 3.11–3.13 并全部通过（见上方更新）。

这不是任务审计平台、操作系统沙箱或自动验收器。语义更新仍需 Agent 使用 Skill/CLI；
硬性规则保证的是当前数据的边界，不保证模型永远选择正确的三个教训。

## 原始记录

0.2.0 运行生成的文件在仓库 `docs/evidence/0.2.0/` 中：pytest-output.txt、test-results.xml、
coverage.json、soak-result.json、release-smoke.json、demo-output.txt、verification-summary.json、
runtime-source-checksums.json。源码包不再打包这些文件。原有的 soak-console.txt 与
soak-result.json 内容重复，wheel-build.txt 含构建沙箱路径，两者已删除。

当时逐项比对，wheel 内运行时代码与该次测试的源代码/Skill 相同（runtime-source-checksums.json）；
此后的提交已修改这些源码。wheel 不能逐字节复现（zip 内记录构建时的文件时间，同一源码连续构建两次
SHA-256 不同），因此不再给出 wheel SHA-256；verification-summary.json 中的 `wheel_sha256`
只是当次构建的记录，无法用于复核。
