# 安装到 AutoResearchClaw

前提：AutoResearchClaw 已按其 README 安装（`pip install -e .`），
`config.arc.yaml` 已配置好 LLM provider。

## 第 1 步：拷贝包

```bash
cp -r apep_optimizer /path/to/AutoResearchClaw/researchclaw/apep_optimizer
```

包内只使用相对导入与标准库，除 `cli.py` 在运行时惰性导入
`researchclaw.config` / `researchclaw.llm.client` 外没有其他依赖，
不会影响现有 2699 个测试。

## 第 2 步：在 CLI 注册 `optimize` 子命令

编辑 `researchclaw/cli.py`，在 `build_parser()` 中（任何
`sub.add_parser(...)` 附近）加入：

```python
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(...)
    sub = parser.add_subparsers(dest="command")

    # ... 现有子命令 ...

    # APEP-style paper optimization loop
    from researchclaw.apep_optimizer.cli import add_optimize_parser
    add_optimize_parser(sub)

    return parser
```

`add_optimize_parser` 自带 `set_defaults(func=cmd_optimize)`，与
cli.py 现有的 `args.func(args)` 分发方式兼容。

## 第 3 步：验证

```bash
# 离线单元测试（不需要 API key）
python3 -m pytest researchclaw/apep_optimizer/../../tests -q   # 或拷贝 tests/ 后运行

# 冒烟测试（需要 API key）
researchclaw optimize --paper any_paper.md --rounds 1
```

## 可选：接入流水线 Stage 18/19

若想让 23 阶段流水线的评审-修订阶段直接使用该循环，可在
`pipeline/stage_impls/_review_publish.py` 的 `_execute_paper_revision`
末尾（或替代 Stage 18+19）调用：

```python
from researchclaw.apep_optimizer import PaperOptimizer, OptimizerConfig

report = PaperOptimizer(llm, OptimizerConfig(
    max_rounds=3,
    experiment_evidence=raw_metrics_revision,  # 复用 Stage 19 已收集的真实指标
)).optimize(draft_path, run_dir / "apep_versions", family_id=run_id)
(stage_dir / "paper_revised.md").write_text(
    report.final_paper_path.read_text(encoding="utf-8"), encoding="utf-8")
```

这样 Stage 20 质量门拿到的就是经过多轮多模型评审打磨后的版本，
且 `run_dir/apep_versions/` 中保留了与 ape-papers 档案同构的全部审计工件。

## 配置异构评审面板（推荐）

APEP 的实践是用不同供应商的模型互审以减少单模型偏见。CLI 方式：

```bash
researchclaw optimize --paper paper.tex \
  --reviewer gpt=gpt-5-mini \
  --reviewer grok=grok-4.1-fast \
  --reviewer gemini=gemini-3-flash-preview \
  --advisor  code=gpt-5.1-codex-mini \
  --rounds 4
```

注意：模型 ID 必须是你在 `config.arc.yaml` 中所配 provider（或其网关，
如 OpenRouter）可解析的名称；留空（`--reviewer gpt=`）则走默认模型链。
