# Coding Agent 优化记录

> 每日自动记录 Coding Multi-Agent 的改进、问题修复和待优化事项。

## 2026-08-02

### 已优化

- 完善 Coordinator、Coding Agent、Verification Agent 三模块协作。
- 增加受限工作区、真实文件生成和自动测试返工流程。
- 完成第一阶段：任务协议、Schema 校验、上下文筛选、命令白名单、逐条验收和运行记录。

### 已解决

- 解决原 Demo 仅模拟修改和验证、未真实操作项目的问题。
- 增加路径穿越防护、命令超时及失败反馈。
- 解决模型计划未经校验、整个代码库可能进入上下文、任务过程无法持久化的问题。

### 待优化

- 接入真实大模型 Coding Backend。
- 增加资源隔离和高风险操作审批。
- 增加工作区快照与回滚、敏感信息脱敏和模型调用指标。

## 2026-08-03

### 已优化

- 明确 Coordinator、Coding Agent、Verification Agent 与 Runtime 的职责边界。
- 接入 DeepSeek API CodingBackend，并增加精确命令授权、受保护路径和独立验收脚本。
- 将模型层重构为供应商无关接口、工厂、注册表和 OpenAI-compatible 协议适配器。
- 引入与 Agent、模型供应商解耦的 RoleSpec 和角色注册表，支持 planner、implementer、reviewer、tester、fixer 动态职责。
- Coordinator 按规划、实现、验证和返工阶段注入角色，并记录角色切换历史。
- Worker 在运行时检查写入或验证能力，避免只依靠角色提示词控制权限。
- 增加 Multi-Agent 可视化界面，支持直接输入需求并实时查看角色交接、实现、验证和返工事件。
- 提取 CLI 通用执行服务，使命令行和界面共享供应商配置、工作区权限与验证策略。
- 增加当前架构与权限边界信息图，覆盖角色分工、模型层、受控 Runtime 和禁止操作。
- 离线回归测试扩展至 20 项，角色切换、能力拒绝、运行记录和原工作流均通过。

### 已解决

- 解决模型可能接触密钥、修改验收脚本、自选验证命令和多文件写入失败的问题。
- 解决角色仅靠提示词约束的问题，增加写入与验证能力的运行时检查。
- 解决 CLI 与展示界面执行逻辑重复的问题，统一使用同一任务执行入口。

### 待优化

- 增加操作系统级网络、CPU、内存和进程隔离。
- 增加工作区版本快照、并行任务租约和集成冲突检测。
- 将 Reviewer 接入主流程，并实现 TaskSpec、WorkerPool 和 Scheduler 以支持安全并行调度。

## 2026-08-04

### 已优化

- 引入不可变 RoleMemoryView、MemoryPolicy 和 MemoryManager，按角色裁剪任务上下文。
- 为 implementer、fixer、reviewer 设置独立项目文件预算，tester 不接收项目源码。
- Coding Backend 和 Verification Worker 改为消费角色 Memory View，保留现有 CLI 与 Web 工作流。
- 将 Tester 与独立 Reviewer 接入并行质量阶段，减少实现后串行等待。
- 引入 ResultEnvelope 和 Task Version，由 Coordinator 校验并单点合并并行结果。
- Web UI 增加双角色并行状态、Reviewer 结果和结果封装事件展示。
- 统一 AgentMessage 交流协议，覆盖请求、交接、结果、反馈、状态和最终通知。
- Agent 消息增加任务版本、发送方、接收方、关联 ID、JSON Payload 与 UTC 时间。
- Web UI 改为展示 sender → recipient 消息流，旧执行事件仅保留用于审计。
- 自动化测试扩展至 29 项并全部通过。

### 已解决

- 解决 Worker 可直接读取完整 TaskContext、角色间上下文缺少最小权限隔离的问题。
- 禁止任何角色配置密钥访问，并限制 fixer 仅接收验证反馈、tester 仅接收获准命令。
- 解决 Reviewer 只注册未工作的资源闲置问题，并防止迟到结果覆盖新任务版本。
- 解决 Agent 交接格式不一致的问题，并递归拒绝消息 Payload 中的敏感字段。

### 待优化

- 将 Planner 接入真实执行流程，并为其持久化独立 PlanningResult。
- 基于 TaskSpec 拆分互不重叠的实现子任务，进一步并行多个 Implementer。
- 隔离验证子进程的环境变量、网络、CPU、内存和进程权限。

## 2026-08-05

### 已优化

- 无新增。

### 已解决

- 无新增。

### 待优化

- 无新增。

## 2026-08-07

### 已优化

- 将项目定位调整为供应商无关的 Coding Agent Harness，并新增声明式 WorkflowSpec、NodeSpec、WorkerRegistry 与 CancellationToken。
- 保留 Coordinator 兼容入口，新增 CodingHarness 控制面，按 Role 动态解析 Worker。
- Web 界面增加工作流 DAG、实时事件时间线和节点详情，展示状态、权限、耗时、产物及关联事件。
- 增加 Harness、可视化和 Fixer 条件节点测试，自动化测试扩展至 36 项并全部通过。

### 已解决

- 解决固定 Agent 拓扑与控制职责混杂的问题，并增加工作流依赖缺失和环检测。
- 解决未触发 Fixer 长期显示“等待”造成的误解，区分无需返工与尝试次数耗尽。
- 修复可视化界面初始节点详情为空和最终结果区域提前显示的问题。

### 待优化

- 让 WorkflowSpec 真正驱动通用 DAG Executor，继续移除 CodingHarness.run 中的硬编码分支。
- 增加节点级超时、执行中取消、Checkpoint 恢复和统一 Execution Gateway。
- 将轮询事件更新升级为 SSE，并补充 Token、模型调用和关键路径指标。

## 2026-08-08

### 已优化

- 新增 Harness 架构边界问题清单，按 P0、P1、P2 记录 11 项已确认问题、风险、目标和完成标准。
- 明确优先从 NodeInput/NodeResult、移除 active_role 隐式依赖和并行不可变输入开始演进。

### 已解决

- 无新增。

### 待优化

- 让 WorkflowSpec 真正驱动执行，并明确 Planner 是真实节点还是 Harness 内部准备阶段。
- 统一 Worker 契约与 Execution Gateway，落实 Memory Scope 和运行中取消。
- 按实际复杂度评估拆分 ProjectWorkspace 与 RunRecorder 的混合职责。

## 2026-08-09

### 已优化

- 无新增。

### 已解决

- 无新增。

### 待优化

- 评估引入受控 Task Board、版本化通用 Artifact 和固定 Coding 评测集。
- 补充 Docker、本地模型适配与可量化的成功率、返工率、Token 和耗时指标。

## 2026-08-11

### 已优化

- 无新增。

### 已解决

- 无新增。

### 待优化

- 无新增。
