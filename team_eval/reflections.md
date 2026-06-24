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
