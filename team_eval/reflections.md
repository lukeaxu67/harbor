# 指标设计迭代日志 (reflections)

> 每次新增会话或修订指标时追加一条：改了什么 / 为什么 / 对新会话的影响。
> 起点是 v0.1，基于单个真实会话（`harbor-research`，map-reduce，health 72.7）。

---

## v0.1 — 初版指标体系（2026-06-24）

### 一、先想清楚"评什么"：team 执行的三层失败模式

多智能体 team 比 single-agent 难评，因为失败可以发生在三个不同层面，混在一起就看不
清根因：

1. **编排层**（structural / coordination）—— 团队没成形、委派错乱、任务没闭环、收尾
   不干净。这是 team 特有的，single-agent 评测完全没有。
2. **执行层**（execution / robustness）—— 单个 agent 卡住、报错、retry churn、没产出。
   和 single-agent 重叠，但要看"每个" agent 而非整体。
3. **效用层**（outcome / efficiency）—— 最终交付物是否合格、成本是否失控。这才是用户
   真正在乎的，但最依赖主观判断。

所以维度刻意拆成 7 个，而不是一个总分——**总分掩盖根因**。一个 health=72.7 的会话，
可能是"编排完美但综合质量差"，也可能是"编排稀烂但勉强出活"，修法完全不同。

### 二、为什么这样设计检查点

- **客观优先**：能从数据自动判定的，绝不留给主观。`C1 任务闭环`、`C3 消息错误`、
  `F3 churn`、`R3 干净终止` 都是"硬事实"，机器就能下结论，且复现稳定。
- **主观显式 pending**：综合质量（O3）、研究深度（O4）、委派纪律（C5）需要人/LLM 判
  断，故意标 `pending` 而非臆造一个分数——**不假装能测的东西其实测不了**。rubric 内联
  在 HTML 里，降低标注成本。
- **evidence 必须可定位**：每个失败点带 `agent_id`/`step_id`/`ref_kind`，让人能一键跳
  到出问题的那一步复核，否则评测不可审计。
- **tag 用于事后归类**：`task-lifecycle`/`churn`/`message-error`/`reduce`——单个会话
  看不出价值，攒 10 个会话后能问"哪类故障最频繁 / 最致命"，驱动指标演进。
- **severity 与 outcome 分离**：`info` 的 A1-A3（ATIF 合规）即使 fail 也不该和 `critical`
  的 C1（任务没闭环）同等拉低 health。当前 health 只按 outcome 加权，**没用 severity
  加权**——这是已知简化，见下方待办。

### 三、第一个真实会话验证了什么（也暴露了什么）

`harbor-research` 是个"看起来跑完了、其实没成功"的典型，正好压测体系：

- ✅ **精准命中**：C1（0/5 任务 completed、全 deleted）、C3（5 条 SendMessage 报错）、
  C4（TeamDelete 重试 4 次）、F3（每角色 3~4 份 transcript 的 churn）、R3（leader 停在
  idle ping、未干净终止）——这些是"编排/协调层"的真实失败，体系全部抓到且有 evidence。
- ⚠️ **太宽松的地方**：
  - `O2 域覆盖 PASS`（5/5）有点假阳性——它检查的是 leader **最长的一段文本**是否提到
    各域关键词，但那段可能是规划/闲聊而非真正的综合报告。reduce_quality 判为 `partial`
    已经反映了这点，O2 却仍 PASS。**待修**：O2 应只在 reduce_quality∈{complete,partial}
    且文本出现在 workers 完成之后才计覆盖。
  - `R1 错误率 4.6% PASS`——恰好在 5% 阈值下，但错误高度集中在 benchmarks-researcher
    （7 个）。平均化掩盖了单点。E3 单独抓到了（warn），但 R1 的"总体率"视角偏乐观。
- ⚠️ **太严苛 / 需斟酌**：
  - `F3 churn` 用"每角色 transcript 数 >2 即 fail"——但 churn 的根因（是队友被重启？
    还是 leader 重复 SendMessage 唤醒？）没区分。**待修**：区分"重启 churn"与"唤醒
    churn"，前者才是 robustness 问题。
  - `R3 干净终止` 把"tasks 全 completed"作为必要条件——但有些任务合理地停在
    in_progress/deleted（用户主动取消）。当前一概判 fail 偏严。
- ❓ **测不到的**：综合报告的**正确性/无幻觉**（O3/O4）完全留白，pending。需要 LLM-judge
  或人工，这是体系诚实的边界。

### 四、待迭代清单（拿到更多会话后逐项做）

1. **health 引入 severity 加权**（critical fail 应比 info fail 拉低更多）。
2. **O2 覆盖与 reduce_quality 解耦修正**（避免假阳性）。
3. **churn 分类**：重启型 vs 唤醒型（看 transcript 之间是 sessionId 续接还是全新）。
4. **per-agent 健康分**：现在只有 E1/E2/E3 散点，应聚合出每个队友的子分数，便于
   定位"哪个角色最弱"。
5. **跨会话基线**：攒 ≥5 个同模板会话后，把绝对阈值（如 churn≤2、错误率<5%）换成
   相对基线（z-score），避免单一硬阈值误判不同任务难度。
6. **模式 × 失败 关联**：pipeline 比 mapreduce 更怕"阶段间断链"，应让检查点随模式
   动态启用/加权。
7. **成本/延迟**：F1 现在只报 token；应加 wall-clock 与每角色 token 占比，识别"谁是
   成本大头"。

### 五、方法论小结

- 评测 = **可审计的事实判定（客观）+ 显式标注的主观判断（pending）+ 统计上下文**，
  三者分开呈现，不混成一个不可解释的分数。
- 指标要**对得起根因**：拆维度、带 evidence、带 tag，让"为什么这个会话 72.7"可回答。
- 单会话定体系有偏差——本日志就是用来在更多样本上**证伪/收紧**每一项阈值的。

---

## v0.2 — 深化：7 角度 / 按角色按任务 / cc-sdk 评委 / 时序图（2026-06-24）

### 驱动
v0.1 是"会话级 + 角色聚合"，回答不了"哪个角色拖后腿""任务到底完成没""中途出错
纠正了吗"。本轮按用户的"领导力类比"重做：把评测拆成**可回答结论的 7 个通用角度**，
并下沉到**角色执行级**和**任务级**。

### 四项升级
1. **7 个通用角度 → 结论**（goal/planning/delegation/execution/robustness/efficiency/
   conformance）。每个角度回答一个具体问题，verdict 由其下的检查点+角色/任务信号
   聚合（取最坏）。报告开头直接回答"任务完成了吗→规划好吗→谁拖后腿→哪崩的→值不值"。
2. **按角色按任务评测**：
   - `RoleEval`：每份 transcript=一次执行（churn 即多次尝试），各自判完成/错误/自愈；
     `RoleExecution.recovered` 用"出错工具名后续是否成功"检测**真·自愈**（区别于
     churn 到死）。
   - `TaskEval`：每任务 4 个 keypoint（assigned/executed/closed/quality）+ 完成判定。
   - 关键洞察：harbor-research 的 5 任务全 deleted 但 owner 都 delivered → 任务级判定
     **warn**（活干了，但没闭环）。这把"做没做"和"跟没跟踪到完成"分开了——v0.1 做不到。
3. **cc-sdk 评委**（学 astroneval）：独立 claude-agent-sdk 会话 + 自定义 MCP 工具
   `submit_verdict` **强制结构化输出** `{passed,confidence,reasoning,strengths,
   weaknesses,suggestions,dimension_scores[]}`。已实测端到端：对综合报告判定
   passed=True/conf0.78/cost$0.35，reasoning **引用了 trials.py:210-223 具体行号**，
   还指出"leader 主动纠正 log-filter flag 命名差异"。MCP 工具比"让 LLM 吐 JSON 再解析"
   稳健得多。`--judge` 显式触发（synthesis+planning+5×role_depth≈7 次调用≈$2.5）。
4. **泳道时序图**（自绘 SVG）：纵向时间、每列一角色生命线、箭头=角色间交互
   (spawn/message/task)、红点=错误、可筛选/点击详情。补上了拓扑图(箭头重叠)和时间线
   (看不到交互)都缺的"交互时序"视角。harbor-research 渲染出 87 事件。

### v0.1 反思清单的处置
- ✅ severity 加权：仍未做（health 仍按 outcome）；但 7 角度结论已把"致命角度"单独
  呈现，缓解了"总分掩盖根因"。severity 加权移到 v0.3。
- ✅ O2 假阳性：仍待修（最长文本覆盖≠真综合）；但 cc-sdk 评委的 accuracy 维度现在
  能独立判定"是否幻觉/答非所问"，主观侧已堵住。
- ✅ churn 分类：已实现"自愈 vs churn 到死"（recovered 字段），benchmarks 19 错全自愈
  → err_profile=pass（韧性，非失败）。重启型 vs 唤醒型仍未分（v0.3）。
- ✅ per-agent 健康分：RoleEval 已是每角色子记分卡。
- ⏳ 跨会话基线、模式×失败关联、成本/延迟细化：仍待样本。

### 方法论增量
- **评测的尽头是"能支撑什么结论"**：先列要回答的 7 个问题，再反推需要哪些检查点/数据。
  不要先堆指标再硬凑意义。
- **客观与主观的分工要诚实**：能从轨迹确定的（闭环/错误/自愈/churn）走客观；需要语义
  判断的（质量/深度/规划合理性）走 cc-sdk 评委，且**用 MCP 工具锁结构**避免解析地狱；
  暂不判的显式 pending，绝不臆造。
- **粒度按"归因需要"下沉**：会话级→角色级→任务级→执行级，每一级都能定位"谁/哪个任务/
  哪次执行"出的问题，否则评测不可行动。

### v0.3 待办
severity 加权 health / O2 覆盖与 reduce 解耦 / churn 重启vs唤醒分类 / 跨会话相对基线 /
模式×失败关联 / 成本延迟细化 / 评委 prompt 调优与多评委投票。

---

## v0.2.1 — 评委首次真实联调：修 SDK 环境继承 + 验证主观点（2026-06-24）

### 触发
v0.2 声称 cc-sdk 评委"已实测端到端"，但首次在真实环境跑 `--judge` 时，7 次会话全部
返回 `judge did not call the verdict tool`，主观项全 pending。排查后发现是 **SDK 环境
继承 bug**，修好后评委才真正工作。

### 根因（值得记，因为会反复咬人）
claude-agent-sdk 起 claude 子进程时，**不会**可靠继承用户 `~/.claude/settings.json` 的
`env` 块。本机用的是 BigModel 的 Anthropic 兼容端点（`ANTHROPIC_BASE_URL`+自定义 token
+`ANTHROPIC_MODEL=glm-5.2`），全部写在 settings.json 的 env 里。子进程拿不到这些 → 落到
真正的 `api.anthropic.com` → 反复 `api_retry`（指数退避，于是单会话看着"跑了很久"其实
没产出）→ 模型从没机会调 `submit_verdict`。表现：cost≈$0.005/会话（几乎零产出）、stderr
全是 api_retry、`state.called=False`。

### 修法（`eval/llm_judge.py`）
- 新增 `_load_claude_settings_env()`：读 `~/.claude/settings.json` 的 `env` 块（可用
  `TEAM_EVAL_CLAUDE_SETTINGS` 覆盖路径），通过 `ClaudeAgentOptions(env=...)` 注入子进程。
- 默认 `model` 取 env 里的 `ANTHROPIC_MODEL`（如 glm-5.2），避免 SDK 的 tier 别名映射到
  `<model>[1M]` 还要 beta 的歧义。
- 加 `setting_sources=["user"]` + `stderr` 捕获，失败时把 SDK stderr 尾部拼进 error，
  不再是"Check stderr output for details"黑盒。
- 诊断法：先跑一个**最小 prompt**（直接命它调 submit_verdict）确认链路通，再跑全量。
  最小 prompt 通了（`submit_verdict called=True`、ResultMessage success）才值得跑 7 会话。

### 评委在 harbor-research 上的真实产出（health 72.7 → 76.0，pending 3 → 0）
- **C5 规划**：pass，conf 0.83。维度 decomposition 87/focus 80/role_balance 74/scoping 79。
  reasoning **引用了真实代码行号**（`job.py:920` asyncio.TaskGroup、`trials.py:210-223`），
  并点出 Leader 主动核验并推翻了"log-filter 差异"误报（实为 `--*-include/exclude-logs`
  命名差异）——这正是 Read 仓库权限的价值：判定**可审计**，不是盲吐 JSON。
- **O3 综合**：pass，conf 0.72。completeness 84/accuracy 82/structure 88/actionability 80。
- **按角色深度（差异化，最有价值）**：
  - cli-researcher：强（depth 86/correct 83/evidence 87/conf 0.82）
  - agents-env-researcher：强（evidence 85/relevance 90/conf 0.80）
  - arch-researcher：**correctness 48 / evidence 50**，conf 仅 0.55 —— 评委明确低把握 + 准确性存疑
  - benchmarks-researcher：correctness 58 / evidence 58，conf 0.62（注意：这正是客观侧 7 错全自愈的那个角色，韧性 ≠ 内容正确，两套信号互补）
  - infra-researcher：中（depth 72/conf 0.70）
- 总成本 **$1.69 / 7 会话**（README 旧估 $2.5 偏高，已更正）。

### 新暴露的张力（进 v0.3 评委调优）
- arch-researcher correctness 才 48，评委却给 `passed=True`（因 mean 61.25 过了它"≥60
  且无致命问题"的阈值）。说明 **`passed` 布尔偏宽松，dimension_scores 才是真信号**。下游
  `_outcome_from_verdict` 又只看 `passed` → 把 correctness 48 也判成 pass。应让低维度
  （尤其 accuracy/correctness <50）能单独把该角色/检查拉到 warn 或 fail。
- 评委 conf 的方差（0.55~0.83）本身就是有用信号：低 conf 的角色（arch 0.55）应单独
  标记"存疑、需人工复核"，而不是和高 conf 角色一视同仁。

### 方法论增量
- **主观评测的可审计性来自"评委能读真源"**：cc-sdk 评委 + Read 权限 + 强制 MCP 结构化
  工具，三者缺一不可。引用行号的 reasoning 让"为什么 pass/warn"可复核，这是它胜过
  盲 JSON 的根本原因，也是 O2 假阳性问题的主观侧补丁（评委 accuracy 维度独立判真伪）。
- **配置继承是评测工具的隐藏正确性维度**：跨供应商/代理场景下，"评委到底打到哪个端点"
  不验证就没意义。最小 prompt 冒烟测试应成为 judge 的自检步骤。

