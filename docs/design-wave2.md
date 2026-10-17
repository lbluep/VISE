# 第二波改进设计索引（生态放大三件套）

> 状态图例：✅ 已定稿（"总体同意"覆盖）｜🔧 详查后修正｜❓ 待拍板｜📝 细节已定、待实现
> 日期：2026-10-17　范围：4.3 全文检索、4.2 Skill 双向桥、2.2 bundle 迁移
> 本文仅为设计与决策索引；实现不提交，由用户自行 commit。

## 树形索引

```
第二波：生态放大三件套
│
├── 1. visectl search（4.3 全文检索） ...................... ✅ 方案已定
│   ├── 1.1 新文件 core/search.py，纯实时扫描、无持久索引 .... ✅（不新增生成状态）
│   ├── 1.2 索引源：manifest(id/name/description/category/tags)
│   │            + commands(name/description/args)
│   │            + README.md 全文（截断 64KB 防护） ......... ✅
│   ├── 1.3 明确不索引 src/ 代码（归属 fast_search，不重叠）.. ✅
│   ├── 1.4 打分：大小写不敏感；精确子串 > 分词；
│   │            command 权重 > README；组序 adapter>command>doc ✅
│   ├── 1.5 --limit 全局跨三组；--json 每条含
│   │            adapter_id/kind/snippet[/command] .......... ✅（B5 补齐）
│   ├── 1.6 退出码：0 命中 / 1 无结果(信息性) / 2 usage ..... ✅（决策 D1）
│   ├── 1.7 纯读：不加锁、不审计 ............................ ✅
│   └── 1.8 MCP 同步加 vise_search(readOnlyHint)，8→9 工具 .. ✅（决策 D7）
│
├── 2. Skill 双向桥 core/skill_bridge.py（4.2） ............ ✅ 方向已定
│   │
│   ├── 2a. skill export <id> --out DIR [--no-scripts]
│   │   ├── 产物：SKILL.md(frontmatter: name/description/
│   │   │   managed-by:vise<ver>) + 能力表 + 退出码提示
│   │   │   + read topic 指引；scripts/ 放 src/*.py ........ ✅
│   │   ├── 诚实标注：分发包不含 baseline/guard/healthcheck .. ✅
│   │   └── 边界：description 截断 ~1024；--out 非空即 exit 2 ✅（B3 补齐）
│   │
│   └── 2b. skill import <dir> [--category C] [--id <id>]
│       ├── 校验：缺 SKILL.md/name → 2；id 冲突 → 2 ......... ✅
│       ├── 知识型(无 scripts/)：生成 list+read 双命令知识
│       │   adapter（topic: info|skill），满足 B2/B3 必填 ... ✅（决策 D2）
│       ├── 脚本型：新增通用 dispatch 模板（每 script 一个
│       │   command 透传 argv + thread/thread_cli/--self-test）
│       │   —— 是"受管接入起点"，命令语义需人工精修 ........ 📝（B2 补齐）
│       ├── python_deps 无法从脚本推断 → 留空 + SKILL.md 注明 📝（B2）
│       ├── establish 用 provider="install"（闭集仅
│       │   install/release），来源信息靠审计事件承载 ....... 🔧 A2 已修正
│       ├── 锁内复用 scaffold.finalize（_finalize 先改名公共）🔧 C2 已修正
│       └── 退出码 0/2/1/75；导入后自动 establish → status clean ✅
│
├── 3. bundle 迁移 core/bundle.py（2.2） ................... ✅ 方向已定
│   │
│   ├── 3a. bundle export --out FILE [--include-audit]
│   │   ├── tar.gz + BUNDLE.json 自校验清单(逐文件 sha256 +
│   │   │   VISE 版本 + created_at)，对齐 provider 哈希契约 . ✅
│   │   ├── 载荷（修正后）：
│   │   │   adapters/ 全量 + vise.release.json
│   │   │   + .vise/{baseline, patches, state}/
│   │   │   + audit/ 仅在 --include-audit 时 ............... 🔧 A3 已修正（D6）
│   │   ├── 排除：snapshots(机器特定)、python/(重建)、
│   │   │   reports、__pycache__ ........................... ✅
│   │   ├── 明确移除：installed.json / tool_paths.json /
│   │   │   .vise/version / registry.json / requirements.txt
│   │   │   （全是机器本地产物或可再生） ................... 🔧 A3 已修正（D6）
│   │   ├── --out 不进 uninstall ledger（导出物是用户文件）.. 📝（B4 补齐）
│   │   ├── 审计记 bundle export/import，不记 --out 路径 .... 📝（B4 对齐隐私边界）
│   │   └── export 短临界区取写锁（防半写 registry） ........ ✅
│   │
│   └── 3b. bundle import FILE [--conflicts ours|theirs] [--dry-run]
│       ├── 【硬冲突修正】不走 upgrade --staged（会撞降级
│       │   闸门 core/baseline.py 版本方向检查和 _verify_staged
│       │   哈希校验） ..................................... 🔧 A1 已修正（D5）
│       ├── fresh 模式（未注册、无基线、无目录）：逐字提取 +
│       │   包内基线随行（无基线则注册后 establish）→ generate ✅（B1）
│       ├── merge 模式（有目录或基线）：classify(old,new,disk)
│       │   自组 per-file 矩阵，冲突→review 行 ............. 🔧 A1+B1 已修正（D5）
│       ├── 冲突默认 skip + 报告，exit 2；--conflicts=theirs
│       │   改写包字节；--dry-run 零写入 .................. ✅（C3）
│       ├── patches copy-if-absent（永不重放）；本地已有
│       │   adapter 的 state 一律不碰 ..................... ✅（B2）
│       ├── merge 前 auto-snapshot 本机 adapter ........... ✅（C2）
│       ├── BUNDLE 版本与当前不一致：警告不阻断（v1） ....... ✅（决策 D4）
│       ├── 包损坏/哈希不符/白名单外路径/缺 manifest：exit 2 ✅
│       ├── 导入后自动 register + establish + release.generate ✅
│       └── 与 setup.sh --upgrade-from 分工：换机交接 vs 同机升级 ✅
│
├── 4. 横切约束 ............................................ ✅
│   ├── 4.1 不新增退出码域（bundle review 未决=2，非对齐 upgrade=1）✅（C3）
│   ├── 4.2 不为 import 放宽 B2/B3 注册校验 ................ ✅（C1）
│   ├── 4.3 不做：远程 registry / 执行第三方代码 / 全局记忆层 ✅（非目标）
│   └── 4.4 Linux 锁语义沿用第一波（75），不新建设计 ....... ✅
│
├── 5. 交付物
│   ├── 新增：core/search.py、skill_bridge.py、bundle.py .... ✅
│   ├── 修改：visectl.py(3 子命令+审计钩子)、scaffold.py
│   │   (_finalize→finalize 公共化)、vise_mcp.py(vise_search) ✅
│   ├── 文档：AGENTS.md 入口表、DESIGN.md 命令清单、
│   │   design-wave2.md（本文件，即三件套契约） ............ ✅
│   ├── TEST-PLAN T14（26 条：search/skill 往返/bundle 往返/
│   │   篡改拒绝/冲突 review+theirs/删除恢复/dry-run/锁 75） ✅
│   └── smoke step 10 ...................................... ✅
│
└── 6. 实施顺序（每步独立可验、可单独放弃） ................ ✅
    ① search → ② skill export → ③ skill import
    → ④ bundle export → ⑤ bundle import（复用 classify，风险最高）
    → ⑥ 文档/T14/smoke step10 → 全量 smoke；全程不提交
```

## 各点细节说明

### 1. search（core/search.py）

- **实时扫描，不建持久索引**：9 个 adapter 规模下全量扫描为毫秒级；持久索引会引入又一个需要加锁、会失效的生成状态。
- 索引三类源：manifest 身份/分类字段；commands 的 name/description/args 名称与描述；README.md 全文（单文件截断前 64 KB）。
- 不索引 `src/` 代码——代码检索是 fast_search 的职责，避免能力重叠。
- 打分：大小写不敏感；精确子串命中优先于分词命中；command 命中权重高于 README 命中；输出分组顺序 adapter > command > doc。
- `--limit N` 为跨三组的全局上限；`--json` 每条含 `adapter_id`、`kind`、匹配片段 `snippet`，command 命中额外带 `command`，供 agent 衔接 `describe`/`run`。
- 退出码：`0` 命中、`1` 无结果（信息性非零，便于 agent 区分"无匹配"）、`2` usage 错误。纯读命令：不加锁、不审计。

### 2. Skill 双向桥（core/skill_bridge.py）

**export**：`visectl skill export <adapter_id> --out DIR [--no-scripts]`

- 生成标准 SKILL.md：frontmatter（`name`=adapter_id、`description`=manifest description（截断约 1024 字符）、`managed-by: vise <版本>`）；正文含能力表（commands+args 从 manifest 渲染）、退出码语义（75=锁冲突可重试、7=未快照拒绝等）、read topic 指引。
- `src/` 下 `.py` 拷入 `scripts/`，`--no-scripts` 关闭。
- 显式注明：分发包不含 baseline/guard/healthcheck，受管版本由 VISE 提供。
- `--out DIR` 已存在且非空 → exit 2，不覆盖。

**import**：`visectl skill import <dir> [--category C] [--id <id>]`

- 缺 SKILL.md / 缺 frontmatter name → exit 2；adapter_id 冲突 → exit 2。
- 知识型（无 scripts/）：生成 `list` + `read` 双命令知识 adapter（topic：`info`/`skill`，SKILL.md 正文作为知识内容），与 reference 模板同构，天然满足 commands 必填与 category 闭集校验。
- 脚本型：新增通用 dispatch 模板（每 `scripts/*.py` 一个 command、argv 透传 + thread + thread_cli + `--self-test` healthcheck）。定位是"受管接入起点"，命令语义需人工精修，不做全自动伪装；python_deps 无法从脚本推断，留空并在 SKILL.md 注明运行期依赖需人工补。
- 【A2 修正】baseline provider 是闭集（仅 `install`/`release`），导入的 establish 用 `provider="install"`（导入机制即 install scan）；"来自 skill 导入"由审计事件 `skill import` 承载。
- 【C2 修正】scaffold 的 `_finalize` 先改名为公共 `finalize()`，skill_bridge 锁内进程内复用（注册 + release 刷新），随后自动 establish baseline，导入即受保护，`adapter status` 为 clean。
- 退出码：`0` / `2` usage / `1` 注册或刷新失败 / `75` 锁超时。

### 3. bundle 迁移（core/bundle.py）

**export**：`visectl bundle export --out FILE [--include-audit]`

- tar.gz，包内 `BUNDLE.json` 自校验清单：逐文件 sha256 + 来源 VISE 版本 + created_at，对齐 release provider "先验哈希再收字节"契约；TarInfo 记录源文件权限位（thread 为 100755，导入时 chmod 还原，防止 bundle 往返产生 git mode 漂移）。
- 【A3 修正后载荷】`adapters/` 全量（含 reference 模板）+ `vise.release.json` + `.vise/` 的 `baseline/`、`patches/`、`state/`（note/task 等持久状态）+ `audit/`（仅 `--include-audit`）。
- 排除：`snapshots/`（大且机器特定，恢复靠 baseline）、`python/`（venv 重建）、`reports/`、`__pycache__`。
- 【A3 移除】`installed.json`、`tool_paths.json`（导出机工具路径，污染新机）、`.vise/version`（本机版本戳）、`registry.json`/`requirements.txt`（register 再生，打包冗余）。
- 【B4】`--out` 写到 VISE_ROOT 之外也不进 uninstall ledger（导出物是用户文件，非安装物）；审计记录 `bundle export`/`bundle import` 事件但不记 `--out` 路径，与第一波"不记参数值"的隐私边界一致。
- export 在短临界区内取写锁，防止读到半写的 registry。

**import**：`visectl bundle import FILE [--conflicts ours|theirs] [--dry-run]`

- 先跑 `bundle_import_check`（BUNDLE.json 逐文件 sha256），损坏即 exit 2，字节先校验后落盘。
- 路径白名单：包内成员只允许落在 `adapters/`、`.vise/baseline/`、`.vise/patches/`、`.vise/state/`（相对路径、归一化），其余一律 exit 2；`vise.release.json` 校验但不落盘（导入后随 registry 本地再生）。
- 【A1】不走 `baseline.upgrade(staged=...)`：该路径有版本方向闸门且要求 staged 字节与 release manifest 完全一致；迁移没有"升级方向"语义。merge 直接调公开函数 `classify(old, new, disk)` 自组 per-file 矩阵，不给 upgrade 加降级后门。
- 逐 adapter 模式判定（adapter_id 取自包内 `vise.adapter.json`）：
  - **fresh**（未注册、无基线、无目录）：adapter 文件逐字提取；包内带 `.vise/baseline/<id>/` 则一并逐字落盘（保留 provenance），缺失则注册后 `establish(provider="install", refresh=True)`；patches 按文件名 copy-if-absent；`.vise/state/<id>/` 仅 fresh 时 copy-if-absent。
  - **merge**（本地目录或基线任一存在）：`old = baseline.files`（无则空表）、`new = BUNDLE.json 哈希（即交付字节）`、`disk = scan` → `classify` 矩阵；apply 前 auto-snapshot 本机 adapter（未注册/无目录则优雅跳过）；update 写包字节、delete 删文件、keep/user 不动；review 默认 skip + 报告（`--conflicts=theirs` 改写包字节；包里没有字节的 vendor 删除型 review 保持未决）；apply 后 `establish(refresh=True)` 自应用后字节重建基线（vendor_sourced 可能变 False，属接受语义）。
  - 包目录名 ≠ 本地目录名（同 id）：报告 + 跳过该 adapter（v1）。
- patches 永不重放：只按文件名 copy-if-absent，同名跳过并报告；本地已存在 adapter 的 `.vise/state/` 一律不碰。
- 包内没有而本地有的 adapter：完全不触碰。
- 收尾一次（非逐 adapter）：`register_adapters.register()` → 逐需 `establish` → `release.generate`（与 skill import 同惯例）。
- 退出码：0 全部落定；2 存在 review 未决（`--conflicts=theirs` 全解则 0）或包损坏/用法错；75 锁冲突。`--dry-run` 纯读：算完整矩阵并打印，不写盘、不 snapshot、不 register。
- 与 `setup.sh --upgrade-from` 的分工：bundle import = 换机/交接；upgrade-from = 同机版本升级；两者底层都复用 per-file reconcile 思想。

### 4. 横切约束

- 不新增退出码域（bundle 落定 0、review 未决/包损坏/用法错 2、锁冲突 75）。
- 不为 skill import 放宽注册校验——知识型/脚本型生成的 manifest 必须自带非空 commands，register 跳过无效 manifest 是正确行为。
- 明确非目标：远程 registry/网络分发、执行第三方 skill 任意代码（import 生成受管 adapter，进入 guard 体系）、跨项目全局记忆层。
- Linux 写锁语义完全沿用第一波（`.vise/.lock`、`VISE_LOCK_TIMEOUT`、75），不重复设计。

## 决策记录

| # | 决策 | 状态 |
| --- | --- | --- |
| D1 | search 无结果 exit 1（信息性非零） | ✅ 已同意 |
| D2 | 知识型 skill → list+read 双命令 adapter | ✅ 已同意 |
| D3 | bundle 默认排除 audit/，`--include-audit` 携带 | ✅ 已同意 |
| D4 | bundle import 版本不一致警告不阻断 | ✅ 已同意 |
| D5 | bundle 双模式导入（fresh 逐字提取 / merge 用 classify 自组矩阵），review 默认 skip + `--conflicts=theirs` 解决，merge 前强制 auto-snapshot | ✅ 已确认并实现 |
| D6 | A3：bundle 载荷只带 baseline/patches/state（剔除 5 类机器产物） | ✅ 已确认（默认排除 audit，`--include-audit` 携带） |
| D7 | B6：MCP 本波只加 vise_search，skill/bundle 留 CLI | ✅ 已确认 |

## 实现状态

| 模块 | 状态 | 说明 |
| --- | --- | --- |
| `core/search.py` + `visectl search` + MCP `vise_search` | ✅ 已实现 | 实时扫描、无持久索引；退出码 0/1/2 |
| `core/skill_bridge.py` export + import | ✅ 已实现 | 知识型（list+read）与脚本型（dispatch）；导入后自动 establish（refresh=True 防陈旧基线） |
| `core/bundle.py` export | ✅ 已实现 | tar.gz + BUNDLE.json 逐文件 sha256；载荷仅 baseline/patches/state(+可选 audit)；导出侧剔除 `__pycache__`/`.git`/`.vise` 运行时残留（对齐 baseline 忽略规则） |
| `core/bundle.py` import | ✅ 已实现 | D5 双模式 reconcile：fresh 逐字提取 / merge classify 矩阵 + pre-merge snapshot；收尾 register→establish→release.generate |
| 文档 / TEST-PLAN T14 / smoke step 10 | ✅ 已完成 | |

## 验证计划（TEST-PLAN T14 要点，实现时展开）

- **search**：命令词命中排在 README 命中之前；`--kind` 过滤；无结果 exit 1；`--json` 含匹配片段；README 超 64 KB 截断不崩。
- **skill**：export 产物含 frontmatter/能力表/scripts；知识型 import 后 `viseinit <id>` exit 0、`run <id> read skill` 可读；脚本型 import 生成命令骨架且不被注册校验跳过；id 冲突 exit 2；导入后 `adapter status` 为 clean；export 到非空目录 exit 2。
- **bundle**：export→import 往返后 `release check` exit 0 且 note/task 数据存活；同树重导入全 keep（rc 0）；删除 adapter 后导入恢复（基线随包重建）；三方不一致 → review + rc 2，`--conflicts=theirs` 后 rc 0；篡改包内文件 → exit 2（哈希拒绝）；白名单外路径/缺 manifest → exit 2；`--dry-run` 零写入；默认不含 audit/snapshots/python，`--include-audit` 才带 audit。
- **锁**：import/bundle 写操作与并发 snapshot 竞争时返回 75。
- smoke step 10 跑自动化子集：search 命中与退出码、skill export→import 往返、bundle 同树往返 + review/theirs/删除恢复/篡改拒绝/dry-run + release check。
