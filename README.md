# 算疏智合

**版本：v0.1.0**  
**类型：公司级组织 Skill 生成工具**

算疏智合是一款面向本地运行场景的公司级组织 Skill 生成工具。它可以根据公司资料、组织结构、岗位信息与组织成员资料，生成一套可用于战略分析、组织模拟、对外口径、执行建议和 Agent Skill 集成的 Markdown 文件。

本正式运行包提供图形界面、命令行和本地 API 三种入口；不包含真实 API Key、历史生成结果、测试入口、演示入口或开发期评测脚本。

---

## 1. 项目定位

算疏智合用于把分散的公司资料整理为结构化组织 Skill。生成结果通常包括：

- 公司总入口 Skill
- 老板 / 决策层 Skill
- 员工集合体 Skill
- 公司行为准则
- 组织成员清单
- 战略愿景
- 职业路径
- Agent 工具导出包

这些文件可以作为企业知识助手、组织模拟、角色化问答、战略讨论和内部协作辅助的基础材料。

---

## 2. 主要功能

### 2.1 公司级 Skill 生成

根据公司名称、行业、资料文件、组织结构和模型配置生成完整组织 Skill 文件。

### 2.2 图形界面操作

提供基于 PySide6 的桌面 GUI，覆盖模型配置、资料上传、组织结构选择、组织成员插入、批量成员目录导入、生成确认和结果查看等流程。

### 2.3 多模型接入

支持两类模型调用方式：

- Cloud / OpenAI 兼容接口
- Local / 本地 OpenAI 兼容模型服务

只要服务提供兼容的 Chat Completions 接口，即可填写 API Base、模型名和 API Key 使用。

### 2.4 人员资料录入

支持为组织成员录入多份资料，格式包括：

- `.md`
- `.txt`
- `.json`
- `.yaml` / `.yml`
- `.docx`
- `.pdf`

推荐优先使用 Markdown，因为结构清晰、读取稳定、便于后续维护。

### 2.5 批量成员目录导入

支持以“一个成员一个文件夹”的方式批量导入人员资料：

```text
employee_docs/
├─ 张三/
│  ├─ 01_基本信息.md
│  ├─ 02_项目经历.md
│  └─ 03_访谈记录.md
└─ 李四/
   ├─ 01_基本信息.md
   └─ 02_工作风格.md
```

每个一级子文件夹会被识别为一个成员；子文件夹内的多份资料会作为该成员的资料包导入。根目录下的散文件不会被自动合并为成员。

### 2.6 Agent Skill 导出

生成完成后，系统会同步导出适配 Agent 工具使用的 Skill 包，默认输出到：

```text
agent_exports/<company_slug>/
```

---

## 3. 运行环境

建议环境：

- Windows 10 / Windows 11
- Python 3.10 或更高版本
- 可访问的 Cloud 模型接口，或本地 OpenAI 兼容模型服务

建议使用 Python 虚拟环境安装依赖，避免和系统 Python 或其他项目依赖冲突。

---

## 4. 安装依赖

在项目根目录运行：

```bat
install_deps.bat
```

或手动执行：

```bash
pip install -r requirements.txt
```

如需使用虚拟环境，可执行：

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

---

## 5. 启动方式

### 5.1 启动图形界面

```bat
start_gui.bat
```

或：

```bash
python gui.py
```

### 5.2 启动本地 API

```bat
start_api.bat
```

或：

```bash
uvicorn app:app --host 0.0.0.0 --port 8000
```

### 5.3 命令行生成

可通过 `run.py` 使用请求 JSON 调用生成流程：

```bash
python run.py --request-file <request.json>
```
---

## 6. 模型配置

配置文件为：

```text
config.json
```

示例文件为：

```text
config.example.json
```

Cloud 模式示例：

```json
{
  "model_type": "cloud",
  "online": {
    "api_key": "",
    "api_base": "https://api.example.com/v1",
    "model": "your-model-name"
  }
}
```

Local 模式示例：

```json
{
  "model_type": "local",
  "local": {
    "host": "http://localhost:11434",
    "model_name": "your-local-model"
  }
}
```

---

## 7. 输入资料建议

推荐准备以下资料：

```text
company_docs/
├─ 公司介绍.md
├─ 业务线说明.md
├─ 战略方向.md
├─ 行为准则.md
└─ 组织结构.md
```

如果需要插入组织成员，可按成员分目录整理：

```text
employee_docs/
├─ 张三/
│  ├─ 01_基本信息.md
│  ├─ 02_项目经历.md
│  └─ 03_工作风格.md
└─ 李四/
   ├─ 01_基本信息.md
   └─ 02_访谈记录.md
```

资料越结构化，生成结果越稳定。建议每份资料尽量包含清晰标题、时间范围、来源说明和适用边界。

---

## 8. 输出结果

生成结果默认位于：

```text
companies/<company_slug>/
```

常见输出文件包括：

```text
SKILL.md
boss-skill.md
employee-collective-skill.md
company-code.md
organization-members.md
strategy-vision.md
career-path.md
generation-summary.md
generation-request.json
```

Agent 导出目录位于：

```text
agent_exports/<company_slug>/
```

---

## 9. 目录结构

```text
算疏智合-v0.1.0/
├─ gui.py                    # 图形界面入口
├─ run.py                    # 命令行生成入口
├─ app.py                    # 本地 API 入口
├─ project_utils.py          # 文件解析、模型检测、资料摘要等工具
├─ agent_exporter.py         # Agent Skill 导出器
├─ config.json               # 本地配置
├─ config.example.json       # 配置示例
├─ requirements.txt          # 依赖列表
├─ install_deps.bat          # 安装依赖
├─ start_gui.bat             # 启动 GUI
├─ start_api.bat             # 启动 API
├─ prompts/                  # 生成提示词协议
├─ ui/                       # GUI 支撑模块
├─ template_library/         # 模板库
├─ companies/                # 生成结果目录
├─ agent_exports/            # Agent 导出目录
├─ uploads/                  # 临时上传目录
└─ projects/                 # GUI 项目保存目录
```

---

## 10. 安全说明

- 正式包不包含真实 API Key。
- 不建议把 `config.json`、日志或生成请求文件直接共享给第三方。
- `generation-request.json` 可能包含公司资料路径、模型配置和生成参数，分享前应检查是否需要脱敏。
- 本地 API 默认用于本机或内网环境，不建议直接暴露到公网。
- 若处理敏感公司资料，请确认模型服务、网络环境和数据存储位置符合组织要求。

---

## 11. 常见问题

### GUI 启动失败

先确认已安装依赖：

```bat
install_deps.bat
```

然后重新运行：

```bat
start_gui.bat
```

### 模型连接失败

检查：

- API Base 是否正确
- API Key 是否为空或失效
- 模型名是否正确
- 本地模型服务是否已经启动
- 网络或代理是否阻断访问

### 批量成员目录一直扫描

请确认选择的是成员资料根目录，而不是项目根目录、桌面、下载目录或包含 `.venv` 的开发目录。推荐结构是“一个成员一个子文件夹”。

### 同名公司反复生成时结果混乱

GUI 生成确认页默认提供“生成前清理同名旧产物”选项。建议保持勾选，避免旧输出结果干扰。

---

## 12. 版本说明

当前版本为 `v0.1.0`，定位为正式运行源码包。该版本保留 GUI、CLI、本地 API、组织成员导入和 Agent 导出等核心能力，移除了开发期测试入口、演示入口、评测脚本和历史样例产物。

---

## 13. 免责声明

算疏智合生成的内容依赖输入资料和模型输出，不应被视为法律、财务、人事或商业决策的唯一依据。涉及真实公司治理、合规、投资、裁员、商业合作等高风险事项时，请结合专业人员审核和真实业务数据判断。
