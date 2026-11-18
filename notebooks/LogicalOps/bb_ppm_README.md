# Gross-code PPM：Notebook 展示与固定参数实验

本示例使用 gross code `[[144,12,12]]`，将默认目标 `X2 Z7 X12` 编译为 shift、基本测量和 Pauli frame 修正。Notebook 的前四个正式 cells 展示构造与搜索结果；Cell 5 是本次固定参数大样本实验的运行、续跑和结果展示入口。

- 交互入口：[bb_ppm.ipynb](bb_ppm.ipynb)
- 保存完整输出的副本：[bb_ppm.executed.ipynb](bb_ppm.executed.ipynb)
- 服务器执行入口：[run_bb_ppm.py](run_bb_ppm.py)
- 结果展示与保存输出刷新：[bb_ppm_large_sample.py](bb_ppm_large_sample.py)
- 本地任务读取与续跑接口：[bb_ppm_large_run.py](bb_ppm_large_run.py)
- 物理构造与编译接口：[BB PPM API](../../docs/api/bb_ppm.md)

两个 Notebook 保存相同的展示输出，可直接在支持 Notebook 的查看器中打开。**查看已保存的表格和图不需要服务器结果目录；执行或续跑本次实验需要原服务器上的完整运行文件。** Git 仓库不包含完整采样、解码检查点和冻结执行脚本，仅从 GitHub 新克隆仓库不能启动或恢复同一个实验。

## 1. 当前实验设置

| 设置 | 固定值 |
|---|---|
| 目标 PPM | `X2 Z7 X12` |
| 每次基本 LPU 测量的纠错轮数 | 10 |
| 噪声模型 | LightStim 原有 `circuit_level_with_idling` |
| 各噪声率 | `p_1q = p_2q = p_meas = p_reset = p_idle = p` |
| `p = 0.0001` | 100,000 shots |
| `p = 0.0005` | 10,000 shots（独立新增点） |
| `p = 0.001` | 10,000 shots |
| 解码并行上限 | 192 个 worker，每个 worker 1 个线程 |

本次使用已经冻结的 Relay-BP 参数，不再根据大样本结果调参：

```python
DECODER_PARAMS = {
    "alpha": 0.8, "gamma0": 0.5,
    "pre_iter": 320, "num_sets": 600, "set_max_iter": 60,
    "gamma_dist_interval": (-0.1, 0.5), "stop_nconv": 5,
    "stopping_criterion": "nconv", "logging": False, "seed": 0,
}
```

这些是针对当前完整 PPM 电路完成训练集搜索后选定的参数，不应称为论文原始参数。旧的论文参数基线、训练集和每点 50-shot 验证记录仍保存在各自目录，不并入本次大样本计数。

本次按上述固定样本数完成实验，不按错误数提前停止。采样按固定种子分批保存；各批次按原顺序合并后解码，不筛选、不去重、不根据结果重新采样。完成数不足目标数时，Notebook 显示运行进度；部分结果不能当作已完成的固定样本统计。

## 2. 下载分支并安装环境

以下命令适用于 Linux，建议使用 Python 3.11 或 3.12：

```bash
git clone --branch codex/bb-code-ppm --recurse-submodules \
  https://github.com/John-YuehanZhang/LightStim-upstream.git bb_code
cd bb_code
python3.11 -m venv venv
venv/bin/python -m pip install --upgrade pip
venv/bin/python -m pip install -e '.[plot]'
venv/bin/python -m pip install -r notebooks/LogicalOps/requirements-bb-ppm.txt
```

如果服务器提供 Python 3.12，将 `python3.11` 换成 `python3.12`。可选加速模块需要 C++ 编译工具；Relay-BP 若无匹配的 wheel，源码安装还可能需要 Rust/Cargo。依赖文件固定 `relay-bp[stim]==0.2.2`。不要从 Windows 复制虚拟环境到服务器。

已有仓库时，先保存自己的改动，再更新分支和子模块：

```bash
git fetch origin
git switch codex/bb-code-ppm
git pull --ff-only origin codex/bb-code-ppm
git submodule update --init --recursive
```

上述步骤足以安装报告生成所需环境。恢复本次大样本实验还需保留原 `bb_ppm_results` 中的电路、矩阵、参数选择记录、冻结执行工具、采样文件及检查点；缺少或哈希不匹配时，入口应拒绝继续，而不能用新的随机样本冒充原任务。

## 3. 只生成前四个报告

在仓库根目录运行：

```bash
venv/bin/python -u notebooks/LogicalOps/run_bb_ppm.py \
  --reports-only --threads 1 \
  --output-dir "$HOME/bb_ppm_results"
```

这一步执行配置和前四个正式 cells，跳过带 `bb-ppm-ler` 标签的最终 Cell 5，不启动完整 LER 实验。

| Cell | 输出 |
|---|---|
| 1 | 15 种基本 PPM 及 stabilizer 定义文件 |
| 2 | 36 种逻辑 shift 与 540 个扩展后的 native measurements |
| 3 | 245 个非平凡数据旋转轴及联合测量实现 |
| 4 | 双向搜索、选中路径和完整操作表 |

输出 Notebook 为 `~/bb_ppm_results/bb_ppm.reports.ipynb`。Cell 3 保留 `axis (A)` 和 `chosen_joint (Q₁A)` 表头；Cell 4 使用固定行高 PNG，分组高度等于行数乘以行高，GitHub 预览只整体缩放图片。打印路径使用相对路径。

## 4. 连接现有实验并展示结果

Notebook 配置 cell 使用：

```python
SHOTS_BY_P = {1e-4: 100000, 5e-4: 10000, 1e-3: 10000}
WORKER_PROCESSES = 192
THREADS_PER_WORKER = 1
RESUME_IF_STOPPED = True
```

`LARGE_SAMPLE_RUN` 由环境变量 `BB_PPM_LARGE_SAMPLE_RUN` 指定；未设置时，默认目录为：

```text
<仓库父目录>/bb_ppm_results/full_circuit_tuning/large_sample_runs/20261117_frozen_192
```

该目录保留已完成的 `p=0.0001` 和 `p=0.001` 原始结果。新增的 `p=0.0005` 单独保存在同级 `20261118_p0005_192` 目录，由 `MIDPOINT_SAMPLE_RUN` 配置；环境变量名为 `BB_PPM_MIDPOINT_SAMPLE_RUN`。两个 campaign 分别核验固定计划和样本，再在 Notebook 中按 p 排序展示；不重新采样或合并旧点的统计样本。

在持有原运行文件的服务器上，可直接打开 Notebook 执行，或使用现有服务器入口：

```bash
BB_PPM_LARGE_SAMPLE_RUN="$HOME/bb_ppm_results/full_circuit_tuning/large_sample_runs/20261117_frozen_192" \
venv/bin/python -u notebooks/LogicalOps/run_bb_ppm.py \
  --threads 1 \
  --output-dir "$HOME/bb_ppm_results"
```

这里的 `--threads 1` 设置 kernel 中的 Rayon 线程环境；**192 是解码 worker 总数，不是每个 worker 的线程数**。大样本执行器统一管理进程池和任务锁，不能把它改成 192 个 worker 各开 192 个线程。

Cell 5 会核对 Cell 4 的目标与编译计划、冻结参数、采样预算和现有任务身份。任务已经运行时，Notebook 连接现有任务并读取进度，不再创建第二个解码池，也不打断正在运行的任务。任务停止时，`RESUME_IF_STOPPED=True` 允许通过既有运行入口续跑同一个已批准任务；设为 `False` 可只读取已有状态与结果。

该接口不会为任意新输入自动建立大样本实验。如果修改 `ppm_sequence`、物理电路、噪声、参数或预算，需要建立并核验新的 campaign；不能继续展示旧目标的计数作为新目标的结果。

## 5. 检查点、自动保存与本地文件

现有任务由独立的持久进程执行，Notebook 作为操作和展示界面。关闭 Notebook 不会取消已经运行的任务。保存的采样批次、合并输入与逐 shot 解码检查点用于续跑：已完成且通过哈希核验的批次和结果被保留；尚未保存完整结果的解码 shot 可能需要重新计算，但不会因此生成一批替代物理样本。

`bb_ppm_large_sample.py` 的配套刷新流程从已保存检查点生成进度表和 LER 图，并更新 `bb_ppm.ipynb` 与 `bb_ppm.executed.ipynb` 中 Cell 5 的已保存输出。它保留前四个正式 cells 的表格和固定行高图片，不通过重跑完整 Notebook 刷新结果。刷新器应与当前 campaign 配套运行；仅下载一个 Notebook 文件不会启动后台刷新。

本地文件大致分布如下：

```text
bb_ppm_results/
  ler/                         原完整 PPM 电路及历史基线
  full_circuit_tuning/
    original_lightstim/        冻结参数、训练与验证记录
    decoder/                   冻结解码执行器
    large_sample_tools/        采样、合并及持久任务工具
    large_sample_runs/
      20261117_frozen_192/
        plan.json              固定预算、参数、来源哈希与执行顺序
        status.json            当前任务状态
        driver.log             持久任务日志
        p0001/                 100,000-shot 输入与结果
        p001/                  10,000-shot 输入与结果
      20261118_p0005_192/
        p0005/                 新增的 10,000-shot 输入与结果
        notebook_integration/  三点结果表与图的保存输出
```

这些运行文件位于 Git 仓库之外；移动实验时必须保留完整依赖与来源关系，不能只复制 Notebook 或某一张结果表。当前保存和刷新操作只发生在本地，不自动提交或上传 GitHub。

`run_bb_ppm.py --output-notebook PATH` 仍可指定服务器执行生成的 Notebook。它与配套刷新器更新的两个仓库内 Notebook 是不同的输出位置；不要把多个写入任务指向同一个文件。入口会使用当前 Python 创建临时 kernel，无需额外注册 Jupyter kernel。

## 6. 如何解释结果

LER 的失败判据保持不变：decoder 对电路声明的 `OBSERVABLE_INCLUDE` 有任意一个预测错误，该 shot 计一次逻辑失败，LER 等于失败数除以实际完成的 shots。PPM 输出的 `+1` 或 `-1` 本身都不是失败标签。

逻辑失败和未收敛分别统计。未满足 syndrome 的纠正即使恰好猜对所有 observables，也不能称为有效纠正成功。最终统计需等待该噪声点的固定样本全部完成；零次失败也不能证明真实 LER 为零，应结合样本数与置信区间阅读。

三个噪声点全部完成后，图中按 p 从小到大连接观测 LER，保留每点的精确 95% 二项置信区间。连线仅辅助阅读，不是拟合曲线；零错误点仍显示为零并保留非零上界。

这里测量的是当前初始化、完整 `X2 Z7 X12` 操作序列与末态读出所检查的逻辑关联。原 LightStim 的物理调度、噪声实现和实验边界保持不变；它不是论文 Table 2 单条指令的 LER，也不声称精确复现论文结果。
