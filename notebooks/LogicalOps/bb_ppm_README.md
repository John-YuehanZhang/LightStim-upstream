# Gross-code PPM：服务器运行说明

本示例使用 gross code `[[144,12,12]]`，将输入的 PPM 序列编译为 shift、基本测量和 Pauli frame 修正，再交给 LightStim 现有的采样、解码和绘图工具。默认目标是 `X2 Z7 X12`，每个噪声点先运行 **50 shots**。使用电路级 Pauli 噪声，不包含 atom loss。

- 交互展示：[bb_ppm.ipynb](bb_ppm.ipynb)
- 服务器入口：[run_bb_ppm.py](run_bb_ppm.py)
- 详细接口与物理构造说明：[BB PPM API](../../docs/api/bb_ppm.md)

服务器入口直接执行上述 notebook 的 cells；没有另写一套 LER 采样或失败判据。Cell 5 使用 Cell 4 生成的同一组 `plans`。

## 1. 下载分支并安装

以下命令在 Linux 服务器上执行。建议使用 Python 3.11 或 3.12，并始终使用本项目的虚拟环境。

```bash
git clone --branch codex/bb-code-ppm --recurse-submodules \
  https://github.com/John-YuehanZhang/LightStim-upstream.git bb_code
cd bb_code
python3.11 -m venv venv
venv/bin/python -m pip install --upgrade pip
venv/bin/python -m pip install -e '.[plot]'
venv/bin/python -m pip install -r notebooks/LogicalOps/requirements-bb-ppm.txt
```

如果服务器提供的是 Python 3.12，将 `python3.11` 换成 `python3.12`。安装会使用 C++ 编译工具构建可选加速模块；服务器需要可用的 C++ 编译器。若 Relay-BP 没有匹配该平台的预编译 wheel，pip 会尝试源码构建，届时还需要 Rust/Cargo 工具链。以安装报错为准补齐构建依赖。

已有这个仓库时，先保存自己的改动，再切换和更新对应分支：

```bash
git fetch origin
git switch codex/bb-code-ppm
git pull --ff-only origin codex/bb-code-ppm
git submodule update --init --recursive
```

不要直接复制 Windows 的 `venv` 到服务器；在服务器上重新安装。上述专用依赖文件固定了 `relay-bp[stim]==0.2.2`，无需安装所有可选 decoder 或 GPU 依赖。

## 2. 先生成报告，检查环境

在仓库根目录执行：

```bash
venv/bin/python -u notebooks/LogicalOps/run_bb_ppm.py \
  --reports-only --threads 8 \
  --output-dir "$HOME/bb_ppm_results"
```

这一步只执行配置和前四个正式 cells，不构建完整 LER 物理电路，也不开始噪声采样。输出包含：

| Cell | 输出 |
|---|---|
| 1 | 15 种基本 PPM，以及各自的 stabilizer 定义文件 |
| 2 | 36 种逻辑 shift、540 个 shift 扩展后的 native measurements |
| 3 | 245 个可实现的非平凡数据旋转轴，以及联合测量实现 |
| 4 | 目标 PPM 的双向搜索记录、选中路径、按阶段标注的完整操作表 |

生成的报告 notebook 是 `~/bb_ppm_results/bb_ppm.reports.ipynb`。可以下载到本地用 VS Code/Jupyter 查看，无需重新运行 cells。

`--threads 8` 限制 Relay-BP 的内部线程数。请改成服务器或作业调度器实际分配的 CPU 数；这不改变论文的 decoder 数学参数。LightStim 的 `num_workers` 仍为 1，由 Relay-BP 内部并行处理。入口会自动用执行命令的 Python 创建临时 kernel，无需额外注册 Jupyter kernel。

## 3. 在服务器运行 LER

环境检查通过后，去掉 `--reports-only`：

```bash
venv/bin/python -u notebooks/LogicalOps/run_bb_ppm.py \
  --threads 8 \
  --output-dir "$HOME/bb_ppm_results"
```

默认依次运行 `p = 0.0001, 0.001, 0.002, 0.003`，每点 50 shots。完成后输出 `~/bb_ppm_results/bb_ppm.executed.ipynb`，包含全部报告和 LER 图。

长时间运行建议在 `tmux` 中执行上述命令，或使用下面的后台方式；二选一即可：

```bash
mkdir -p "$HOME/bb_ppm_results"
nohup venv/bin/python -u notebooks/LogicalOps/run_bb_ppm.py \
  --threads 8 \
  --output-dir "$HOME/bb_ppm_results" \
  > "$HOME/bb_ppm_results/run.log" 2>&1 &
echo $! > "$HOME/bb_ppm_results/runner.pid"
tail -f "$HOME/bb_ppm_results/run.log"
```

不要同时启动多个写入同一输出目录的任务。`tail -f` 中按 Ctrl+C 只停止查看日志；在前台运行入口时按 Ctrl+C 则中断该运行。入口会在正常异常或中断时保存已经完成的 notebook 输出；强制杀死进程无法保证保存正在执行的 cell 输出。

论文参数的解码计算量较大，即使只有 50 shots，也可能运行数小时。进度通常在整批解码返回后更新；日志暂时不变不代表任务停止。服务器更快与否取决于 CPU、内存和可分配资源，这个入口不会自动改用 GPU。

## 4. 输入和参数在哪里修改

编辑源文件 `notebooks/LogicalOps/bb_ppm.ipynb`，然后重新运行入口。不要修改生成的 executed notebook 来控制下次任务。

**Cell 4 的 `ppm_sequence` 是测量序列输入**，逻辑编号从 1 开始。默认示例：

```python
ppm_sequence = [BBPPMStep(((2, "X"), (7, "Z"), (12, "X")))]
```

这表示一次 `X2 Z7 X12` 联合测量。多个元素表示按顺序执行多个 PPM。例如：

```python
ppm_sequence = [
    BBPPMStep(((2, "X"), (8, "X"), (9, "X"))),
    BBPPMStep(((2, "Z"),)),
]
```

logical qubit 1 是旋转 gadget 的辅助 pivot；一般数据目标使用 logical qubits 2–12。

最前面的**配置 cell**控制噪声点、采样预算和 syndrome-extraction 轮数：

```python
ROUNDS = 10
P_VALUES = (1e-4, 1e-3, 2e-3, 3e-3)
MAX_SHOTS, MAX_ERRORS, BATCH_SIZE = 50, 50, 50
```

当前两个停止上限都是 50，因此每点按 50 shots 运行。若增加采样量，也要检查 `MAX_ERRORS`：达到错误数上限同样会停止。`ROUNDS` 是每次基本 LPU 测量的纠错轮数，不是完整序列只测量十次。改变这些参数会改变实验或统计预算。

保留以下 Relay-BP 配置即可使用当前对齐的论文参数：

```python
DECODER_PARAMS = {
    "alpha": 1.0, "gamma0": 0.125,
    "pre_iter": 80, "num_sets": 600, "set_max_iter": 60,
    "gamma_dist_interval": (-0.24, 0.66), "stop_nconv": 5,
    "stopping_criterion": "nconv", "parallel": True,
}
```

初始 leg 最多 80 次迭代，最多再执行 600 个、每个最多 60 次迭代的 leg，并按 5 个收敛解的条件提前停止。参数来源与 memory 约定见 [Tour de gross Table 7](https://arxiv.org/html/2506.03094v1#A7.T7) 和 [Relay-BP Section IV / Figure 2](https://arxiv.org/html/2506.01779v1#S4)。

入口的完整参数可用以下命令查看：

```bash
venv/bin/python notebooks/LogicalOps/run_bb_ppm.py --help
```

`--output-notebook PATH` 可指定执行后 notebook 的位置。未给 `--output-dir` 时，默认输出目录是仓库中的 `notebooks/LogicalOps/assets/bb_ppm/`。

## 5. 查看结果与继续运行

使用上面的输出目录时，文件结构为：

```text
~/bb_ppm_results/
  basic_measurements/       15 种基本测量的 stabilizer 定义
  shifts/                   shift 列表
  native_measurements/      shift 扩展后的基本测量
  rotation_axes/            可实现旋转轴及其实现
  searches/                 输入 PPM 的路径与操作序列
  ler/
    <run_id>/
      configuration.json    输入、decoder、噪声、预算及哈希
      resources.json        电路规模、构建时间和运行环境
      clean.stim            无噪物理电路
      noisy_p_*.stim        各噪声点的物理电路
      progress.log          采样/解码进度
      results.csv           每个已完成噪声点的结果
      ler.png               全部噪声点完成后的图
    latest.json             最近一次完整运行的位置
  bb_ppm.executed.ipynb      完整运行生成的 notebook
```

`run_id` 来自输入、编译计划、物理电路、配置、相关源码和软件版本的指纹。每完成一个噪声点，代码就保存一行结果。用**相同配置和相同输出目录**重新运行，会重建并核验电路、跳过完全匹配且已保存的噪声点，然后继续剩余点。尚未完成的 50-shot batch 没有可续接的 decoder 状态，需要重新计算该点。

更改目标、噪声点列表、预算、相关源码或记录的软件版本后，会创建新的结果目录。新旧配置的结果不会自动混合。默认生成的 assets 属于可再生成文件，不随 Git 上传；服务器首次运行会重新生成它们。

本地暂停前已保存的首批数据是 `p=0.001`、`12/50` 次失败、LER `0.24`；这些原始记录继续保存在本地。服务器从新安装环境开始时不会自动导入这条结果，也不应把未完成的另两点当作零失败。

## 6. 这张 LER 图的含义

失败判据沿用 LightStim：decoder 对电路自动生成的 `OBSERVABLE_INCLUDE` 预测有任意一个错误，该 shot 就计作一次失败。这里没有 postselection，LER 为失败次数除以 shots。PPM 原始输出的 `+1` 或 `-1` 本身都不是失败标签。

报告的是当前初始化、完整 PPM 序列和末态读出能够检查的逻辑关联的错误率。它不是每个基本测量的 LER，也不单独验证随机 PPM 输出位。decoder 参数已经对齐；物理调度、内置噪声模型和完整实验边界仍是本实现的基线，不宣称精确复现论文 Table 2。

50 shots 的统计误差较大。图中误差棒复用 LightStim 的 95% 正态近似；零错误或全失败在小样本下仍存在不确定性。完整结果表保留真实计数，之后可以增加预算再做精确比较。
