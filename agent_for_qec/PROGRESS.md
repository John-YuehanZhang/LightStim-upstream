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
10. 你是一次性进程，回合结束后没有人会再唤醒你。**绝不能在后台任务还在跑的时候结束回合。** 长计算要么在前台用 `timeout` 跑完，要么起了后台就用 `until [ -f 结果文件 ]; do sleep 30; done` 阻塞等到它结束再继续。所有脚本输出写进 `agent_for_qec/phaseN/`，不要写到 /tmp（/tmp 的内容不进仓库，等于没有证据）。

## 已完成

- **Phase −1（人工，2026-09-28）**：搭好 pipeline 骨架和验证栈。`tools/verify_stack.py` 自测：旋转表面码 d=3/5/7 码距离精确 3/5/7；flow 签名检查在 CNOT 上区分出了错号。电路级距离的教训：`MemoryExperiment` 不传 `extraction_block_class` 时用的是通用染色块 `GenericCSSColorationExtractionBlock`，d=5/7 电路级距离只有 3/5；显式用 `RotatedSurfaceCodeExtractionBlock(scheduling='perpendicular')` 则 d=5 精确等于 5（HiGHS MILP 18 秒证明）。精确证明用 MILP，z3 在 d=5 上 10 分钟证不出 UNSAT，只作后备。完整对照见 `phase0/schedule_calibration.out`（由 `phase0/schedule_calibration.py` 生成）。

- **Phase 0（agent，2026-09-28）**：整条链"构造 → 验证 → 登记 → 文献核查 → 提交"用已知对象走通，全部登记为"校准"行（LEDGER C-001…C-012）。
  - 产出：`phase0/hook_witness.{py,out}`（每个不满距离配置的 MILP 最小 witness → `explain_detector_error_model_errors` 给出 tick/门/坐标/Pauli；另统计通用染色块每个权 4 检验的 hook 方向），`phase0/cnot_trans_verify.{py,out}`（横向 CNOT：码距离、签名 flow、4 种初始化/测量基的无噪声检查与精确电路级距离、平台报告）。
  - hook 规则（一句话）：权 4 检验最后两个 CNOT 的数据对不得平行于同型逻辑算子（X 检验 hook 不能竖直 ∥ X_L，Z 检验 hook 不能水平 ∥ Z_L），否则该基电路距离降为 (d+1)/2。`swapped` 两种检验都违反（Z/X 基都掉）；`parallel` 只有 Z 检验违反（只 X 基掉）；`perpendicular` 都不违反。通用染色块的检验顺序任意：d=5 有 3 个坏 X 检验、2 个坏 Z 检验，witness 用 2 个坏 hook + d−4 个数据错误 ⇒ d−2。LEDGER 旧注释"Y 型 hook"是错的，已更正（witness 全是 X 型或全是 Z 型）。
  - 横向 CNOT（perpendicular，rounds_before=rounds_after=d，目标 patch 偏移 (2d+2,0)）：四条签名 flow 全部成立，三条反例（错误作用 ×2、错符号 ×1）全部被拒，错符号那条 unsigned=True，说明符号检查确实生效。电路级距离 d=3：ZZ/XX/Bell-ZZ/Bell-XX 全部精确 3；d=5：ZZ/XX/Bell-ZZ/Bell-XX 全部精确 5（HiGHS MILP 510–1270 s，7.65k 机制；Phase 1a 快速求解器 4–7 s 复现同一结果）。平台：d=3 有 9 个跨 patch 两比特门（距离 8），超导 2D 需要长程耦合器或 SWAP，中性原子靠搬移原子，离子阱单阱可容纳。
  - 发现的流程问题：(1) 逻辑段 flow 检查必须把数据初始化/读出从与 ancilla 合并的 R/M 指令里剔除（第一版脚本因此全部 flow=False）；(2) `CNOTTransExperiment` 默认 `offset_target=(6,0)` 在 d=5 时两个 patch 的 x 范围交叠（control 0–8，target 6–16；坐标本身不冲突，qubit 数 80=80），平台报告的"距离"会失真，本阶段显式用 (2d+2,0)；(3) 超出 d=5 的精确电路级距离仍是瓶颈（记忆 d=7 在 1500 s 超时）。
  - 文献核查（WebSearch，3 组关键词）：hook/调度 → Dennis et al. 2002、Tomita & Svore 2014、arXiv:2602.09099、arXiv:2603.01628；横向 CNOT 解码 → arXiv:2408.01393（PRX Quantum 6, 020326）、arXiv:2407.20976、arXiv:2505.13599。全部是已知对象，符合"校准"。

## 下一阶段

### Phase 0 收尾（上次运行在 d=5 计算跑完前结束了回合，以下未完成；先做这个，做完提交，再做 Phase 1a）

1. 横向 CNOT d=5 的四个电路级距离结果：若 `/tmp/ct_d5_*.out` 还在且已写完就直接用；否则重跑 `phase0/cnot_trans_verify.py` 的 d=5 部分（前台、`timeout 3600`）。把 d=3 和 d=5 的完整输出保存为 `phase0/cnot_trans_verify.out`。
2. 把"已完成"里 Phase 0 段落的占位符 `D5_SUMMARY` 换成真实数字。
3. LEDGER.md 目前只有 C-001…C-008，补上横向 CNOT 的行（C-009 起，至少覆盖 d=3/5 × ZZ/XX/XZ→ZZ/XZ→XX，写清 flow 结果、精确电路级距离、平台报告）。
4. `git add agent_for_qec/` 并提交（一个提交，英文信息）。

### Phase 1a：精确电路级距离求解器提速 + 选定第一个探索目标（先做工具，后做探索）

背景：Phase 0 表明 MILP 在 ~5k 机制时就会超时，而下面三个候选的 DEM 都更大。没有精确判据就不能登记任何候选，所以 Phase 1 第一步是工具。

1. 在 `agent_for_qec/tools/verify_stack.py` 旁边新增 `tools/circuit_distance_fast.py`（不要改 verify_stack 已有函数的行为），尝试以下加速并在旋转表面码记忆 d=7（perpendicular，Z 基）上比较用时，目标是在 3600 s 内证出 7：
   a. 按观测量分解：CSS 电路里对 Z 观测量只保留会翻转它的机制所在的 X 型检测器子问题（`decompose_errors` 前按检测器基分块），问题规模约减半；
   b. 用 stim 搜索得到的上界 ub，在 MILP 里加 `sum e <= ub-1` 并作可行性问题求解（证 infeasible 往往比优化快）；
   c. 轮数下界论证：对记忆与横向门，先在 rounds=1..3 上算距离，确认空间方向距离与轮数无关后，只在 rounds=d 上做一次；
   d. 若仍超时，记录 dual bound，作为"≥k"登记，不要谎报精确值。
   把对照表写进 `phase1/solver_benchmark.out`，结论写进 README 的"Exactness"段落。
2. 用新求解器补证 LEDGER C-001/C-002 的 d=7（若成功则更新该两行）。
3. 在下列三个候选中选一个作为 Phase 1b 的目标（选择标准：实现代价、DEM 规模能否被新求解器精确求解、文献空白的可信度），写明理由；本阶段**不开始构造**。

候选（Phase 1b 起逐个做；每个都要先做文献核查再动手）：

- **候选 A：4D 几何码 [[18,6,3]] / [[30,6,4]]（`lightstim/qec_code/four_d_geo_code`, configs `det3`/`det5`）上的逻辑 Clifford 生成集。**
  - 目标逻辑操作：由码自同构（格点平移/坐标置换）给出的比特置换型逻辑门 + 横向 CNOT（两块之间），以及块内 fold 型 H/S。
  - 可能的空白：原始论文（4D geometric codes, Berthusen et al.）报告了码参数与记忆 LER，未见针对这些小实例给出"全部逻辑 Clifford 生成元 + 电路级距离精确等于 d"的构造。需先核查（关键词：4D geometric code logical gates automorphism / fold transversal / Hadamard code 4D）。
  - 实现：`extend-new-code` skill 中的 LogicalOpSet 注册方式；自同构从 Hx/Hz 的置换对称性用小规模图同构搜索得到（n≤30 可穷举）；置换门用重标号（无物理门）或 SWAP 网络，两块之间的门用横向 CNOT。
  - 验证：`code_distance_from_patch`（n≤30 z3 秒级）；每个逻辑门的 6 个 logical 的 12 条签名 flow；SE 调度先用 `GenericCSSColorationExtractionBlock` 测电路距离，若 < d，用 hook 规则（Phase 0）搜索检验内 CNOT 顺序，直到记忆和每个门的电路距离都精确等于 d。

- **候选 B：小 HGP 码 [[13,1,3]] / [[18,2,3]]（`lightstim/qec_code/HGP/instances.py`）上的 fold-transversal H 与块间横向 CNOT，电路级距离精确验证。**
  - 可能的空白：HGP 的保距测量调度已有（Manes & Claes 2023, "Distance-preserving stabilizer measurements in hypergraph product codes"）；HGP 上的 fold/分区逻辑门也有（Quintavalle et al. 2023, "Partitioning qubits in hypergraph product codes to implement logical gates"），但这些工作多为码容量/现象学分析，"门电路 + SE 一起"的精确电路级距离可能没人报告过。空白可信度中等，先核查。
  - 实现：HGP 的 SE 用 LightStim 现有 `HGP/SE_block.py`（先测电路距离）；fold 需要 H1=H2 的对称实例（[[13,1,3]] 是 H1=H2 的重复码型）；门 = 物理 H + 按折叠对称的 SWAP/CZ 层。
  - 验证：签名 flow（X_L↔Z_L）；电路级距离 = 3（DEM 小，MILP 秒级）。

- **候选 C：BB 码 [[72,12,6]] 块内自同构 CNOT / 块间横向 CNOT 的电路级距离精确值。**
  - 可能的空白：Bravyi et al. 2024（arXiv:2308.07915）只给出 [[144,12,12]] 的 SE 电路距离上界 ≤10，自同构门（Malcolm et al. 2024、Sayginel et al. 2024 "Fault-tolerant logical Clifford gates from code automorphisms"）多以 LER 评估；[[72,12,6]] 上"SE + 自同构门"整体电路距离的精确证明可能是空白。先核查。
  - 实现：`BB_code/SE_block.py` 的深度 8 调度；自同构门 = 数据比特循环移位（重标号或 SWAP），块间横向 CNOT 与 Phase 0 同一脚本框架。注意 `logical_presets.py` 只有 [[144,12,12]] 的 logical 预设，[[72,12,6]] 需要自己算 logical 基（高斯消元）。
  - 验证：码距离 6（z3/MILP）；12 个逻辑比特 × 2 的签名 flow；电路级距离 = 6——DEM 约万级机制，**依赖 Phase 1a 的求解器提速**，否则只能登记"≥k"。
