# 来源与许可证

本模块以独立 `chaoxing` 项目为功能基线，并保留既有超星协议的行为修正。旧仓库只供开发对照，运行时不导入、不启动，也不读取它们的数据目录。

- 字体映射表及字形哈希方法来自 `December172/chaoxing` 的 `dfc99da09890eac24e9c8406b3ce9371e68d2db3`，依据 GPL-3.0-only 保留授权。
- 字体原作者及来源署名：SocialSisterYi；后续编辑 Samueli924；参考项目 `SocialSisterYi/xuexiaoyi-to-xuexitong-tampermonkey-proxy`。
- 本仓库采用 GPL-3.0-only，许可证原文见 `LICENSE`。字体适配、解析器及独立宿主 RPC 入口属于本次模块化改动。
- 未声明许可的早期代码不作为直接导入的运行依赖；接口行为、测试场景及问题修正仅用于对照。

模块通过标准输入输出与框架通信，宿主负责平台 Cookie、网络请求、持久化、模型调用和任务调度。
