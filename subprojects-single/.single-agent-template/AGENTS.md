# Single-Agent Research Project

本目录是 Project Agent 的独立 Git 项目根，不承担 Topic Main Agent 职责。每次新会话先完整读取 `.ResearchProject/memory/main.md`，再围绕当前目标实践、观察并提取经验。

## 记忆系统

```text
.ResearchProject/
├── memory/
│   ├── main.md
│   └── logs/<id>.json
└── knowledge/
    ├── README.md
    ├── items/<item-id>/
    ├── items.json
    └── topics.json
```

`main.md` 只保留目标与背景、元认知、当前进展与必要文件索引。元认知通过持续实践、观察和经验提取形成；只保留会影响后续判断、具有证据依据并说明适用边界的认识。

出现实质性或值得长期保留的进展时，Project Agent 向 Human 提议一条 Log。只有 Human 批准后才能写入。每条 Log 使用 `id`、`created_at`、`progress` 和 `experience`，记录进展、关键数据、证据位置、具体经验及其限制；已写入的 Log 不覆盖，修正通过新 Log 追加。

写入 Log 后，立即复审 `main.md`：更新当前进展与文件索引，并判断目标与背景或元认知是否需要调整。写完后重新读取 Log 和 `main.md`，确认两者一致。日常命令、临时观察和无决策影响的细节留在工作文件中，不进入长期记忆。

外部知识库的结构和维护规则见 `.ResearchProject/knowledge/README.md`。项目代码、实验、数据和结果目录按实际需要创建，不预设固定结构。
