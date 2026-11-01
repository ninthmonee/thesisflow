# 研衡 · ThesisFlow：品牌与项目命名

本项目的中文名为 **研衡**，英文名为 **ThesisFlow**，组合写法为 **研衡 · ThesisFlow**。

“研”表示研究与证据，“衡”表示比较、权衡与纪律；ThesisFlow 表达研究假设从提出、验证到决策与复盘的过程。产品描述为：**连接研究、证据与个人决策的投研工作台**。

## 对外名称

| 场景 | 使用名称 |
| --- | --- |
| 中文产品名称 | 研衡 |
| 英文产品名称 | ThesisFlow |
| 项目标题、介绍和完整署名 | 研衡 · ThesisFlow |
| GitHub 仓库与项目 slug | `thesisflow` |
| 项目仓库 | [ninthmonee/thesisflow](https://github.com/ninthmonee/thesisflow) |
| 简短说明 | 连接研究、证据与个人决策的投研工作台 |

在新写的界面文案、项目介绍与开发文档中使用上述名称。既有实施记录、验收日期和原始引用保留当时的名称；名称变化不重写项目历史。

## 研究引擎与兼容名称

研衡 · ThesisFlow 基于 [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) 持续扩展。TradingAgents 指上游研究框架与本项目沿用的内部引擎，不作为研衡的新产品名称。

Python 包及导入路径 `tradingagents`、既有 `tradingagents` / `tradingagents-web` / `tradingagents-web-worker` 命令、`TRADINGAGENTS_*` 环境变量、历史配置与存储标识继续按实际实现使用。项目品牌改名不要求用户迁移账号数据、模型凭据或研究记录，也不通过全局替换改变内部接口。

新增的 `thesisflow`、`thesisflow-web` 和 `thesisflow-web-worker` 命令别名使用相同研究引擎；重新安装后端包后可用。

上游链接、论文引用、作者署名、许可证和原有技术名称保留原文。来源与许可见 [README](../README.md#来源与许可)、[NOTICE](../NOTICE) 和 [LICENSE](../LICENSE)。
