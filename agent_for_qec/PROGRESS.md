# agent-for-QEC PROGRESS

STATUS: RUNNING

## 每次运行必读的规则

1. 你在 `/nvme2n1/yuehan_zhang/LightStim-upstream` 的 `agent-for-QEC` 分支上以无人值守方式运行。先 `git status` 和 `git log -3`，再读本文件。如果工作区有未提交改动，说明上一次运行被中断（通常是账号额度用尽），先接着把那一阶段做完。
2. 一次运行只做"下一阶段"里的事。做完后：把该阶段移到"已完成"，写清产出文件、关键数字、结论、失败原因；然后写出新的"下一阶段"（具体到可执行的步骤）。最后 `git add` 相关文件并本地提交（作者用仓库现有配置，提交信息英文，不加任何 Co-Authored-By），不要 push。
3. 动 LightStim 之前先读 `skills/SKILL.md`，按任务再读对应 skill（`extend-new-code`、`builder-tracker-api`、`logical-coupler-design`、`gotchas`）。Python 一律 `PYTHONPATH=. /home/yuehan/miniconda3/envs/light_stim/bin/python`（仓库里没有 venv）。改了 `lightstim/` 下的代码，提交前必须 `-m smoke` 测试全绿。
4. 判据只有三条：码距离精确值 d；逻辑操作电路的电路级距离精确值等于 d；逻辑作用带符号正确（stim flow）。全部用 `agent_for_qec/tools/verify_stack.py`。**不要跑 LER 采样**，那是最后的评估步骤，不由你做。
5. 结果登记到 `agent_for_qec/LEDGER.md`，三类行都要写：新且容错 / 找到但文献已有 / 尝试失败。负结果必须留下。每一行都要有可复现的证据（脚本路径 + 命令 + 输出摘要）。
6. 找到一个通过验证的候选后，立刻用 WebSearch 查它是否已被发表（至少 3 个不同关键词组合），把查到的引用和"与我们的差别"写进 LEDGER。
7. 并行限制：同一时刻最多 2 个子 agent，不要用 Workflow 工具；模拟 `num_workers ≤ 16`；单条命令用 `timeout 3600` 包住；不用 GPU。
8. 不要修改 `agent_for_qec/runner/`、`.secrets`、`agent_for_qec_runner/` 下的任何东西。
9. 时间/轮次预算用完之前主动收尾：先把 PROGRESS.md 和 LEDGER.md 写好再做别的。

## 已完成

- **Phase −1（人工，2026-09-28）**：搭好 pipeline 骨架和验证栈。`tools/verify_stack.py` 自测：旋转表面码 d=3/5/7 码距离精确 3/5/7；flow 签名检查在 CNOT 上区分出了错号。电路级距离的教训：`MemoryExperiment` 不传 `extraction_block_class` 时用的是通用染色块 `GenericCSSColorationExtractionBlock`，d=5/7 电路级距离只有 3/5；显式用 `RotatedSurfaceCodeExtractionBlock(scheduling='perpendicular')` 则 d=5 精确等于 5（HiGHS MILP 18 秒证明）。精确证明用 MILP，z3 在 d=5 上 10 分钟证不出 UNSAT，只作后备。完整对照见 `phase0/schedule_calibration.out`（由 `phase0/schedule_calibration.py` 生成）。

## 下一阶段

### Phase 0：走通整条流程（校准，不做探索）

目标：用已知对象把"构造 → 验证 → 登记 → 文献核查 → 提交"整条链跑一遍，并把发现的问题修掉。全部对象都是已知的，这一阶段的产出全部登记为"校准"行。

1. 读 `phase0/schedule_calibration.out`（已由人工跑出：四种 SE block/调度 × X/Z 基 × d=3,5,7 的电路级距离）。对每个不满距离的配置，用 `search_for_undetectable_logical_errors` 的 witness（`circuit_error_locations`）解释距离掉在哪：哪个 tick 的哪个两比特门、hook 沿哪个方向、为什么通用染色块会这样而 `perpendicular` 不会。把解释写进 LEDGER 对应行的"先前工作/说明"列。
2. 如果 `swapped` 或 `parallel` 在某个基下不满距离，说明该基下哪类 stabilizer 的 hook 方向与逻辑算子平行，给出一句话规则；不需要改 `SE_block.py`。
3. 在满距离调度下验证一个已知逻辑操作：两个旋转表面码 patch 之间的横向（transversal）CNOT（`lightstim/protocols/cnot_trans.py`）。用 `verify_stack.check_flows` 验证四条带符号 flow（X_c→X_c X_t、Z_t→Z_c Z_t、X_t→X_t、Z_c→Z_c，按 LightStim 的 patch 逻辑算子和测量记录写出 flow 字符串），并验证其电路级距离等于 d（d=3、5）。
4. 用 `verify_stack.platform_report` 给上述电路出平台分析，写进 LEDGER 的平台列。
5. 把以上全部结果登记进 `LEDGER.md`（类别写"校准"），每行附证据路径。把所有脚本放在 `agent_for_qec/phase0/`。
6. 更新本文件：Phase 0 移到"已完成"；在"下一阶段"写出 Phase 1 的三个候选探索目标（每个写：目标码、目标逻辑操作、为什么文献里可能是空白、打算用什么实现方式、预计怎么验证），**不要开始做 Phase 1**。
7. 本地提交。
