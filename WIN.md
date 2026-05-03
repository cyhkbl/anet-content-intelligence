# WIN.md — 夺冠改造方案

## 当前问题
评委把我们归类为"NLP商品市场"（#8），不是"协议级基础设施"。
前3名都是**协议**：PNEUMA=仲裁协议, SWARM-INTEL=共识协议, SWARM-MEMORY=记忆协议。
我们需要把 marketplace 从"NLP的附属品"升级为"Agent Network 的服务交易协议"。

## 核心战略：SHELL MARKET PROTOCOL

把拍卖/声誉/共识系统提取为**独立的协议层服务**，任何 skill 的 provider 都能接入。

### 新增 3 个协议级服务

1. **reputation-svc** (port 7420, skill=reputation, per_call=0, FREE)
   - 全网声誉注册表
   - POST /v1/report {service, score, caller_did} — 报告一次调用结果
   - GET /v1/lookup/{service} — 查询服务声誉
   - GET /v1/leaderboard — 声誉排行榜
   - 内存存储，重启清零（demo够用）

2. **auction-svc** (port 7421, skill=auction, per_call=0, FREE)
   - 全网拍卖行
   - POST /v1/open {skill, text, k?} — 开启一轮密封拍卖
   - POST /v1/bid {auction_id, service, peer_id, cost, eta_ms} — 提交竞价
   - POST /v1/close/{auction_id} — 关闭拍卖，返回赢家
   - GET /v1/active — 当前进行中的拍卖

3. **market-dashboard-svc** (port 7422, skill=market-dashboard, per_call=0, FREE)
   - 实时市场仪表盘（不是原来的pipeline dashboard）
   - GET / — HTML页面，展示：
     * 实时拍卖竞价表
     * 声誉排行榜
     * 服务价格对比图
     * 竞价历史
   - GET /api/leaderboard — JSON声誉排行
   - GET /api/auctions — JSON拍卖记录

### 改造 orchestrator
orchestrator 现在通过调用 auction-svc 和 reputation-svc 来完成拍卖：
1. 调用 auction-svc /v1/open 开启拍卖
2. discover 所有 provider，让它们 /v1/bid
3. 调用 auction-svc /v1/close 获取赢家
4. 调用赢家执行实际任务
5. 调用 reputation-svc /v1/report 报告结果

### 改造所有 provider agent
每个 provider 的 /v1/quote 端点改为同时向 auction-svc 注册竞价。

### README 重写
- 标题改为 "Shell Market Protocol — Agent Network 的服务交易协议"
- 强调这是**协议层**，不是NLP工具
- 展示任何skill都能接入拍卖市场
- 5分钟评委指南

### 脚本更新
- setup-nodes.sh: 13个daemon（10个agent + 3个market protocol）
- run-all.sh: 启动所有服务
- 端到端测试

## 成功标准
1. bash scripts/run.sh 一键跑通
2. 评委看到的是"服务交易协议"而不是"NLP工具"
3. reputation/auction/market-dashboard 作为独立协议服务存在
4. 任何新 provider 只需接入 /v1/quote 就能参与拍卖
5. 声誉排行榜实时更新
