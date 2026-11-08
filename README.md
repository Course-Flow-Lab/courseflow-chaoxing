# Courseflow 超星模块

独立的超星学习通平台模块，以 `December172/chaoxing` 为功能基线，保留播放窗口、任务完成判断、字体解码和作答恢复修正。模块不导入旧仓库，也不包含旧 Web UI、数据库或账号文件。

宿主为 [courseflow-framework](https://github.com/Course-Flow-Lab/courseflow-framework) ≥ 0.1.5，模块 API 为 1，模块编号为 `cx`。通过标准输入输出交换 NDJSON JSON-RPC；网络、Cookie、加密状态、任务队列、题库和模型调用由框架管理。

## 安装

需要 Python ≥ 3.11。把依赖环境放在源码和已安装模块之外；以下以 uv 为例，系统已有兼容 Python 时可改用 `python -m venv` 和 pip。

```sh
uv venv "$HOME/.local/share/courseflow-module-runtimes/cx-0.1.0" --python 3.13
uv pip install --python "$HOME/.local/share/courseflow-module-runtimes/cx-0.1.0/bin/python" -r requirements.txt
chmod +x main.py
```

在框架目录中操作。已有本机服务时，先停止服务，CLI 使用同一个外部数据目录；不要同时启动两个使用相同数据目录的实例。

```sh
export CF_DATA_DIR="$HOME/.local/share/courseflow-local/data"
npm run cli -- module add /绝对路径/courseflow-chaoxing
npm run cli -- module config cx "{\"source\":\"live\",\"python\":\"$HOME/.local/share/courseflow-module-runtimes/cx-0.1.0/bin/python\"}"
npm run cli -- module enable cx
```

启动框架后，在「平台连接」选择超星扫码或账号密码登录。密码仅用于当次登录交换，不保存到模块状态。额外手机验证、验证码和人脸确认转由用户在官方页面处理。

| 模块配置 | 默认值 | 说明 |
| --- | --- | --- |
| `source` | `live` | `live` 使用平台接口；`fixture` 是完全离线的合成课程 |
| `python` | 系统 Python | 依赖环境中解释器的绝对路径，入口在导入依赖前切换解释器 |
| `finish_seekable` | `false` | 仅对平台明确返回 `fastforward=true` 的音视频使用拖动和完成上报；其他视频按真实时间运行 |

源码目录不需要构建。安装时只复制清单、入口、`cx/`、许可和依赖清单。入口关闭 Python 字节码缓存，避免运行后改变安装指纹。

## 能力

- 课程文件夹、班级与章节目录；分页短调用避免一次扫描整门课超时。
- 视频、音频、文档、阅读和空章节；直播观看记录与完成状态回读。
- 章节测验的单选、多选、判断、填空、简答；原有作答保留、人工补答、草稿、确认交卷和结果回读。
- 平台字体解码、康熙部首规范化、选项辅助文本解析和题目图片。无法解码时暂停，不把乱码发送给模型。
- 已确认答案由宿主题库复用；模型服务由框架统一配置。外部题库协议在 [courseflow-question-bank](https://github.com/Course-Flow-Lab/courseflow-question-bank) 中提供适配接口，模块不存放供应商密钥。
- 按用户加密保存目录、原始任务点和作答状态；暂停或重启后不会累计离线停留时间。未知开卷／交卷不会自动重发。

完整对照见 [功能迁移说明](docs/功能迁移.md)。学习时长到达和 HTTP 成功均不等同于平台确认完成。直播无可靠时长、章节未开放、平台未确认完成时保留原因供人工核对。

## 离线验证

```sh
PYTHONDONTWRITEBYTECODE=1 /依赖环境/bin/python -m unittest discover -s tests -v
# 在相邻框架目录运行真实子进程契约；仍仅使用合成账号与 fixture
CF_CX_PYTHON=/依赖环境/bin/python node --test test/chaoxing.test.ts
```

Python 测试使用假宿主与合成 HTML／字体；框架契约测试使用 `source=fixture`，不连接超星、不使用真实账号、不调用模型或付费题库。实际平台接口可用性尚待用户自行验收。

## 许可

本仓库采用 GPL-3.0-only。原项目来源、版本和字体数据署名见 [NOTICE.md](NOTICE.md)，许可原文见 [LICENSE](LICENSE)。
