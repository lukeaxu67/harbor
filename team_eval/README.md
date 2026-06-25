# team_eval — Claude Code FleetView Team 轨迹：图化 / ATIF / 可视化 / 评测

把一个 Claude Code **Team 模式**会话（主会话 jsonl + 队友 subagents/）变成：

1. **执行图** —— 识别 pipeline / map-reduce / supervisor 等协作模式
2. **ATIF 轨迹** —— 尽量贴合 harbor 的 [ATIF v1.7](../rfcs/0001-trajectory-format.md) 协议（leader 为 root，队友内嵌进 `subagent_trajectories[]`），并用 harbor 的 Pydantic 模型校验
3. **自包含交互 HTML** —— 拓扑图 + **泳道时序图** + 执行时间线 + 步骤下钻 + 评测面板，并把评测结论叠加到节点/步骤
4. **评测体系（v0.2 深化）** —— **7 个通用考察角度**（结论导向）+ **按角色/按任务**评测 + 纠错检测 + **cc-sdk 评委**做主观判定
5. **本次评测报告** —— 对真实会话跑全套并出报告
6. **采集 harness** —— 一键触发更多 `claude` team 会话、累积数据集、迭代指标

> 独立子项目，不修改 harbor 主仓；ATIF 模型仅 import 复用（`harbor.models.trajectories`）。
> v0.2 设计受 `astroneval`（cc-sdk + MCP 工具强制结构化判定）与 `session-eval`（可插拔 metric + DimensionScore）启发，详见 `reflections.md`。

## v0.2 评测深化（回应"该从什么角度评 team 任务好坏"）

**结论导向的 7 个通用角度**（报告开头直接回答）：

| 角度 | 回答 | 客观来源 | 主观(cc-sdk 评委) |
|---|---|---|---|
| goal 任务完成度 | 最开始的任务完成了吗？ | 任务状态/覆盖/交付 | 综合质量(O3) |
| planning 规划拆解(领导力) | 拆解合理吗？聚焦吗？ | 角色均衡/覆盖 | 规划质量(C5) |
| delegation 委派协调 | 派对人了吗？交接干净吗？ | 闭环率/孤儿/消息错误 | — |
| execution 角色执行 | 谁强谁弱？为什么？ | 每 role 错误/完成/自愈 | 每角色深度(O4) |
| robustness 韧性纠错 | 出错了吗？纠正了吗？ | 错误率/自愈/干预 | — |
| efficiency 效率 | 值这个成本吗？ | token/并行/churn | — |
| conformance 合规 | 轨迹可复用吗？ | ATIF 校验 | — |

**按角色按任务评测**：每份 transcript = 一次角色执行（churn 即多次尝试），各自判
完成/错误/**自愈**(出错工具后续是否成功)；每任务带 keypoint 判据(assigned/executed/
closed/quality)。这让"活干了没"与"是否跟踪到完成"可分开判定。


---

## 安装

```bash
conda create -n harbor-team-eval python=3.12 -y
conda activate harbor-team-eval
pip install pydantic jinja2
# 注册 harbor 元数据（--no-deps，不拉重依赖；ATIF 模型仅依赖 pydantic）
pip install -e D:/github/harbor --no-deps
```

## 快速开始

```bash
# 处理一个会话，产出 runs/<session_id>/{atif,graph,eval}.json + report.html
PYTHONPATH=D:/github/harbor/team_eval python -m team_eval.cli \
  "C:/Users/<you>/.claude/projects/<proj>/<session>.jsonl" --out runs

# 同时登记进数据集
python -m team_eval.cli <session>.jsonl --out runs --sessions sessions --register

# 只看记分卡，不写文件
python -m team_eval.cli <session>.jsonl --checks-only

# 用 cc-sdk 评委跑主观项（综合质量/规划/每角色深度；约 7 次调用 ≈$1.7）
python -m team_eval.cli <session>.jsonl --out runs --judge --judge-cwd D:/github/harbor

# 评委通过 claude-agent-sdk 起独立 claude 会话。为复用你本机 Claude Code 的供应商
# 配置（自定义 ANTHROPIC_BASE_URL / token / 模型，如 BigModel、代理等），评委会自动
# 读取 ~/.claude/settings.json 的 env 块注入到 SDK 子进程；否则 SDK 会落到真正的
# api.anthropic.com 而反复 api_retry。可用环境变量 TEAM_EVAL_CLAUDE_SETTINGS 指向别的
# settings.json。仅 Read + submit_verdict 两个工具，只读不改动仓库。
```


双击打开 `runs/<session_id>/report.html`（拓扑/时间线/步骤/评测四个 tab，全部交互）。

## 采集更多会话（任务 6，需你显式触发）

```bash
# 触发一个 headless team 运行并自动跑完整 pipeline + 登记数据集
PYTHONPATH=D:/github/harbor/team_eval python -m team_eval.harness.run_claude \
  --prompt "使用 Team 模式完成一个调研项目……" --workdir D:/some/repo
# 或：--prompt-file prompt.txt   --model <model>   --timeout 1800
```

> 会消耗 token、需要 claude CLI 与 API key。默认 **不自动触发**。

---

## 架构

```
parse/   session_loader → 中间模型 TeamSession（镜像 harbor claude_code.py 的归一化思路，
                          但扩展支持 subagents/ 内嵌与团队语义）
         event_normalize（uuid 去重 / timestamp 排序 / message.id 聚合 agent_step / usage→metrics）
graph/   build_graph（节点=leader+角色；边=spawn/message/task_assign；孤儿检测）
         pattern_detect（mapreduce / pipeline / supervisor / custom + reduce_quality）
atif/    convert（root Trajectory + 内嵌 subagent_trajectories[] + SubagentTrajectoryRef 委派边）
         validate（Trajectory.model_validate 往返 + 一致性自检）
eval/    checks（25 个检查点目录 + 客观评估器）/ stats / annotator / llm_judge(默认关)
viz/     report_html（vis-network CDN 力导图 + 原生 JS 时间线/步骤/评测）
harness/ run_claude（采集）/ pipeline（一条龙）/ dataset（累积索引 + 跨会话汇总）
cli.py   python -m team_eval.cli
```

数据流：`parse → graph + atif → eval(含 graph/atif 信号) → viz`。

## 评测维度与意义

| 维度 | 关注点 | 失败意味着 |
|---|---|---|
| **structural** | 团队是否正确成形（创建/角色齐全） | 编排本身坏了 |
| **execution** | 每个 agent 是否健康完成（产出/无超步/无错误） | 单点可靠性差 |
| **coordination** | 委派纪律与协调（任务闭环/消息完整/收尾干净） | 多智能体协作质量差 |
| **outcome** | 是否产出合格交付物（综合存在/覆盖/质量） | 最终无效用 |
| **efficiency** | 成本与并行度（token/并行率/churn） | 资源浪费 |
| **robustness** | 异常与韧性（错误率/重试 churn/干净终止） | 不稳定 |
| **atif-conformance** | 转换是否真符合协议 | 不可复用/不可训练 |

- **客观检查**（`auto=True`）直接从解析数据判定；**主观检查**带 rubric，在 HTML 内人工填（或可选 LLM-judge）。
- 每个 checkpoint 带 **tags**（`task-lifecycle`/`churn`/`message-error`/`reduce`…）用于事后按类聚合。
- `health` = 客观检查的加权分（pass=1 / warn=0.5 / fail=0），主观 pending 不计入。

## 输出

```
runs/<session_id>/
  atif.json     完整 ATIF v1.7 轨迹（root + 内嵌 subagents）
  graph.json    执行图（节点/边/任务/模式）
  eval.json     EvalReport（检查点 + 统计 + 分数）
  report.html   自包含交互报告
sessions/index.json   采集数据集索引（跨会话）
annotations/<session>.json   主观标注（HTML 内填写，可导出）
reflections.md   指标设计迭代日志
```

## 已知限制 / 后续

- 采集 harness 的 `run_claude` 在 Windows 上对 `claude.cmd` 可能需调整 shell 调用；未对真实运行联调（按约定由用户触发）。
- 主观检查的 cc-sdk 评委默认关闭（`--judge` 显式触发，`eval/llm_judge.py`），需 `claude-agent-sdk` + Claude 认证 + 可达的 API 供应商；评委自动继承 `~/.claude/settings.json` 的 `env` 块（见上）。
- 跨会话指标迭代依赖更多样本——见 `reflections.md`。
