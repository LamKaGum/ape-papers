# APEP Optimizer — 为 AutoResearchClaw 移植 ape-papers 的论文自动优化能力

本目录交付一个**可直接放入 AutoResearchClaw 的 Python 模块**（`apep_optimizer/`），
把 [ape-papers](https://github.com/LamKaGum/ape-papers)（APEP，Autonomous Policy
Evaluation）项目中经过实战验证的「论文自动优化循环」移植到
[AutoResearchClaw](https://github.com/LamKaGum/AutoResearchClaw)。

> 说明：本模块放在 ape-papers 仓库中交付，是因为当前代理只对本仓库有写权限。
> 安装方式见下文与 [INSTALL.md](INSTALL.md)，只需拷贝一个目录 + 在 CLI 注册两行。

---

## 一、两个仓库的分析

### 1. ape-papers（APEP）：论文优化能力从何而来

ape-papers 是 APEP 系统产出的全部工作论文档案（1100+ 版本、900+ 论文家族）。
从档案工件可以完整还原它的优化机制——以 `apep_0038`（v1→v4，评审决定从
MAJOR REVISION 一路优化到 3 票 MINOR REVISION、advisor 3/4 PASS）为例，
每个版本目录包含：

| 工件 | 作用 |
|---|---|
| `review_gpt_1.md` / `review_grok_1.md` / `review_gemini_1.md` / `review_cc_*.md` | **多模型并行外审**（GPT-5-mini、Grok-4.1-Fast、Gemini-3-Flash、Claude Code），每份评审以固定清单展开（格式检查 → 统计方法 → 识别策略 → 文献 → 写作 → 建设性意见），末尾给出五档决定 |
| `parallel_review_summary.json` | 并行评审汇总：模型、路由、成功/失败数、每位审稿人的决定 |
| `advisor_gpt_1.md` 等 + `advisor_summary.json` | **导师预检门**：多模型快速 PASS/FAIL 致命缺陷扫描（如伪造数据、数字不一致），`overall_pass` 决定是否值得进入完整外审 |
| `revision_plan.md` | **修订计划**：编辑视角把多份评审意见合并成按优先级排序的 Workstreams（CRITICAL/HIGH/…），无法满足的要求明确写"诚实的局限性段落"而不是编造数据 |
| `reply_to_reviewers_1.md` | **逐条回复审稿人**：引用每条意见 + 说明改了什么/为何不改 |
| `final_review.md` | 本轮最终编辑决定 |
| 根目录 `manifest.json`（schema v3） | **版本谱系**：`paper_family_id`、`parent_paper_id`、`is_revision`、`version`、五档决定（ACCEPT / CONDITIONALLY ACCEPT / MINOR REVISION / MAJOR REVISION / REJECT AND RESUBMIT） |

也就是说，APEP 的"自动优化论文"= **多模型异构评审 → 编辑聚合决定 →
修订计划 → 自动改稿 → 回复审稿人 → 生成新版本(vN+1) → 再评审**，
循环直到接收或预算耗尽，全过程留下可审计的版本谱系。
manifest 统计显示该机制被大量使用：174 个版本是修订版（`is_revision: true`）。

### 2. AutoResearchClaw：现状与差距

AutoResearchClaw 是 23 阶段的自主科研流水线。与论文打磨相关的是：

- **Stage 18 `PEER_REVIEW`**（`pipeline/stage_impls/_review_publish.py` 的
  `_execute_peer_review`）：**单次 LLM 调用**产出 `reviews.md`，无多模型、无结构化决定；
- **Stage 19 `PAPER_REVISION`**（`_execute_paper_revision`）：**单次改稿**，
  已有防伪造数据护栏和长度护栏，但**没有循环**——改完直接进 Stage 20；
- **Stage 20 `QUALITY_GATE`**：质量门失败会回滚到 Stage 16 重写，
  但这是"推倒重来"，不是"针对评审意见的定向迭代"。

| 能力 | ape-papers (APEP) | AutoResearchClaw 现状 |
|---|---|---|
| 评审模型 | 3-4 个异构模型并行 | 1 个（主模型链） |
| 评审决定 | 五档结构化决定 + 聚合 | 无结构化决定 |
| 导师预检门 | PASS/FAIL 快速致命缺陷扫描 | 无 |
| 修订方式 | 修订计划 → 定向改稿 → 回复审稿人 | 直接一次改稿 |
| 迭代 | 循环至接收/预算耗尽 | 单轮 |
| 版本谱系 | v1→vN + manifest 血缘 | 无版本概念 |

## 二、本模块的设计

`apep_optimizer/` 完整复刻上述循环，工件命名与 APEP 档案一一对应：

```
PaperOptimizer.optimize(paper, family_dir)
│
├─ v1/  ← 输入论文（.md 或 .tex）
│   ├─ advisor_<name>_1.md + advisor_summary.json     (可选预检门)
│   ├─ review_<name>_1.md × N  (并行, ThreadPoolExecutor)
│   ├─ parallel_review_summary.json
│   └─ final_review.md         (聚合决定: majority / worst)
│
├─ 决定 ∈ accept_decisions ? → 结束(接收)
│   否则（且还有轮次预算）:
│
├─ v2/
│   ├─ revision_plan.md        (编辑合并意见 → Workstreams)
│   ├─ paper.md                (完整改稿, 带长度护栏+防伪造护栏)
│   ├─ reply_to_reviewers_1.md
│   └─ ...再评审...
│
├─ manifest.json               (APEP schema v3: parent_paper_id / is_revision / version)
└─ optimization_summary.json   (整个循环的机器可读报告)
```

关键设计决策：

- **只依赖一个极小的 LLM 协议**：任何带
  `chat(messages, *, model, max_tokens, temperature, system) -> .content`
  的对象即可——`researchclaw.llm.client.LLMClient` 原生满足，测试用 FakeLLM 注入。
  因此模块可独立于流水线运行，也可嵌入 Stage 18/19。
- **异构评审面板**：`OptimizerConfig.reviewer_models` 把审稿人名映射到模型 ID
  （对应 APEP 的 gpt/grok/gemini 路由）；单个审稿人失败不会终止本轮，
  失败/无法解析决定的评审按 MAJOR REVISION 计（沉默失败绝不放行论文）。
- **决定聚合**：`majority`（平票取更差档）或 `worst`；advisor 门 FAIL 时
  即使评审通过也强制降级为 MAJOR REVISION（致命缺陷一票否决）。
- **沿用 AutoResearchClaw Stage 19 的两个护栏**：
  改稿短于原文 80% 时重试一次，仍短则保留原文并把模型输出归档为内部笔记
  （绝不丢内容）；`experiment_evidence` 注入真实实验数据 + 禁止编造数字的指令。
- **幂等**：v1 已存在时不重复播种，可断点续跑。

## 三、安装与使用

### 安装（两步）

```bash
# 1. 拷贝包
cp -r apep_optimizer  /path/to/AutoResearchClaw/researchclaw/apep_optimizer

# 2. 在 researchclaw/cli.py 的 build_parser() 里注册子命令（两行）
#    from researchclaw.apep_optimizer.cli import add_optimize_parser
#    add_optimize_parser(sub)
```

详细 diff 见 [INSTALL.md](INSTALL.md)。

### CLI 用法

```bash
# 优化一篇流水线产出的论文，最多 3 轮
researchclaw optimize --paper artifacts/rc-.../deliverables/paper_draft.md

# APEP 同款异构评审面板 + 导师预检门
researchclaw optimize --paper paper.tex \
    --reviewer gpt=gpt-5-mini --reviewer grok=grok-4.1-fast \
    --reviewer gemini=gemini-3-flash-preview \
    --advisor code=gpt-5.1-codex-mini \
    --rounds 4 --policy majority
```

退出码：0 = 接收，1 = 轮次预算耗尽仍未接收。

### 作为库使用（例如嵌入 Stage 18/19）

```python
from researchclaw.apep_optimizer import PaperOptimizer, OptimizerConfig
from researchclaw.llm.client import LLMClient

llm = LLMClient.from_rc_config(rc_config)
config = OptimizerConfig(
    reviewer_models={"gpt": "gpt-5-mini", "grok": "grok-4.1-fast", "gemini": ""},
    advisor_models={"code": ""},
    max_rounds=3,
    experiment_evidence=raw_metrics,   # Stage 14 的真实指标 → 防伪造护栏
)
report = PaperOptimizer(llm, config).optimize(paper_path, out_dir / "paper_family")
if report.accepted:
    final = report.final_paper_path
```

## 四、测试

离线测试（FakeLLM，不需要网络/密钥）：

```bash
cd integrations/autoresearchclaw-apep-optimizer
python3 -m pytest tests/ -v
```

覆盖：五档决定解析、PASS/FAIL 解析、majority/worst/平票/失败降级聚合、
多轮收敛、轮次预算耗尽、首轮直接接收、长度护栏保原文、
manifest 血缘字段与 APEP schema v3 对齐、汇总 JSON 有效性。

## 五、目录结构

```
apep_optimizer/
├── __init__.py       # 公共 API
├── models.py         # Decision 五档枚举、解析器、OptimizerConfig、manifest 血缘
├── prompts_apep.py   # 依 APEP 实际工件建模的评审/预检/修订计划/改稿/回复提示词
├── reviewers.py      # 多模型并行外审 + 导师预检门（含 APEP 同款 summary JSON）
├── editor.py         # 决定聚合（majority/worst）+ final_review.md
├── revision.py       # 修订计划 → 改稿（长度/防伪造护栏）→ 回复审稿人
├── loop.py           # PaperOptimizer 循环编排 + 版本目录 + manifest + 总报告
└── cli.py            # `researchclaw optimize` 子命令
tests/
└── test_apep_optimizer.py
```
