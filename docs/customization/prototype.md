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


## P28 阶段4执行分批

按4A公共入口台账/权限选择边界、4B各入口真实调用与统计、4C独立向量服务上传检索、4DRealtime撤销断流及集成收口推进。每入口分别记录身份、保存重开、权限、出站与账本证据；只有复用组件的入口保持待验。第一批先复现Arena全局候选和失效任务模型回退，不自动重选其他连接。阶段4未完成前不宣称全局目录全部验收通过，不部署生产。

P28 / 4A落地：Arena在读取成员参数/知识前按当前用户权限与连接状态筛选，浏览器成员还需本人活跃会话；主入口和任务回退入口复用同一规则，递归保留调用方权限策略，管理员绕过仍遵循既有配置。显式指定的任务模型失效或非服务器聊天能力时返回422；只有未配置任务覆盖才沿用既有默认。自动化模型搜索包含连接名，不可用模型标签保留原ID。以上仅完成基础链路本地验证，各界面保存重开、实际调用与账本矩阵仍待4B。

P28执行约束更新（R42）：本轮以整个阶段4通过为收口点；逐入口证据、独立pgvector上传检索、Realtime鉴权撤销/断流、模型统计按原始调用去重均必须完成。


### P28 阶段四落地
新版 @ 菜单按连接分组并仅展示聊天能力；管理弹窗默认进入统一连接目录，本地引擎文件管理保留兼容页。回复、自动化、历史和图表显示连接/模型来源；已不可用的历史身份仅用于展示。统计按原始调用去重，旧账目明确保留消息口径，Token 标为已确认小计。Realtime 每条出站命令及空闲期重新校验权限，已确认用量随中断保留。390/430px、1.1倍缩放和键盘选择已实测。


## P29 阶段五登录与草稿可靠性
有效人类会话提前续签，保留原认证时间、MFA及撤销边界；旧标签页和迟到响应不得覆盖新登录，离线/502不能注销。真实失效使用页内恢复，保留输入与回复编辑内容。普通草稿按用户/会话唯一，IndexedDB保护未同步输入，服务器CAS识别冲突；恢复网络/登录后重试。发送确认仅处理对应输入/队列版本，附件上传中断必须有可恢复提示，不能永远停在上传中。临时会话只保存本机。按R44使用内置浏览器的独立origin与关闭重开验证，不以此宣称真机PWA通过。阶段五收口含实际CI，不部署正式环境。


## P30 — 阶段五检查点与续接
本轮收口已实现的认证续签同步、旧登录响应保护、草稿网络恢复重试和中断附件提示；保存本地提交与验收证据。剩余UI矩阵、CI集成和发布验证继续待办。真机PWA按R44不要求，内置浏览器验收仍需完成。

P29续接：从stage5-checkpoint继续剩余浏览器矩阵、隔离CI与发布材料核验，不重复阶段四、不部署生产。

P29落地：会话续签同步到实时连接；真正撤销停止立即重连，传输故障延迟恢复。草稿管理器随用户/普通-临时模式切换，更换后才处理后续编辑。中断上传保留引用并显示重新选择提示。内置浏览器已覆盖冲突双向选择、页内登录+回复编辑、发送中新输入、临时关闭重开和账户隔离；输入法采用真实编辑器上的合成组合事件，未宣称原生输入法或真机测试。

P29阶段五已按记录方法验收，release.json记录实际CI与镜像digest。原型/实现/部署继续分列；部署保持not_deployed。


## P31 — 阶段六执行与证据边界
以stage6-plan.md矩阵推进；原生历史与usage/结束原因分别验证。上下文环形入口按当前分支与连接模型显示，分类估算不混充提供方计数，缓存不重复加进上下文，未知容量不编造百分比。手动压缩必须显式操作和可追溯状态，原文保留。各协议的合成、真实供应商、UI出站、CI证据分列。


P31落地：环形入口保持在输入框，预算和分类明细折叠，手机展开可滚动；工具定义估算单列并计入占用，当前草稿与旧请求实际值分开。未知窗口无伪造百分比，默认不压缩；原生签名在中断时保留但不当完整历史回放。推理控件沿用原组件，真实浏览器档位与预算均核对出站。


P31验收收口：预算、分类与费用使用折叠详情，390/430px展开可滚动，1.1倍缩放和宽屏通过；显式手动压缩后显示摘要状态，关闭后恢复完整历史，费用调用数对应账本。真实与合成边界、CI及镜像见stage6-release.json。


## P32 — 阶段七执行与验收
按stage7-plan.md验证账本边界、费用基数替换、归档完整还原及并发/权限/恶意输入。实际与估算分列；未知不是零；基数编辑携带版本；导入和恢复必须原子且保留调用去重身份。界面复用原生组件，观测/归档元数据不进入模型请求。实现、验证、部署分别记录，正式部署留给阶段八。

P32落地：普通JSON选择后进入原生费用确认弹窗，按会话填写并可明确另开导入批次；费用编辑沿用上下文popup详情，冲突时保留输入。ZIP预览显示消息/调用/修订/附件与明确模型映射，恢复模型显示别名与原始账本身份分离；书签、分支和附件在原有组件中还原。

P32验收收口：内置浏览器确认JSON初始基数、冲突保留输入、替换和重试去重；跨环境ZIP恢复后正文/分支/修订/书签/附件/费用与模型映射保留，当前分支2/全会话4次显示正确。390/430px与桌面、Esc退出通过。原型、实现、验证与部署分别记录；发布见stage7-release.json。


## P33 — 阶段八执行
先核验固定镜像与实际生产配置；加密备份数据库、对象和稳定密钥，以独立DB角色/数据库、Redis实例或命名空间及附件存储恢复预发。关闭复制来的自动任务，真实供应商测试仅用合成短文本。通过迁移、回退恢复和用户路径后，用GitOps停止新生成、等待在途任务结束、执行最终备份和Recreate切换。正式接收新数据后不回退上游镜像；验证真实URL/TLS/auth/socket、模型路由、草稿、usage/账本、附件和监控，保存版本/digest/迁移/验收证据。


## P34 — 阶段八停止边界
遵照R49立即停止推进；只读确认现网镜像及原Pod未变化，结束本任务本机转发。已创建预发工作负载、独立Redis和数据库副本保留现状，不擅自删除或继续迁移。新旧OpenRouter模型歧义尚未得到用户选择；不裁决。续接必须先明确用户希望保留、清理或如何继续，不能沿旧授权自动切换生产。


## P35 — 阶段8A完整验收与微调交接
仅在隔离预发执行剩余真实HTTP/浏览器用户路径、合成短文本供应商验证、附件/向量检索、兼容版本恢复与断流/草稿/账本检查；以已完成阶段的CI为回归基线，发现实际缺陷才修复并补验证。发布覆盖文件及维护开关只作离线材料，不进入正式chart默认值。生产切换、正式维护开关、生产数据迁移与CPA替换不执行。保留模型歧义的显式待选状态；完成后提供可微调的预发版本、验收矩阵和续接入口。


P35 acceptance refinement: provider-reported actual USD and discovered shared-window capacity appear in the existing composer popup; explicit overrides and unresolved legacy aliases remain intact.


P35/F16 refinement: ordinary personal/admin forms share a 48rem content boundary. The trailing controls stay aligned within that column; connection/model management and other workspace panels retain the full content area. A 2560px/1.1x browser check reproduced a 2172px general form before this correction.


P35收口：候选预览保留，17个连接模板、输入框上下文环形popup、原生费用、编辑/书签/归档及共享设置表单均有对应证据。普通表单48rem，复杂工作区保留宽度；不新增隐式RP上下文。正式路由和生产数据未切换。


## P36 — Existing-component refinement
Model names remain primary; upstream ID is secondary only when different. Expanded connection lists scroll within five consistent-height rows, with actions outside the scroll region. Composer utility buttons share a centered hit area and no extra wrapper padding. Context remains anchored to the composer, uses the visible viewport, and scrolls within a bounded panel even when every section is expanded; unknown capacity never implies a percentage. Usage shows readable status and confirmed totals, separates missing data, and folds technical IDs. Dropdown options share a listbox surface with selection, disabled, keyboard and focus behavior. CPA discovery and generation must use the same protocol-specific prefix and preserve reverse-proxy paths, stable IDs and RP headers.


## P37 — CPA protocol guidance
Show /v1beta for CPA/AGY native Gemini. Preserve the /v1 family for OpenAI Chat/Responses and Anthropic Messages. Gateway identity, client protocol and upstream routing are separate concepts. Existing model/connection identities remain stable; registered routes are not advertised as universal model capability.


## P38 — Protocol-first configuration and compact lists
Primary fields: connection name, execution transport, service address, default interface mode and readonly resolved URL, followed by shared credentials. Models show inherited/effective mode and can override. Four common modes are not template-filtered; legacy extensions remain. Provider compatibility is independent from wire format. Enabled-first ordering is stable while toggling; five-row scrolling uses 2rem/2.5rem rows; connection separators removed; form toggles use a fixed right column. Validation and preview must use the same resolver as generation.

P38 落地验收：连接与模型主表单直接展示接口模式/继承来源和只读实际地址；四种常用协议不再被模板裁剪。启用模型优先，开关操作期间位置稳定；桌面2rem、手机2.5rem，最多五行内部滚动，控件靠右。内置浏览器实测390/430/1280/2560及1.1倍缩放，通过深色本地夹具和浅色隔离预发验证。R50 不变。

## P39 — 复用成熟 Popover
上下文环形按钮使用 Bits UI Popover.Trigger，内容通过 Portal/Content 呈现。定位、碰撞避让、焦点与键盘由现有库处理；Content本身是唯一滚动容器，高度由库提供的可用空间CSS变量限制。真实验证顶部/底部、展开明细、窗口变化、Escape关闭和焦点返回。
P39补充验收：不同锚点位置自动选择方向，左右/上下均不越界；只滚动弹层内容；点击空白关闭；Escape关闭并恢复触发按钮焦点。使用项目既有Popover，不新增定位算法。
P39验收组织：完成整批界面和交互调整后统一执行验收；发现新缺陷时修复后补跑对应范围，预发按整批结果更新。


P39落地：由 Bits UI 管理 Portal、方向、碰撞、外部关闭和焦点；Content 单一纵向滚动。390/430/1280/2560、1.1倍缩放、滚动两端和窗口变化本地验证通过；实际预发新会话滚动0→146、已有会话0→511，均到达底部；空白/Escape关闭通过。R50保持。


## P40 — 紧凑模型管理与固定编辑窗
子模型容器左右对齐父连接，以浅色背景/内部缩进区分层级，继续五行内部滚动。连接与模型编辑窗使用固定视口内高度和三段布局（标题、可滚动表单、操作底栏）；折叠项默认保持折叠，展开不改变外框。单行输入统一1.75rem，与SettingsSelect相同；JSON多行保留可编辑高度。容量分列总窗口、输入上限和最大输出，说明上游提供时同步、缺失保留未知、可手动填写；以真实发现结果说明缺失原因，不猜模型参数。

P40验收：894px下模型区域与父连接同宽，全部折叠/展开的弹窗和底栏坐标相同；390/430/1280及390×667短视口通过，1.1缩放下单行输入/下拉为30.796875px。实际预发独立滚动和保存底栏通过。总容量与独立输入上限分列，缺失保持未知。


## P41 — 单一 info Popover 与紧凑回复编辑
消息工具栏原有info位置使用Bits UI Popover，移除正文下独立usage详情块。面板包含本条调用的tokens、实际/估算/未知费用、状态/耗时、截至本轮累计；技术调用信息和原始usage可展开，无账本的历史消息仍能查看原始info，不编造费用。纵向仅Popover滚动，内容紧凑；折叠箭头右、展开下。回复编辑压缩外层/工具栏/正文重复padding，单个文本块不占宽大的类型标签栏，长内容内部滚动且操作底栏可见。聊天输入框模型按钮显示单行模型名，连接副标题隐藏，title/ARIA与选择列表保留来源。

P41本地验收补充：长文撤销后原有textarea高度未回收，已让程序化修订/撤销触发重新测量，并保持正文内部滚动位置。结构化和普通文本路径均验证；无账本旧消息保留所有原始字段，HTML样式字符串按纯文本展示。

P41实际预发：单条回复仅1个info入口，旧inline usage为0；254输入/38输出含30推理/292总计与原始candidate8同时可核对。折叠右、展开下；无账本原始字段保留、失败调用可查看。回复编辑短文高度约213px，8167字符撤销后回到同一高度；390px短屏底栏可见。composer单行，来源仍在title/ARIA和分组中。


## P42 — 顶部概览、独立交互状态与有界编辑器
回复模型名下、正文前显示紧凑用量卡：桌面三列/小屏两列，实际/估算/未知及截至本轮快照分明；复用同一账本派生数据，info只放调用/原始明细。用户展开状态由组件本地状态持有，不从每5秒刷新的调用列表重算。上下文使用既有Bits UI Popover，固定28rem并按可用视口缩小，展开只增加内部滚动；折叠行统一py-2，内容间距放在展开体内部。回复编辑采用固定42rem宽度上限及随视口约束的最小宽度，工具栏在小屏使用短文案和完整ARIA/title；正文/思考/必要工具与JSON以既有Bits UI Tabs组织，保留全部原始结构，不在编辑正文内重复TEXT/THOUGHT标题。

P42编辑安全补充：正文/思考切换与修改保留其他结构块、非文本内容及元数据；无效JSON禁用保存/另存，撤销恢复原结构。模型请求的签名失效策略沿用既有后端。

P42验收：内置浏览器430×932、390×667及桌面1.1缩放通过；上下文展开前后约492.8px不变，折叠行上下8.8px对称，原始usage跨多轮轮询与滚轮仍展开。桌面编辑区约737.2px封顶，小屏工具栏一行；正文/思考/JSON切换、无效JSON阻止保存与撤销均通过。隔离预发证据见usage-editor-acceptance.json。


## P43 — Wrapping metadata and flat complete info
The approved separate metadata row wraps whole metric groups on narrow screens. Input/output/cache-read/cache-write/current cost are always visible; reasoning, total, elapsed and cumulative costs remain available without card chrome. Full info uses sibling Overview/Calls/Raw tabs with stable call-ID source selection. See usage-density-acceptance.json for the verified layout.

## P44 — Independent summary visibility
Gemini GenerateContent and CPA-AGY deep-merge includeThoughts=true by default; native model controls offer Default / Request summaries / Do not request. Explicit false, effort and budget are preserved. Reuse thought:true → reasoning_content → structured reasoning → existing renderer, including saved reload. No historical CPA thought text is added to RP inputs. OI behavior is verified with synthetic server/browser streams; one actual CPA request did not return readable thoughts, so live supplier acceptance remains open.

## P45 — Bounded session recovery
A 30rem-wide frame, height bounded by 34rem and the viewport, embeds the existing login/MFA flow with compact spacing and clear invalid-session wording. Retain the original page and local input; stop repeated renewals of an already-rejected credential. New credentials resume normally; expiry/revocation/security policies are unchanged. Local browser recovery, mobile layout and CI session checks passed.

## P46 — Responsive metric grid and shared icons
At ≤639px use three fixed metric columns: input/output/reasoning, cache-read/cache-write/total, cost plus timing, then cumulative cost plus exceptional status. At ≤359px use two columns with full-width cumulative/status rows. Long monetary values remain readable inside their assigned field. Desktop retains the compact metadata row. Overview and Calls use shared semantic-colored icons with neutral numbers; connection headers gain 0.5rem horizontal inset.
