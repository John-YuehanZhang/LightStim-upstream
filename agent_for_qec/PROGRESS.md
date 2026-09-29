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


- **Phase 1a（agent，2026-09-29）**：精确电路级距离求解器提速 + 选定 Phase 1b 目标。
  - 产出：`tools/circuit_distance_fast.py`（新文件，未改 verify_stack），`phase1/solver_benchmark.{py,out}`，`phase1/candidate_probe.{py,out}`，`phase1/cnot_d7_bounds.py` + `phase1/cnot_d7_*.out`。
  - 方法：(a) 检测器子集松弛——只保留子集 S 的检测器约束（CSS 电路取"数据比特 X 错误能翻转的检测器"=Z 型半边），合并投影后相同的机制、丢弃投影为空的机制；每个全问题解都是松弛问题的解 ⇒ 松弛最优值是**证明过的下界**。投影后若全部机制 ≤2 个检测器，用奇偶提升图上的 BFS 精确求解（min_v dist((v,0),(v,1))，含边界点）；否则用 MILP。上界：把松弛最优解提升回原机制（每个合并类取 S 外检测器最少的代表），对完整 H、L 复核通过即为上界；LB=UB 即精确。下界按观测量分别取（每行取各子集的最大值，总下界取各行最小）。(b) 全问题 MILP 可行性"是否存在权 ≤ub−1"：d=7 记忆 1800 s 超时，**无效**。(c) 轮数扫描：d=7 在 rounds=1/2/3/7 上都是 7（只作旁证，不作证明）。(d) 超时一律登记区间 [lb, ub]。
  - 关键数字：旋转码记忆（perpendicular）d=5…15 两个基全部精确等于 d，d=7 用 0.2 s（旧 MILP 1500 s 超时），d=15（66k 机制）19 s；与 Phase 0 的 18 个已知精确值（含非 FT 的 2/3/(d+1)/2）零分歧；横向 CNOT d=5 四种组合精确 5，用 4–7 s（全 MILP 510–1270 s）；横向 CNOT d=7（23.8k 机制）：四种组合都只证到区间 [3, 7]（ub=7 为复核过的提升见证；每个 patch 单独投影后是图问题，但只对"不经过 CNOT 传播"的那个观测量给出 7，例如 ZZ 测量的 obs0；跨 patch 的观测量需要含超边（度 4）的投影 MILP，500 s 超时，对偶界 3）。stim 搜索在 d=7 上跑不完，已加开关 `use_stim_search=False`。
  - 独立审查（子 agent 逐条审数学正确性）：松弛下界、图 BFS、见证复核、可行性 MILP 均无问题；但发现 `verify_stack.min_weight_vector_milp` 在**超时**路径上可能高估下界（跳过的行/未知行被忽略，状态 3/4 被当成不可行）。快速工具因此自带 `_milp_min`（逐行严格计账）。verify_stack 未改（本阶段规则），README 已注明：该函数 status=timeout 时的 proven_lower_bound 不可用，status=exact 不受影响。
  - 局限：投影后仍有超边（BB 的权 6 检验：单个数据错误翻 3 个检验；横向 CNOT 时刻跨两个 patch 的错误）时退回 MILP，规模上去就超时。BB [[72,12,6]] 记忆 Z 基（16k 机制，12 个观测量）1800 s 内只得到 ub=6、lb 未证（X 基整体 3600 s 超时未跑到）。4D det5 记忆（5.9k 机制，投影超边度 6）精确 4 用 25 s。
  - 目标选择：**候选 A（4D 几何码 det3 [[18,6,3]] / det5 [[30,6,4]]）**。理由：(1) 可精确验证——记忆电路距离 det3=3（4 s）、det5=4（25 s），两个基都满距离，原生 SE 块 `FourDGeoCodeExtractionBlock` 即可用；(2) 候选 C 被求解器卡住（BB 记忆的下界都证不出，门电路只会更大）；(3) 候选 B 的 [[13,1,3]] 就是 d=3 非旋转表面码、[[18,2,3]] 是 d=3 环面码，它们的 fold-transversal H 早已有文献，空白可信度低。风险：arXiv:2506.15130（4D geometric codes 的容错量子计算机）声称给出了 4D 码"完整的逻辑 Clifford 操作集"，主要针对 [[96,6,8]] Hadamard 格点；小实例 det3/det5 上"每个生成元的电路级距离精确等于 d"是否已被报告，Phase 1b 第一步必须精读该文确认。
  - 文献（WebSearch）：arXiv:2506.15130（4D 几何码，含逻辑 Clifford 全集）；arXiv:2407.03973（BB 码的 logical 与 fold-transversal 门）；arXiv:2506.03094（Tour de gross，BB 码指令集含自同构）；Quintavalle et al., Quantum 7, 1153 (2023)（HGP 分区逻辑门）；arXiv:2603.22532（Webster, Jacob, Higgott：码与电路距离算法综述——我们的松弛下界属于其中"精确算法 + 界"的范畴，工具本身不作为新结果登记）。

## 下一阶段

### Phase 1b：4D 几何码 det3 [[18,6,3]] / det5 [[30,6,4]] 的逻辑 Clifford 生成集——先文献，后构造

1. 文献核查（先做，结论写进 PROGRESS/LEDGER）：精读 arXiv:2506.15130 的逻辑门部分（WebFetch 取 abs/HTML），回答：它给出的 Clifford 生成元是什么（自同构置换、fold-transversal H/S、块间横向 CNOT？）；是否覆盖 det3/det5；是否报告了电路级距离（精确值还是 LER）。再用至少 3 组关键词搜 "4D geometric code logical gate circuit distance"、"loop-only 4D toric code fold transversal"、"[[30,6,4]] code automorphism logical Clifford"。如果 det3/det5 上的生成元及精确电路级距离已发表，本候选降为"已有"类，直接转去候选 A'（同族 det9 [[54,6,6]]，先用 `candidate_probe.py` 测其记忆电路距离能否精确求解）或回到候选池。
2. 自同构：用 `FourDGeoCode(L=...)` 取 Hx/Hz，在 n=18/30 上穷举保持 (Hx,Hz) 行空间的数据比特置换（先用格点平移 Z^4/ΛZ^4 和坐标置换/符号翻转生成候选，再用 GF(2) 秩检验是否保码），计算每个自同构在 6 个逻辑比特上的辛作用（写成 12×12 GF(2) 矩阵），求它们生成的群。脚本 `phase1/four_d_automorphisms.py`，输出 `.out`。
3. 若有交换 X/Z 型的对偶（fold）：检查 Hx 与 Hz 在某个置换下互换，构造 fold-transversal H（物理 H + 置换）与 S 型（CZ/S 层），用 `logical_flow_string` 验证签名作用。
4. 块间横向 CNOT：沿用 `phase0/cnot_trans_verify.py` 的框架（`CNOTTransExperiment` 若不支持 4D 码，就用 builder-tracker API 手搭：两块 + 横向 CX 层 + 两侧各 d 轮 SE），flow + 电路级距离（`circuit_distance_fast`，按 patch 分的检测器子集放进 `extra_subsets`）。
5. 每个门：码距离、签名 flow（6 个 logical × X/Z，含反例对照）、无噪声检查、电路级距离（精确，超时写区间）、平台报告；每个通过验证的候选按规则 6 立即做文献核查后登记 LEDGER。
6. 本阶段不求做完所有门：优先"自同构群作用 + 一个具体门的完整验证链"，其余写进下一阶段。

### 候选池（未选）

- 候选 B（小 HGP [[13,1,3]]/[[18,2,3]] fold-H）：两者分别是 d=3 非旋转表面码与环面码，fold-transversal 门已有文献（Moussa 2016；Breuckmann & Burton 的 fold-transversal 系列；Quintavalle et al. 2023），空白可信度低；记忆电路距离均精确 3（<1 s），工具上随时可做。
- 候选 C（BB [[72,12,6]] 自同构/横向 CNOT）：被求解器卡住。前置工作：超边问题的精确求解（例如在奇偶提升图上做带耦合约束的流 MILP，或 MaxSAT），先在 BB 记忆 Z 基上证出 lb=6。相关文献：arXiv:2308.07915、arXiv:2407.03973、arXiv:2506.03094、Sayginel et al. 2024。
