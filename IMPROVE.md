# IMPROVE.md — 战略改进方案（打动评委版）

## 核心战略：从"demo"进化为"全网自组合智能体"

当前项目是一个固定流水线（6个硬编码agent）。改进方向是让 orchestrator 变成一个
**self-composing agent** —— 它不只知道自己的5个子agent，而是能发现网络上所有
可用的 P2P 服务，根据用户意图自动选择和编排。

这是 ONLY AgentNetwork 才能做的事。MCP做不到，A2A做不到，只有P2P mesh + ANS才行。

## 改进清单（按优先级）

### P0: Web UI Dashboard（视觉冲击力）
创建一个终端风格的 Web Dashboard，实时展示：
- 当前 P2P 网络上发现的所有服务（从 ANS 查询）
- 流水线执行过程的可视化（节点连线 + 数据流向）
- 每个 agent 调用的耗时、cost、状态
- 技术：纯 HTML + JS + SSE (Server-Sent Events)，不需要框架

文件：agents/dashboard.py (FastAPI + 静态HTML)
端口：7400
路径：GET / (HTML页面), GET /api/stream (SSE实时数据), GET /api/services (服务列表)

### P1: Self-Composing Orchestrator（核心创新）
改造 orchestrator，使其具备：
1. 启动时调用 `anet svc discover` 发现网络上所有可用服务
2. 根据用户输入的文本特征，自动选择需要的处理步骤
3. 如果网络上出现了新服务（比如一个 translation service），自动纳入流水线
4. 返回结果时附带"发现报告"：发现了哪些服务、选择了哪些、为什么

改造 agents/orchestrator.py：
- 添加 GET /v1/discover 端点（返回当前网络上发现的所有服务）
- 改造 POST /v1/analyze：
  - 先 discover 所有 skill=content-intel 的服务
  - 如果发现新服务（不在本地列表中），尝试调用
  - 返回 {results, discovered_services, pipeline_plan}

### P2: 新增 3 个差异化 Agent
增加到 9 个服务，展示体系完整度：

1. **factcheck-svc** (port 7407, skill=factcheck, per_call=8)
   - 事实核查：检查文本中的数字、日期、名称是否合理
   - POST /v1/factcheck {text} → {claims: [{claim, status, confidence}]}

2. **translate-en-zh-svc** (port 7408, skill=translate-en-zh, per_call=5)
   - 英→中翻译（当前只有中→英）
   - 让流水线支持双向翻译

3. **keywords-svc** (port 7409, skill=keywords, per_call=3)
   - 关键词提取（不是 entity，是 TF-IDF 风格的关键词）
   - POST /v1/keywords {text, top_k} → {keywords: [{word, score}]}

### P3: 增强审计 + Shell 经济展示
在 client.py 输出中增加：
- Shell 账户余额查询（每个 daemon 的 credits）
- 调用链路图（ASCII art 展示 A→B→C 的调用关系）
- 总 cost 汇总

### P4: README 升级
- 增加 GIF/ASCII 动画展示流水线
- 增加"为什么必须 P2P"的论述
- 增加与其他方案的对比表
- 增加"评委5分钟指南"

## 文件变更清单
- agents/orchestrator.py — 改造为 self-composing
- agents/dashboard.py — 新增 Web UI
- agents/factcheck.py — 新增
- agents/translate_en_zh.py — 新增
- agents/keywords.py — 新增
- agents/register.py — 可能需要小改
- agents/anet_sdk.py — 可能需要添加 discover 方法
- client.py — 增强输出
- scripts/setup-nodes.sh — 增加到9个daemon
- scripts/run-all.sh — 增加到9个agent
- scripts/run.sh — 适配
- README.md — 大幅升级
- static/index.html — Web UI 前端

## 成功标准
1. bash scripts/run.sh 一键跑通
2. Web Dashboard 可以看到实时流水线执行
3. orchestrator 能发现并调用网络上其他人的服务
4. 9个服务全部注册到公网 ANS
5. 评委在自己机器上 5 分钟内跑通
