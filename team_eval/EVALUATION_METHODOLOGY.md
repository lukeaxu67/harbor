# team_eval 评测方法体系（Methodology）

> 一份把 team_eval 当前实现的评测体系**说清楚**的参考文档：评哪些维度、主客观各怎么做、
> 评分区间如何设计、检查点有哪些分类标签、记录/统计了哪些数据。所有阈值与字段名均取自
> 代码（`team_eval/eval/*.py`、`graph/pattern_detect.py`），可逐项回溯。
>
> 配套：`README.md`（用法）/ `reflections.md`（指标迭代日志）/ 本文（方法体系）。

---

## 0. 一句话定位

team_eval 评测的是 **Claude Code「Team 模式」多智能体团队的执行质量**。输入一个团队会话
（leader jsonl + 队友 subagents/），输出一份从**会话级 → 角色级 → 任务级 → 单次执行级**逐层
下沉、且**客观判定与主观判定分开呈现**的评测报告。

设计上刻意把评测拆成多个维度而非一个总分——**总分掩盖根因**：一个 health=72.7 的会话，
可能是"编排完美但综合质量差"，也可能是"编排稀烂但勉强出活"，修法完全不同。

---

## 1. 设计哲学与三层失败模式

多智能体 team 比 single-agent 难评，因为失败发生在三个不同层面：

| 失败层 | 涉及维度 | team 特有？ | 典型表现 |
|---|---|---|---|
| **编排层** | structural / coordination | ✅ team 特有 | 团队没成形、委派错乱、任务没闭环、收尾不干净 |
| **执行层** | execution / robustness | 与 single 重叠，但看"每个"角色 | 单个 agent 卡住、报错、retry churn、没产出 |
| **效用层** | outcome / efficiency | 最终用户在乎 | 交付物不合格、成本失控 |

由此衍生四条核心原则（见 `reflections.md` v0.1）：

1. **客观优先**：能从解析数据自动判定的，绝不留给主观（任务闭环、消息错误、churn、终止状态都是硬事实）。
2. **主观显式 pending**：质量/深度/规划合理性需要人或 LLM 判断的，标 `pending` 而非臆造分数——**不假装能测的东西其实测不了**。
3. **evidence 必须可定位**：每个失败点带 `agent_id`/`step_id`/`role`/`ref_kind`，能一键跳到出问题的那一步，否则评测不可审计。
4. **tag 用于事后归类**：单个会话看不出价值，攒会话后能问"哪类故障最频繁/最致命"，驱动指标演进。

---

## 2. 指标维度体系：7 个 dimension + 7 个 angle

体系里有**两套"七"**，容易混淆，必须分清：

### 2.1 dimension（检查点的归类，7 个）

每个检查点 `Check.dimension ∈ {structural, execution, coordination, outcome, efficiency, robustness, atif}`（`checks.py:DIMENSIONS`）。这是检查点的**静态分类**。

### 2.2 angle（结论导向的聚合，7 个）

`angle` 是评测要回答的**7 个通用问题**（`checks.py:ANGLES`）。每个 angle 聚合一组 dimension
的检查点，**取最坏结果**作为该 angle 的 verdict，并额外纳入 role/task 信号：

| angle | 回答的问题 | 映射的 dimension | 额外纳入的信号 |
|---|---|---|---|
| **goal** | 最开始的任务完成了吗？ | outcome | 各 task 的 `completion`；`tasks closed: x/n` |
| **planning** | 拆解合理吗？聚焦吗？角色边界清晰吗？（领导力） | structural | — |
| **delegation** | 活派对人了吗？交接干净吗？协调有效吗？ | coordination | — |
| **execution** | 每个角色完成得怎么样？谁强谁弱？ | execution | 各 role 的 `completion`+`error_profile`；`weak roles` |
| **robustness** | 中途出错了吗？发现并纠正了吗？（韧性/纠错） | robustness | `recovered executions` + `unrecovered` 计数 |
| **efficiency** | 值得这个成本吗？（token/时间/并行/冗余） | efficiency | — |
| **conformance** | 轨迹可复用/合规吗？（ATIF） | atif | — |

> **dimension vs angle 的区别**：dimension 是"这个检查属于哪一类"；angle 是"这组检查能支撑什么
> 结论"。angle 的 verdict = 其下所有相关检查（+role/task 信号）的**最坏**（`_worst_outcome`）。
> 报告开头直接回答这 7 个问题，而不是堆 25 个检查点。

---

## 3. 检查点目录（25 个，全表）

22 个客观 + 3 个主观（`C5`/`O3`/`O4`）。

| ID | dimension | 标题 | 主/客 | severity | tags | 实现要点（阈值） |
|---|---|---|---|---|---|---|
| **S1** | structural | Team created | 客观 | critical | team, leader | `TeamCreate≥1` 且有 team_name → pass |
| **S2** | structural | Every spawned role has a transcript | 客观 | major | spawn, churn | 所有 spawn 的角色都有 transcript |
| **S3** | structural | Valid role types | 客观 | minor | role | 角色名非空且 ≠ `unknown` |
| **E1** | execution | Teammates produced final text | 客观 | minor | output | 全员有 final text；否则 warn 并列缺谁 |
| **E2** | execution | No transcript step blow-up | 客观 | minor | steps | 最大步数 ≤`MAX_STEPS=200` → pass，否则 warn |
| **E3** | execution | Per-agent tool errors bounded | 客观 | major | error | 任一角色错误 >`ERR_THRESH=10` → fail |
| **E4** | execution | Leader final text present | 客观 | info | leader | leader 有 final text |
| **C1** | coordination | Task closure | 客观 | critical | task-lifecycle | 全部任务 `completed` → pass，否则 fail |
| **C2** | coordination | No orphan messages/spawns | 客观 | minor | message | 无孤儿消息/spawn 目标 |
| **C3** | coordination | No SendMessage errors | 客观 | major | message-error | 0 条 SendMessage 报错 → pass |
| **C4** | coordination | Clean team cleanup | 客观 | minor | shutdown | `TeamDelete` 恰好 1 次 → pass；0 或 >1 → warn |
| **C5** | coordination | Leader delegation discipline | **主观** | major | leader, delegation | rubric：leader 是否只编排不自干 |
| **O1** | outcome | Synthesis present | 客观 | major | reduce | reduce_quality：complete→pass/partial→warn/none→fail |
| **O2** | outcome | Domain coverage in synthesis | 客观 | major | reduce, coverage | 最长 leader 文本覆盖域关键词 5/5 |
| **O3** | outcome | Synthesis quality | **主观** | critical | reduce, quality | rubric：结构/引用/无幻觉/可落地 |
| **O4** | outcome | Research depth per domain | **主观** | major | depth | 按角色深度聚合（取最坏） |
| **F1** | efficiency | Token cost reported | 客观 | info | token | 仅信息性，恒 pass，报 token 总量 |
| **F2** | efficiency | Worker parallelism | 客观 | minor | parallel | worker 活动窗口有重叠 |
| **F3** | efficiency | Low transcript churn | 客观 | major | churn | 每角色 transcript 数 ≤2→pass/==3→warn/else fail |
| **R1** | robustness | Low error rate | 客观 | major | error | 错误率 <5%→pass/<15%→warn/else fail |
| **R2** | robustness | No manual intervention | 客观 | minor | intervention | 无 TaskStop 且 TeamDelete≤1 |
| **R3** | robustness | Clean termination | 客观 | critical | termination | 非 idle 收尾 ∧ 任务全完成 ∧ TeamDelete==1 |
| **A1** | atif | ATIF Pydantic validity | 客观 | info | atif | Trajectory 经 harbor 模型校验通过 |
| **A2** | atif | ATIF schema integrity | 客观 | info | atif | id 唯一/step 连续/source_call_id 完整 |
| **A3** | atif | ATIF refs resolvable | 客观 | info | atif | SubagentTrajectoryRef 都能解析到内嵌轨迹 |

**severity 分布**：critical×4（S1/C1/O3/R3）、major×9、minor×7、info×5。

---

## 4. 主观 vs 客观：分别怎么实现

### 4.1 客观检查（22 个）

每个客观检查在 `checks.py` 里是一个 `evaluate(ctx: EvalContext) -> CheckResult` 函数，返回：

```
(outcome: CheckOutcome, explanation: str, evidence: list[CheckEvidence], metric: dict)
```

- `outcome` 由**硬阈值**判定（见上表与 §5）。
- `evidence` 带可定位字段（`agent_id`/`step_id`/`role`/`snippet`/`ref_kind`），让失败点可跳转复核。
- `metric` 存原始数值（如 `{per_role: {...}, threshold: 10}`），供 HTML 下钻与跨会话统计。
- 构建于 `build_eval_report()`：遍历 `CHECKS` 目录，对客观项调 `evaluate`，装配成 `CheckPoint`。

### 4.2 主观检查（3 个：C5 / O3 / O4）

主观检查**带 rubric**、`auto=False`，默认 `pending`，有两条填充路径：

**路径 A — 人工标注**（`annotator.py`）
- 标注存于 `annotations/<session_id>.json`，rebuild 时 merge 进报告，主观结论跨"重新生成"持久化。
- 设计意图：rubric 内联在 HTML 里，降低人工标注成本。

**路径 B — cc-sdk LLM 评委**（`llm_judge.py`，`--judge` 显式触发，默认关）
- 起一个**独立的 `claude-agent-sdk` 会话**，配自定义 MCP 工具 `submit_verdict`，**强制结构化输出**：
  `{passed, confidence, reasoning, strengths, weaknesses, suggestions, dimension_scores[]}`。
  比"让 LLM 吐 JSON 再解析"稳健得多（学 astroneval）。
- 评委对仓库**只读**（`allowed_tools=["Read", "submit_verdict"]`），所以能回真源核验——
  这是判定**可审计**的根基。
- 评 3 个 area（`judge_prompts.py`），各自的评分维度（0-100）：
  | area | 填充 | 评分维度 |
  |---|---|---|
  | `synthesis` | O3 | completeness / accuracy / structure / actionability |
  | `planning` | C5 | decomposition_quality / focus / role_balance / scoping |
  | `role_depth` | 每个 role 的 `judgement` + O4（取最坏）+ 对应 task 的 `.quality` keypoint | depth / correctness / evidence / relevance |
- ⚠ **配置前提**：评委子进程不继承 `~/.claude/settings.json` 的 `env` 块，必须由
  `_load_claude_settings_env()` 注入（自定义 `ANTHROPIC_BASE_URL`/token/模型，如 BigModel），
  否则打到真 `api.anthropic.com` 反复 `api_retry`、从不调用 `submit_verdict`。
  可用 `TEAM_EVAL_CLAUDE_SETTINGS` 指向别的 settings.json。

---

## 5. 评分区间与聚合

### 5.1 单检查结果枚举（`CheckOutcome`）

`pass` / `warn` / `fail` / `not_applicable` / `pending`

各客观检查的**判定阈值**（集中列在此，便于一次看清）：

| 检查 | 阈值规则 |
|---|---|
| E2 步数 | `≤200` pass / `>200` warn |
| E3 每角色错误 | 任一角色 `>10` → fail；否则 0 错 pass / `>0` warn |
| O1 综合 | reduce_quality `complete`/`partial`/`none` → pass/warn/fail |
| O2 域覆盖 | 文本 `<200` 字符 → fail；覆盖率 `1.0`/`≥0.6`/`else` → pass/warn/fail |
| C4 收尾 | TeamDelete `1`→pass / `0` 或 `>1`→warn |
| F3 churn | 每角色 transcript `≤2`/`==3`/`else` → pass/warn/fail |
| R1 错误率 | `<5%`/`<15%`/`else` → pass/warn/fail |
| R3 干净终止 | `¬idle ∧ 全任务completed ∧ TeamDelete==1` → pass，否则 fail（并列原因）|

主观检查由评委决定：`_outcome_from_verdict()` → 评委 `passed=True`→pass；否则 `mean(dimension_scores)≥50`→warn，否则 fail；评委报错→pending。

### 5.2 health（会话级总分，`_score()`）

```
health = 100 × Σweight / n
weight: pass=1.0, warn=0.5, fail=0.0
n = 已出结论的检查数（pass/warn/fail 计入；pending / not_applicable 不计入）
```

- `pending`（未评的主观项）**不计入分母**——所以"主观还没评"时 health 只反映客观侧。
- ⚠ **已知简化**：health **未按 severity 加权**（一个 info 的 A1 fail 和一个 critical 的 C1 fail
  拉低同样的分）。这是 `reflections.md` 列的 v0.3 待办；目前靠 7-angle 结论把"致命角度"单独呈现来缓解。

### 5.3 维度 / 角度聚合（`_worst_outcome()`）

聚合多个结果时取**最坏**，排序：`fail(0) < warn(1) < na(2) < pass(3)`，`pending` 跳过。
即一个维度里只要有一个 fail，该维度/角度就是 fail。这让"致命单项"不会被其他 pass 稀释。

---

## 6. 按角色 / 按任务评测（粒度下沉）

会话级看不清"谁拖后腿""任务到底完成没"，所以下沉两级。每一级都能定位"谁/哪个任务/哪次执行"出问题。

### 6.1 RoleEval（每角色，`role_eval.py`）

每个角色一份 `RoleEval`，把该角色的多份 transcript 聚合成 `executions[]`（**每份 transcript = 一次执行尝试，churn 即多次**）。

`RoleExecution`（单次执行）字段：
`transcript_id / attempt / step_count / has_final_text / error_count / tool_calls / tool_success / tool_failure / recovered / first_ts / last_ts`

`RoleEval`（角色级 rollup）：
- `completion`：任一执行有 final text → pass（角色交付了产出）
- `error_profile`：`0 错` 或 `出错但全自愈/干净` → pass；`出错但交付了` → warn；`出错且没交付` → fail
- `churn` = 执行次数；`total_errors`、`tool_success/failure`、`tool_summary`
- `judgement`（主观）：由评委 `role_depth` 填充（depth/correctness/evidence/relevance）
- `keypoints`：`role.delivered` / `role.errors_bounded` / `role.depth_quality`(主观)

**自愈检测**（`error_correction.py:detect_recovery()`，核心创新）：
- 真自愈 `self_healed`：某个**报错的工具名**在**更晚的 step** 再次调用并成功。
- 弱信号：出错了但仍产出 final text。
- `recovered = self_healed ∨ (any_error ∧ delivered)`；`churned_to_death = any_error ∧ ¬recovered`。
- 意义：把"韧性"（出错自愈）和"失败"（出错没救回来）分开——harbor-research 里 benchmarks 角色错 19 次但全自愈 → `error_profile=pass`（韧性，非失败）。

### 6.2 TaskEval（每任务，`task_eval.py`）

每个共享清单任务一份 `TaskEval`，带 4 个 keypoint：

| keypoint | type | 判定 |
|---|---|---|
| `<id>.assigned` | 客观 | 任务分给了存在的角色 |
| `<id>.executed` | 客观 | owner 角色产出过交付物 |
| `<id>.closed` | 客观 | 任务在共享清单达到 `completed` |
| `<id>.quality` | 主观 | 交付物满足任务意图（由评委填） |

`completion` rollup：`completed`→pass / `delivered但未closed`→warn / 否则 fail。
**关键洞察**：harbor-research 的 5 任务全 `deleted` 但 owner 都 delivered → 任务级判 **warn**
（活干了，但没闭环）。这把"做没做"和"跟没跟踪到完成"分开——v0.1 做不到。

---

## 7. 检查点分类标签体系

一个检查点同时带多套 label，支撑过滤/聚合/事后归类：

| label 集 | 取值 | 用途 |
|---|---|---|
| **dimension** | structural / execution / coordination / outcome / efficiency / robustness / atif | 归类 + angle 聚合的输入 |
| **severity** | info / minor / major / critical | 失败的严重度（注：health 暂未用它加权） |
| **kind** | objective / subjective | 决定是自动评还是待标注/评委 |
| **auto** | True/False | 是否客观自动评（= kind==objective） |
| **scope** | session / role:`<role>` / task:`<id>` | 作用域（默认 session） |
| **tags** | team, leader, spawn, churn, role, output, steps, error, task-lifecycle, message, message-error, shutdown, delegation, reduce, coverage, quality, depth, token, parallel, intervention, termination, atif | 事后按类聚合："哪类故障最频繁/致命" |

---

## 8. 记录与统计的数据

### 8.1 EvalReport 顶层（`models.py`）

```
session_id, team_name, pattern, reduce_quality,
checks[], role_evals[], task_evals[], conclusion[],
stats{}, score{}, generated_at, notes[]
```

### 8.2 stats（`stats.py:compute_stats()`，跨会话统计的基础）

| 分组 | 字段 |
|---|---|
| `leader` | steps, assistant_turns, tokens{prompt,completion,cached}, errors, has_final_text |
| `subagents` | count, by_role, roles |
| `totals` | steps, tokens{prompt,completion,cached,all}, errors, tool_calls |
| `tool_hist` | 各工具调用次数（leader+所有队友合计） |
| `messages` | leader_outgoing, teammate_to_leader, peer, send_errors |
| `spawns` | spawn 总数 |
| `tasks` | total, by_status |
| `churn` | by_role, max（每角色 transcript 数） |
| 其他 | teamdelete_count, taskstop_targets, wall_clock_sec, worker_parallel, span{first,last}, pattern, reduce_quality |

### 8.3 评委产出（`Verdict` / `DimensionScore`）

```
Verdict: passed, confidence(0-1), reasoning, strengths[], weaknesses[],
         suggestions[], dimension_scores[], evidence_refs[], judge_model, cost_usd, error
DimensionScore: id, name, score(0-100), analysis, suggestions[]
```

### 8.4 可定位证据（`CheckEvidence`）

`agent_id / step_id / role / snippet / ref_kind(spawn|message|task|error|...)`——每个失败点都能跳到具体步骤复核，是"评测可审计"的物理载体。

### 8.5 产物落盘

```
runs/<session_id>/  atif.json + graph.json + eval.json + report.html
sessions/index.json  跨会话数据集索引（session_id/team/pattern/health/score_counts/...）
annotations/<session>.json  主观人工标注（持久化，rebuild 时 merge）
```

---

## 9. 模式识别与 reduce 质量（`graph/pattern_detect.py`）

| 字段 | 取值 | 判定依据 |
|---|---|---|
| `pattern` | mapreduce / pipeline / supervisor / custom | 拓扑 + 并行度：leader 扇出且无 peer 消息 → 并行则 mapreduce / 串行则 supervisor；有 peer 消息 → pipeline；否则 supervisor/custom |
| `pattern_confidence` | 0.3~0.85 | 模式把握度 |
| `pattern_reason` | 文本 | 判定理由 |
| `reduce_quality` | complete / partial / none | leader 最长文本 ≥800 字 ∧ 在所有 worker 结束**之后**且有 ≥400 字非 idle 文本 → complete；仅够长 → partial；都不满足 → none |

模式识别的价值：不同模式怕不同失败（pipeline 怕"阶段间断链"，mapreduce 怕"reduce 缺失"），
检查点应随模式动态加权（v0.3 待办）。

---

## 10. 已知简化与迭代方向（v0.3，详见 `reflections.md`）

| 项 | 现状 | 待办 |
|---|---|---|
| health 加权 | 未用 severity | critical fail 应比 info fail 拉低更多 |
| O2 假阳性 | 查 leader 最长文本（可能是规划非综合） | 与 reduce_quality 解耦，仅在真综合后计覆盖 |
| churn 分类 | 不分重启/唤醒 | 区分"重启 churn"（robustness 问题）与"唤醒 churn"（正常协调） |
| 跨会话基线 | 绝对硬阈值 | 攒 ≥5 同模板会话后换 z-score 相对基线 |
| 模式×失败 | 检查点不随模式变 | 按模式动态启用/加权检查点 |
| 成本/延迟 | F1 只报 token | 加 wall-clock + 每角色 token 占比 |
| 评委阈值 | `passed` 布尔偏宽松（correctness 48 也 pass） | 低维度（accuracy/correctness<50）单独拉到 warn/fail；低 conf 角色标"需人工复核" |

> **方法论小结**（`reflections.md`）：评测 = **可审计的事实判定（客观）+ 显式标注的主观判断
> （pending）+ 统计上下文**，三者分开呈现，不混成一个不可解释的分数。指标要对得起根因：
> 拆维度、带 evidence、带 tag，让"为什么这个会话 72.7"可回答。单会话定体系有偏差——
> 本体系就是用来在更多样本上**证伪/收紧**每一项阈值的。

---

## 附录：harbor-research 实测（佐证，health 72.7 → 76.0）

跑通评委后的 7-angle 结论：

| angle | verdict | 关键依据 |
|---|---|---|
| goal | warn | O1 reduce=partial；5 任务全 deleted、0/5 闭环（但 owner 全 delivered） |
| planning | pass | S1-S3；评委 C5 conf 0.83（拆解 87/聚焦 80/均衡 74/范围 79），引用 job.py:920 |
| delegation | fail | C1 任务 0/5 completed、C3 5 条 SendMessage 报错 |
| execution | warn | 16/16 出 final text；错误集中 benchmarks（7）；5 角色差异化（cli 强/arch·bench 弱） |
| robustness | fail | R3 未干净终止；但 10 次出错全自愈、0 次 churn 到死 |
| efficiency | fail | 1760 万 token（94% cached）、每角色 3-4 transcript |
| conformance | pass | A1-A3 ATIF 往返+引用全通过 |

评委按角色深度（最有价值的差异化产出）：cli-researcher 最强（depth 86/correct 83/conf 0.82），
arch-researcher（correctness **48**/conf 0.55）与 benchmarks-researcher（correctness 58）偏弱——
而 benchmarks 正是客观侧"7 错全自愈"的角色，**韧性 ≠ 内容正确**，两套信号互补。
