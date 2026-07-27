# Discovery Research Workbench

一个可从零开始的、人机协作研究工作台。Human 负责研究目标、领域判断与最终决策；Main Agent 负责推进工作、调用专项能力、组织证据，并把结论交回 Human 审阅。

本仓库是**干净模板**：不包含任何具体课题、数据集、论文、实验、路线、基线、结果、知识条目或历史记忆。克隆后即可将它作为一个新研究 Topic 的起点。

## 何时使用什么

- 目标明确或工作强顺序依赖时，Main Agent 直接在 `subprojects-main/<id>/` 中工作。
- 结论依赖大量外部资料时，使用 `chatgpt-handoff` Skill 准备可人工执行的 Deep Research 交接包。
- 只有当问题重要、存在多条实质不同路线、并且能定义共同 Candidate 与可信 Evaluation 时，才使用 `create-exploration-problem` Skill 建立 Team。

## 开始一个新课题

1. 克隆仓库并在仓库根目录工作。
2. 阅读并按需调整 [AGENTS.md](AGENTS.md)：它定义 Human/Main 的协作边界、研究品味和记忆写入规则。
3. 在 `.DiscoveryProgram/memory/main.md` 中写入你的课题入口简报；这是新会话必须先读的文件。
4. 目标明确的工作直接新建 `subprojects-main/<id>/`，不需要预设模板。
5. 需要 Team 搜索时，先由 Human 与 Main 明确研究设计，再使用 `create-exploration-problem` Skill 创建第一个 Problem。它会注册 `subprojects-team/<problem-id>/`，而非把任何示例课题带入仓库。

先确认模板完整性：

```bash
./discovery _control validate
./discovery _control status
```

空工作台通过 `validate` 是正常的；在创建并配置至少一个 Problem 前，`./discovery start` 会拒绝启动 Dashboard。

## 目录结构

```text
AGENTS.md                         Human/Main 协作与研究治理
CLAUDE.md                         独立第三方审查者的边界
.DiscoveryProgram/                Topic 控制面、知识与长期记忆
.agents/skills/                   Main Agent 专项技能
.discovery/                       运行时、Problem/Route 模板与测试
subprojects-main/                 Main 直接工作的自由目录
subprojects-team/                 经设计和注册的 Team Problems
discovery                         Human/Main 控制命令
```

## 运行要求与边界

核心结构校验只需要 Python 3。完整的 Team 调度、资源隔离和 Dashboard 还需要 Linux 上可用的 Codex、cgroup v2/systemd 资源执行环境及相应权限；运行 `./discovery doctor` 可检查这些前提。

`.gitignore` 会排除运行时凭据、队列状态和临时输出，但不会排除你主动纳入版本控制的研究设计、代码、评价合同和证据。发布到公开 GitHub 前，请自行选择适用许可证，并检查你新增的数据、模型、外部资料和密钥是否允许公开。
