# Galaxy Fitting Voting System

基于 Flask 的星系拟合结果投票评价系统，与 [galaxy_morphology_mcp](https://github.com/weinaike/galaxy_morphology_mcp) 配套使用。

**galaxy_morphology_mcp** 负责自动化星系拟合流程（生成拟合轮次、分析报告、成分分析），本项目则提供 Web 界面让多位评审人员对拟合结果进行浏览、评价和投票。

支持多数据源，多人协同对 GALFIT 拟合轮次进行质量评价和投票。

## 功能

- **多数据源支持** — 可同时挂载多个拟合结果目录，每个数据源独立展示样本列表和统计
- **拟合轮次浏览** — 查看每个星系的多个拟合轮次，展示对比图、χ²/ν、BIC 等指标
- **投票评价** — 用户对每个样本进行"接受/拒绝"投票，选择最佳轮次并填写理由
- **统计面板** — 按数据源查看投票统计、完成度矩阵、共识轮次
- **S4G 参考数据** — 展示 S4G Table 7 的真实分解成分作为对照
- **弹窗查看** — 日志、成分分析、Agent 分析报告均以弹窗形式展示
- **Docker 部署** — 一键构建和启动

## 项目结构

```
├── app.py              # Flask 主应用，路由和业务逻辑
├── config.py           # 配置解析（多数据源、数据库路径）
├── database.py         # SQLite 初始化和迁移
├── scanner.py          # 扫描拟合结果目录，解析 summary.md
├── start.sh            # Docker 构建和启动脚本（仅容器）
├── start_all.sh        # 联合启动 + KB 服务管理（start/restart/stop/status）
├── Dockerfile          # Docker 镜像定义
├── requirements.txt    # Python 依赖
├── templates/          # Jinja2 模板
│   ├── base.html           # 导航栏和页面骨架
│   ├── sample_list.html    # 样本列表（按数据源分组）
│   ├── sample_detail.html  # 样本详情：轮次卡片 + 投票表单
│   ├── statistics.html     # 投票统计
│   ├── analysis_list.html  # 残差图评价列表
│   ├── analysis_eval.html  # 残差图评价详情
│   └── analysis_stats.html # 残差图评价统计
├── static/
│   ├── style.css           # 暗色主题样式
│   ├── app.js              # 前端交互（弹窗、筛选、表单）
│   ├── s4g_table7.tsv      # S4G 星系分解参考数据
│   └── final_chi2.json     # 最终 χ² 参考值
```

## 快速开始

### 1. 配置数据源

编辑 `start.sh` 中的 `SOURCES` 数组，每个条目格式为 `LABEL:HOST_PATH:CONTAINER_PATH`：

```bash
SOURCES=(
  "gadotti-0513:/home/wnk/code/s4g-p4-galfit/gadotti-0513:/data/gadotti-0513"
  "s4g-cc5:/home/wnk/code/s4g-p4-galfit/filter_mag_lt9_cc5:/data/s4g-cc5"
  "zhongyi-0512:/home/wnk/code/s4g-p4-galfit/galfit_data_0512:/data/zhongyi-0512"
)
```

### 2. 启动

**推荐用 `start_all.sh`** —— 联合启动 visualRAG KB 服务（宿主 GPU 上的 DINOv2 + FAISS）+ 本标注容器，并自动经 `host.docker.internal` 把容器指向 KB 服务（KB 蒸馏/入库按钮才可用）：

```bash
bash start_all.sh        # = start：起 KB 服务（已健康则复用）+ 起容器
```

`start_all.sh` 同时是 visualRAG KB 服务的**后台服务管理器**（PID 文件 `visualrag_server.pid`，`setsid`+`nohup` 完全脱离会话，存活于启动它的 shell）：

| 子命令 | 作用 |
|--------|------|
| `start_all.sh`（或 `start`） | 起 KB 服务（健康则复用）+ 起容器 |
| `start_all.sh restart` | **仅重启 KB 服务**（reload KB / 部署新服务代码；容器不动，经 `host.docker.internal` 自动重连）。清库或改了服务代码后用这个 |
| `start_all.sh stop` | 停 KB 服务 |
| `start_all.sh status` | 查看 KB 健康 + PID |

> KB 服务把 FAISS 索引常驻内存，所以**磁盘上的改动（如 visualRAG 的 `reset_kb.py` 清库、或重建索引）必须 `restart` 后才会被服务加载**。

如果**不需要** visualRAG KB 联动（只跑标注容器），直接用 `start.sh`：

```bash
bash start.sh            # 仅起容器，KB 联动关闭（badge 红、入库按钮 no-op）
```

标注容器默认运行在 `http://127.0.0.1:35091`；visualRAG KB 服务在 `http://127.0.0.1:8765`。

### 3. 使用

1. 登录页输入用户名（首次自动注册）
2. 选择数据源（导航栏下拉切换）
3. 在样本列表中点击"查看"进入详情
4. 浏览各拟合轮次的对比图、指标（χ²/ν、BIC）、日志、成分分析
5. 提交投票评价

## 数据目录结构

数据由 [galaxy_morphology_mcp](https://github.com/weinaike/galaxy_morphology_mcp) 生成，每个数据源目录应遵循以下结构：

```
<data_source>/
├── <galaxy_id>/
│   ├── archives/
│   │   ├── <timestamp_1>/
│   │   │   ├── *_galfit_comparison.png   # 拟合对比图
│   │   │   ├── *_galfit_summary.md       # 拟合摘要（含 χ²/ν、BIC、成分）
│   │   │   ├── fit.log                   # 拟合日志
│   │   │   └── *_component_analysis.md   # 成分分析（可选）
│   │   ├── <timestamp_2>/
│   │   └── ...
│   ├── *_comparison.png                  # 最终对比图（可选）
│   └── *_analysis_report.md              # Agent 分析报告（可选）
```

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `GALFIT_SOURCES` | 多数据源 JSON，如 `{"label":"/path",...}` | 无（使用 GALFIT_BASE_PATH） |
| `GALFIT_BASE_PATH` | 单数据源路径（GALFIT_SOURCES 未设置时生效） | `~/code/galfit_example` |
| `DATABASE` | SQLite 数据库路径 | `./galfit_viewer.db` |
| `ANALYSIS_IMAGE_DIR` | 残差图评价图片目录 | `./analysis_data/images` |

## 数据库

使用 SQLite，表结构：

- **users** — 用户（用户名登录）
- **samples** — 样本（galaxy_id + source 唯一）
- **rounds** — 拟合轮次（含 χ²/ν、BIC、成分 JSON）
- **votes** — 投票记录（用户 + 样本唯一）
- **a_galaxies / a_evaluations** — 残差图评价模块

数据库迁移在启动时自动执行。
