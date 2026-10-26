# Behavioral design

## P01

Native provider connections with lossless structured content and usage.

## P02

Context capacity and categorized usage; no automatic compression or truncation.

## P03

Built-in per-message usage, separate actual/estimated costs and explicit unknowns.

## P04

Modal response editing, revision history and branch preservation.

## P05

Native reasoning controls generated from connection-specific capabilities.

## P06

Generation/call ledger, cumulative snapshots including regeneration and continuation.

## P07

Per-import editable historical cost baselines without double counting.

## P08

CPA/AGY native transport; proxy owns OAuth and scheduling; preserve RP and 429 contracts.

## P09

Public fork and immutable image; deployment managed separately through GitOps.

## P10

Valid-session renewal and authentication recovery preserving user work.

## P11

One durable versioned draft per user/conversation, device-only temporary drafts.

## P12

Traceable requirements, behavioral design and implementation/test/deployment status.

## P13

One global connection-to-model catalog for all model lists and selectors.

## P14

Reusable provider connection templates, discovery and overrides.

## P15

Deferred RP features remain excluded; no automatic memory or extra context injection.

## P16

Compact setting rows, 48rem form maximum, 1rem control gap, 24rem sliders.

## P17

Named branches/comparison, bookmarks/read positions and lossless archive enhancements.

## P18

Approved implementation: actual/estimated split; fork continues anchor totals; temporary drafts stay local; complete validation before GitOps cutover.

See implementation.md for the accepted delivery contract.

## P19 输入框环形上下文入口

对应 R31，覆盖早期 P02 的平铺集成稿。输入框工具栏靠近模型控件/发送按钮处放置一个环形进度按钮；常态只有环，不额外占用聊天内容或输入框外的垂直空间。悬停/键盘聚焦显示简短的已用/容量/比例和实际或估算来源；点击/Enter/Space 打开锚定按钮、优先向上展开的浮层。

浮层包含：上下文容量与剩余、分类占用、缓存、输出预算、调用/分支统计、实际/估算会话费用与未知数，费用明细和导入基数按需展开。屏幕宽度不足时限制为视口减去边距，内容内部滚动；手机打开键盘也不能被遮挡。Esc/外部点击关闭并返回焦点；未知容量使用静态空环和明确未知提示，不假装为0%或加载动画。

草稿正常保存状态用工具栏内轻量图标/提示承载；冲突、离线或保存失败必须保留可操作的恢复入口。单条回复 usage 仍在回复处，与会话上下文浮层分工。所有操作只读已有统计，不额外调用模型或修改上下文。

## P20 连接模板与原生管理整合

对应 R32/R33。外部连接设置使用现有设置外壳，仅展示一套连接列表。顶部轻量“新增连接”打开模板选择弹窗；选模板后进入同一种连接编辑弹窗。OpenAI、Claude、Gemini、CPA/AGY、Ollama 及网关等均是可重复使用的模板，真正出站协议仍由对应原生适配器执行。

连接行沿用原版小字号、弱分隔、原生开关、图标按钮和留白；名称为主、模板/地址为次，模型数量用于展开本连接模型。展开列表在连接下缩进，各模型独立配置/启用。价格、推理和高级协议选项按需展开，不以大块边框或技术字段占满首屏。Modal、Switch、SensitiveInput、SettingsSelect、Tooltip、AdminSettingSection 复用上游组件。禁止重复展示旧版 OpenAI/Ollama 管理区和另一套新管理区。

U20 优先于 U19 的“类型同级”展示建议：日常一级是具体连接，模板仅用于创建和标识。所有其他模型选择位置继续按具体连接分组，不按供应商品牌混并。

### P20 创建与持久化细节

- 模板是可复用的创建默认值：协议、根地址、认证方式、专用表单字段。现有连接使用已保存配置；模板升级不覆盖地址、凭据、模型、参数或价格。
- 创建弹窗先选模板，再填写连接名称和凭据；同一模板允许多个连接。新增成功立即展开该连接，随后刷新模型或手动添加。Azure 使用部署名称手动添加模型，API 版本是显式字段。
- 日常列表的一级始终是具体连接。模型行在其连接下展开，刷新/添加模型位于模型区域。手机长连接名称允许换行，连接行保留编辑和启用控件。
- 搜索同时匹配连接、模板、模型名称和上游 ID。搜索时展开匹配模型；模型数量表示本连接总数。
- 刷新发现的新模型默认未启用；已存在模型的自定义名称、价格、参数与能力覆盖保留。分页完整读取后才能更新可用性；中途失败保留原目录。
- 接口模式保留在高级选项，模板承载默认协议；Claude/Gemini 分别使用原生适配器。创建/编辑本身不调用生成接口。


## P21 设置表单行修正

对应 R34，覆盖 P16 中将1rem理解为紧贴标题的错误实现。保留表单最大48rem，以整行组织标题、说明与控件；标题和说明归于左侧，控件在统一右侧列对齐。行间使用清晰的节奏和轻分隔，控件至少保留1rem间隔。长标题换行而不推挤开关；展开滑杆的容器也限制24rem，字体等长内容字段保留整行。复用原有Switch和设置外壳，保留即时保存、继承状态、快捷键和焦点。

## P22 连接与模型视觉层级

对应 R35。选择器以明确的连接分组行承载连接图标、连接标识、名称和模型数；其下模型缩进并有轻量层级引导线，模型项保持原生头像、选择勾和操作。分组不是可选模型；键盘移动只遍历模型。省去模型行重复的外部连接标记。当前选择优先展示模型名称，连接作为次要信息或完整提示，避免长连接名称挤掉模型名。搜索连接或模型仍保留分组和唯一绑定，不改变实际选中的模型或请求内容。


### P22 键盘与搜索细节

搜索后焦点从第一条匹配模型开始；无搜索重新打开时定位当前模型。上下方向键只遍历模型项，Enter 选择当前高亮模型，中文输入法组合中不触发选择；Esc 关闭并返回触发按钮。比较模式保留各连接同名模型的独立选中状态。连接标题使用命名分组，搜索框向辅助技术暴露当前高亮项。


## P23 审查修复与可靠性约束

对应 R36，细化 P01/P03/P04/P11/P13 的既定行为。目录读取与生成使用相同模型授权边界；只向可管理者返回完整配置。编辑正文或另存编辑副本后不得回放过时原生正文；未修改内容的原生签名按实际模型绑定回放，预设名称不决定路由。工具续轮和重复规范化保留原生结构，临时会话只在本次请求内保有原生数据。所有已确认用量在正常、断流、错误和取消时均进入同一调用账本，截断、阻断与正常完成分开呈现。草稿解决冲突和发送确认仅清理已处理的本机版本，保留并发未处理输入。具体调查、复现和验证结果仅放 Feature 层。


### P11 既有消息队列的草稿保护

排队中的输入仍是未提交内容。沿用现有队列界面，将队列和当前编辑内容保存在同一份会话草稿内；普通会话同步服务器，临时会话只在本机恢复。队列条目拥有稳定的请求、用户消息与回复消息标识，重试和跨浏览器接续复用这些标识。确认某条已被服务端接受时只移除该条对应版本，保留当前输入和其他排队内容。原生模型绑定、手动选择的工具与控件随入队内容保存；不得隐式添加提示词或调用。编辑/删除队列项也必须先持久化，错误与未知接受状态保留恢复入口。此为 R13/R14/R28 的可靠性细化，不新增后台自动生成系统。

### U22 implementation refinement — P04 / P17 / P06

Reply editing reuses the upstream DiffEditorModal for original/current and historical comparisons. Loading a revision changes only the open editor until saved; undo resets this edit session. Revision persistence and native replay invalidation share the message write transaction, including the single-message API. Save As Copy records its source message so a later fork can locate the original cost boundary. An imported/inherited history with no generation can have a cost anchor without inventing a model call.

The navigation bar opens a Reading and Branches modal with bookmarks/position and branch/comparison tabs. Bookmarks point to original messages, names attach to branch message IDs, and choosing a branch preserves other candidates. Reading state uses per-user chat annotations with revision conflicts; it never enters the inference payload. Mobile layouts place the name field and actions on separate rows. Ordinary sends and queued sends now use the same durable submission identities.

### U22 archive behavior — R27 / R29

Conversation menu offers an OI ZIP archive alongside legacy JSON/text/PDF exports. Personal Data settings provide a preview and restore flow with the shared connection-grouped model options. ZIP includes the complete normalized message tree, revisions, user reading annotations, model references without credentials, canonical generations/calls/snapshots/baselines, original accessible file bytes and extracted text. File digests are checked before import; external/unavailable references remain explicit in preview. Vector retrieval indexes are not included and require attachment processing if used.

Restoration creates a new conversation, keeps private ledger lineage per user and deduplicates original call identity across repeated imports. A retried import ID returns the same restored conversation. Imported views inherit a fixed archived cost boundary and append their future calls separately. No model inference is issued by archive/reading operations. Message, file and billing DB writes commit together; failed restoration rolls back and removes only newly created storage objects. Plain legacy JSON imports start with an unknown imported-history baseline that can be replaced in the cost panel.


### P13 能力专用入口的调用契约（R17 / U22 延续）

文档 Embedding、重排、STT、TTS、图像生成/编辑和实时语音选择连接中的稳定模型绑定，按已确认能力过滤。保存的是绑定 ID，出站时使用该连接的地址、凭据、原始模型 ID 和参数；同名模型不能串用。运行时再次检查权限，TTS 缓存命中也不能绕过检查。实时语音与其内置转录必须选择同一连接，因为转录由该实时会话的服务端执行。已有专用服务配置仍可读取，后续迁移不能静默替换连接。

每个实际能力调用分别归账；Embedding 的输出 token 不适用，语音二进制和时长用量没有已配置价格时保持未知。音频与图像 token 若使用独立单价，必须有显式价格与分类计数，不能用普通文本单价冒充。实时回复按 response ID 去重、转录按 item ID 独立归账，断流保留未知/不完整状态。


## P24 阶段收口与下一次续接

对应 R37 / U23。当前阶段结束仅表示这批改动已完成指定本地验证，不表示整个定制计划完成或正式上线。Feature 分别记录实现、验证、部署；下一次首先阅读接手入口、最新 Feature 和证据，再从尚未验证的项目继续。保留源码工作区及私有补丁快照，不以重复实现历史修复消耗验收时间。


## P25 当前阶段源码提交

对应 R38 / U24。本次将已记录的阶段代码、合成测试和脱敏文档提交并推送到 `oi/main`，不触发或执行集群部署。私有记录、凭据、备份与本机运行数据不进入仓库。源码发布、CI 构建、完整功能验收和正式部署分别记录；推送成功不表示完整计划完成。


## P26 能力入口与不可用选择的阶段收尾

对应 R17/R24/R35/R39。普通能力设置表单整体限制48rem，模型下拉展开仍按连接分组，收起后显示“模型 · 连接”，避免同名模型无法区分。聊天候选、默认继承和Arena成员排除已声明只支持Embedding/重排/语音/图像等专用能力的模型；明确chat=true的混合能力模型可聊天，能力未知的历史聊天模型仍允许手工选择。端点能力来自连接绑定，不能把工作区“允许使用图像生成工具”等开关当成模型原生图像端点能力。

已保存、URL或草稿中的不可用绑定保留原ID并提示重选，不以默认模型静默替换；无有效聊天模型时发送被拦截并保留输入。服务端同样拒绝专用模型的聊天请求，不发出供应商请求。原生/混合检索中的HTTP错误（含429）直接传递，不转成另一条模型或向量回退调用。检索批次及重排继承原生成身份，跨并发分支保持隔离。


## P27 阶段3：连接身份与迁移

连接有server/browser传输类型，模型绑定ID稳定，浏览器直连仍在浏览器执行。统一管理标明执行位置；模型发现、请求、启停和费用引用真实绑定，不使用数组下标或显示名称路由。浏览器配置凭据统一加密，只有连接所有者可经no-store接口读取；目录与导出均脱敏。旧直连、OpenAI/Ollama与外部检索/语音/图像配置通过预览、来源版本校验、幂等事务迁移；重复/歧义配置列为待处理，既有覆盖保留。迁移至服务器个人连接先检查可达性，成功后创建新连接与明确模型映射，保留原直连。无浏览器会话的后台任务明确拒绝browser连接。

阶段3通过回归与用户路径后提交推送并核实CI；仅源码和镜像流水线，本轮不切换集群。阶段4–8按已批准后续顺序继续，仍需全入口、认证/PWA、协议、数据及发布验收。


P27 落地：个人/管理连接复用同一组件，标注浏览器或服务器；同名模型分组显示。旧配置先预览后应用，重读预览处理409；重复来源需逐项选择。移除连接可恢复且恢复后停用，不删除模型或账本。显式复制到服务器先验证可达，保留原直连并返回模型映射。浏览器需要活跃发起会话，服务器独立执行入口拒绝直连；关闭浏览器后保留已确认usage、未知费用。旧专有或不兼容引擎保留兼容入口并提示手工处理，不猜协议。

P27 验证状态：同名A/B/server调用、改名重排/移除A/草稿重开、权限、429、断流usage和PG并发迁移还原通过；旧URL含凭据时不回显并保持手工处理。实现、CI镜像发布、正式部署分别记录，后者未执行。
