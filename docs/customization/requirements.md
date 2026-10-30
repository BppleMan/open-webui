# Customization requirements

Approved product requirements. Private discussion and deployment evidence are maintained outside this public repository.

| ID  | Requirement                                                                            | Prototype |
| --- | -------------------------------------------------------------------------------------- | --------- |
| R01 | 原生 OpenAI、Claude、Gemini 接入                                                       | P01       |
| R02 | 对齐 LibreChat 的上下文窗口与用量面板                                                  | P02       |
| R03 | OI 内置单条消息 usage                                                                  | P03       |
| R04 | 助手回复独立弹窗编辑                                                                   | P04       |
| R05 | 原生模型控件自动生成推理强度滑杆                                                       | P05       |
| R06 | 单轮 cost 累加为会话 total cost，包含 regen                                            | P06       |
| R07 | 新会话从首轮正确，历史不要求完美追溯                                                   | P06       |
| R08 | 导入历史的 total cost 可手动设置                                                       | P07       |
| R09 | RP 默认不压缩上下文，只有用户手动开启或执行                                            | P02       |
| R10 | CPA 承担 OAuth/调度，OI 新增尽量保真接收与发送的 AGY 适配                              | P08       |
| R11 | 定制源码放当前工作区，核实已有 CPA 源码                                                | P09       |
| R12 | 排查使用中登录失效强制跳转的问题                                                       | P10       |
| R13 | 输入草稿在服务器按会话独立保存，每会话一份，换浏览器继续输入                           | P11       |
| R14 | PWA 被杀进程后仍能恢复草稿                                                             | P11       |
| R15 | 需求、原型、Feature 状态三层落地，支持上下文压缩后接手                                 | P12       |
| R16 | 只改造输入草稿，不扩展改造其他 sessionStorage 数据                                     | P11       |
| R17 | 所有模型列表与选择模型的功能统一采用连接一对多模型组织                                 | P13       |
| R18 | 预设多种连接模板，参考 TypingMind 的配置体验                                           | P14       |
| R19 | 查看 TypingMind 公开源码；不足时通过实际效果反推 OI 行为设计                           | P13、P14  |
| R20 | 评估适合长期 RP 跑团的额外优化，提出价值、边界和优先级；具体增项待选择                 | P15       |
| R21 | 近期暂不做无端增加模型上下文的功能，新增界面与元数据功能不得隐式注入请求               | P15、P17  |
| R22 | 战役/角色卡、RP 写作约定与请求预览、骰子规则、主持人知情范围仅作憧憬，现在不实现       | P15       |
| R23 | 核对并复用现有跨端生成与参数调整能力，不重复建设 RP 参数预设；用户通常不调 top_p/top_k | P15、P17  |
| R24 | 修正设置页标题与控件在宽屏下距离过远、对应关系不清的问题                               | P16       |
| R25 | 收敛并安排近期可做功能，明确既有能力、增强范围、暂缓项和验收                           | P17       |
| R26 | 公开 fork；原话与现场证据留在私有目录，公开文档脱敏                                    | P18       |
| R27 | 实际/估算分列，每轮累计快照；fork 继承锚点快照继续累计                                 | P18       |
| R28 | 临时会话草稿仅本机，普通会话服务器同步                                                 | P18       |
| R29 | 分支对照、书签和归档增强纳入本期                                                       | P18       |
| R30 | 执行完整计划，独立预发和 GitOps 发布，通过用户路径验收                                 | P18       |
| R31 | 上下文以输入框内环形按钮为入口，点击弹出详情，不在聊天窗口常驻平铺统计区域             | P19       |
| R32 | 原生接入整合到正式外部连接管理，复用 Open WebUI 原有组件和视觉规范                     | P20       |
| R33 | 接口类型由连接模板承载，从模板新增具体连接，连接下管理多个模型                         | P20       |
| R34 | 设置项使用一致的控件对齐列；修正开关紧贴可变长度标题的布局 | P21 |
| R35 | 聊天模型选择器清楚区分连接分组与模型项，当前模型优先可辨识 | P22 |


R36：继续已批准完整计划；审查结论须先验证、再修复并以回归证据关闭，三层记录持续同步。对应 P23。

实施追踪：R17 / U22 的“所有模型入口”包括能力专用入口的真实出站调用，不能只完成分组选择器。此项为既有需求的落实，不新增 RP 上下文或模型调用。对应 P13、F13。

R37：允许在阶段验证后收口本次任务；完整计划保留，保存可直接续接的工作区状态、证据与剩余验收项。对应 P24、F18。

R38：发布当前阶段源码提交到公开 fork；本次不执行部署，未完成的功能与验收继续保留。对应 P25、F09/F18。

R39：从已保存阶段继续 OI 完整计划，沿用按可验证阶段收口与三层记录同步的约定。对应 P26、F13/F16/F18。

R40：实施后续计划阶段3；保留浏览器直连并统一连接模型身份，加密保存凭据，迁移须预览、版本校验和幂等；通过验收后提交推送并核实CI，本轮不部署。对应P27。


R40 实施追踪：阶段3采用连接身份统一管理，浏览器执行方式保留；调用和迁移验收单独记录，阶段4–8继续待办，正式发布未进行。

R40 阶段3验收已收口：稳定连接/模型身份、浏览器传输、凭据权限及旧配置迁移通过本地与实际CI验收；源码已推送，镜像已发布。本轮未部署。R30完整计划仍按阶段4–8继续。


## R41 / U27 — Continue stage4

继续已批准阶段4；先核对入口和形成可执行验收清单，再按既有范围实施，不重新扩展产品范围。对应P28、F13/F18。

R41实施追踪：已建立22入口独立验收台账，4A基础链路通过本地验证；4B逐入口、4C真实向量库、4DRealtime/CI仍未完成，不将阶段4标为完成。


## U28 / R42 — Complete stage4 in full
本轮完成4B/4C/4D及阶段4集成验收、提交推送与实际CI，不再以4A小批次收口；正式部署仍不在本轮范围。对应P28、F13/F18。


### R42 阶段四实施追踪
阶段四按 M01–M22 全量核对；实际验收发现并修复新版 @ 菜单漏接、管理弹窗旧入口、自动化/历史/图表来源标签、统计重复累计及 Realtime 撤销后继续发送。纯展示身份不赋予调用权限，不修改供应商上下文。实际 HTTP、WS、pgvector 与隔离 PostgreSQL 还原已通过；最终类型增量、构建及 CI/镜像仓库 digest 均已验证。阶段5–8仍未完成，未部署。


## R43 / R44 — 阶段五完整实施与验收范围
R43：完整完成阶段五登录与草稿可靠性，继续三层同步、测试、提交及CI验证。R44：本阶段使用内置浏览器验收，不要求真机PWA测试；关闭重开、独立浏览器存储、离线/502和输入恢复仍需实际操作证据，不声称完成了真机验证。对应P29、F10/F11/F18。


## R45 — 用户要求本轮先收口暂停
完成手头认证与草稿修复的回归、证据保存和本地提交后暂停；第五阶段维持未完成，下一次从剩余验收继续，不提前标记发布。对应P30、F10/F11/F18。

R43续接（2026-10-27）：恢复第五阶段完整验收与CI收口，R44免除真机PWA继续有效。

R43阶段五实测追踪：新增修复撤销后立即重连循环、空白页切换临时模式仍复用普通草稿管理器。临时草稿严格本机；401等待页内恢复，网络/502保留状态并延迟重试。其余验收证据见stage5-acceptance.json，CI待核实，未部署。

R43阶段五收口（2026-10-27）：本地与远端CI验收完成，源码和不可变镜像已发布；详情stage5-release.json。正式环境未切换，阶段6–8仍待实施。


## R46 — 阶段六完整实施
按既定阶段六范围补齐原生协议、推理控件、上下文面板及验收，保持需求→原型→Feature同步。默认不压缩、不裁剪、不额外注入RP上下文，真实供应商仅用合成短文本，正式上线仍等待后续发布门槛。对应P31、F01/F02/F05/F08/F18。


R46实施追踪：七协议、原生历史/中断、推理档位/预算、当前文本与最近工具估算、显式手动压缩已实现并通过本地验收。仅既有CPA网关短文本实测，不冒称官方直连；完整边界见stage6-acceptance.json。CI待收口，未部署。


R46阶段六收口：源码1c68b47235188f0aea4ae9f8ae42bb9a6cb3a670，CI40498639250全部success，注册表digest已核验；stage6-release.json保存OI及CPA伴随版本。阶段七/八未完成，正式环境未部署；供应商证据限制继续保留。


## R47 — 阶段七完整实施
完成既定费用与归档阶段，包含普通JSON导入费用基数、修改/幂等、迟到结算、regen/continue/工具/后台归属，以及归档权限、恶意输入、并发恢复和附件关系。对应P32、F03/F06/F07/F17/F18。

R47实施追踪：JSON导入可填写历史实际/估算费用，默认未知；版本冲突不覆盖输入，重复导入不重置基数。累计快照校正、跨账户原始调用去重、归档完整性及权限/并发/恢复已补齐；最终验收与CI见stage7-acceptance.json、stage7-release.json，正式部署仍留阶段八。

R47阶段七收口：源码9b850fcb85ffb43de4aed2555816c3129665f251、CI40663148496前后端和image全部success，镜像digest独立核验。费用/归档按stage7-acceptance.json方法验收，正式环境未切换；下一步阶段八。


## R48 — 阶段八发布完成
按已批准计划完成源码与发布审计、独立预发、正式数据库副本迁移/恢复、用户路径验收、GitOps切换与运行核验；实施、验证和部署分列。对应P33/F18，保留此前明确的测试边界。


## R49 — 用户要求立即暂停阶段八
用户获知阶段八最终涉及线上替换后明确要求停止。阶段八暂停，不再执行部署或业务数据变更；正式切换需用户后续明确授权。本次仅完成隔离预发与数据副本准备，正式OI仍为原版v0.12.3。对应P34/F18。


## R50 — 完成上线前工作，等待用户微调
恢复阶段8A：完成当前计划中上线前的准备、隔离预发、迁移/恢复、用户路径验收与发布材料。正式实例、线上镜像、路由、业务数据库和CPA生产实例均不切换；正式上线另需明确授权。完成后交给用户继续微调，不把8A完成等同于上线批准。对应P35/F18。


R50 implementation finding: preserve the exact official OpenRouter template during legacy import so reported USD is not misclassified as unknown; discover advertised context/output limits without guessing unavailable metadata.


R50/F16 closure: the shared settings shell must bound all ordinary form panels, while connection/model lists, analytics and large workspace editors retain their width. This completes the existing settings-layout scope.


R50收口：已完成阶段8A的现有上线前实施与验收，普通设置页限宽补齐，生产保持原版。旧模型歧义、实际供应商凭据限制和未来正式切换单独列明；下一步由用户提出微调。


## R51 — Preview refinement and CPA routing correction
Remove redundant model subtitles, limit each connection's expanded model list to five visible rows with internal scrolling, align composer controls, fix expanded context popup positioning/scrolling, improve per-message usage hierarchy, and style dropdown options using existing accessible components. Diagnose the CPA 404 without retry fallback or production changes. R50 remains in force.


## R52 — Distinguish CPA gateway, client protocol and upstream channel
The CPA/AGY template uses native Gemini; use a /v1beta API base for new connections. Its legacy /v1 input compatibility must not rewrite explicit OpenAI Chat/Responses or Anthropic Messages requests. CPA is a gateway and AGY is an upstream channel, independent of the client-facing protocol. A registered endpoint alone is not proof that every upstream/model supports it. R50 still applies.


## R53 — Protocol decoupling and compact connection management
Implement the approved protocol-decoupling plan: unrestricted common protocol selection, connection defaults with model overrides, one shared service root/key/auth, automatic protocol paths, server and browser parity, stable identities and native-history compatibility. Compact enabled-first model lists and right-aligned switches. This supersedes R52's manual /v1beta entry: service addresses no longer require protocol prefixes. R50 production hold remains. Corresponds to P38.

R53 实施与验收收口：协议目录、服务地址解析、模型继承/覆盖、直连原生流、紧凑列表已实现，源码 97b7936939f4c5e7937feecc2ea8c60a24ccfb3b；351 后端/57 前端、类型无新增、构建及 CI 40814642568 通过。隔离预发已验证；OpenRouter Messages/CPA Gemini 各一次合成请求成功。真实 OpenRouter 浏览器 Messages 的专用头未获 CORS 预检放行，明确保留服务方限制，未标为通过。正式环境仍受 R50 保护。

## R54 — 上下文弹层可实际滚动
修复滚轮/触控板无法滚动上下文弹层的问题。内部滚动不得重新测量并清零滚动位置，调整窗口或展开内容后应保持合理位置。验证上下滚动、到达底部、展开明细、键盘与窗口缩放；只更新隔离预发，R50不变。

R54追加要求：用户指出overlay已有成熟方案；上下文入口应复用项目已有Bits UI Popover，由库管理定位、边界、焦点和键盘，避免继续叠加手写定位逻辑。

R54执行约定：一轮相关改动集中完成后，再统一运行浏览器验收、测试、类型检查和构建；按批次提交和更新预发，避免每个小改动分别重复全量验证。


R54/P39收口：改用既有 Bits UI Popover，移除本次共享 Dropdown 临时补丁；上下滚动、边界、方向、空白关闭和键盘焦点已在内置浏览器验证。整批改动统一通过57前端、351后端CI及集成回归、零新增类型诊断和构建；仅更新隔离预发。U49批量验收约定持续适用。


## R55 — Aligned model lists and stable compact editors
Align each expanded model region with its parent connection row. Keep connection/model editor dimensions stable while disclosures expand; scroll the content with fixed header/footer. Use the same single-line control height as existing settings selects. Verify provider-advertised token limits against discovery and stored capabilities; preserve unknown values and manual overrides. Validate the related changes as one batch (U49) and update isolated preview only (R50).

R55容量核验：CPA当前Gemini与OpenAI两个模型目录均未返回所查模型的token容量字段，OI解析无可用数值；缺失不表示模型无限制或免费。保留未知和手动填写，未改CPA。

R55收口：五项批注已整批实施，原生UI滚动和几何实测、57前端/351后端及集成CI通过。模型区左右对齐，编辑框固定尺寸/底栏，单行输入与下拉均1.75rem。仅更新隔离预发，CPA与正式OI保持。


## R56 — Unified message information and compact editing
Unify native message info with per-message usage/cost details, preserving raw provider usage and cumulative snapshot semantics. Use compact detail spacing and right/down disclosure arrows. Reduce reply editor padding without changing structured edit, revision, undo or fork behavior. Composer model triggers show one line; connection identity remains in menus and accessible labels. Batch validation (U49), isolated preview only (R50).

R56收口：原info位置统一用量/费用入口，独立usage框移除；折叠箭头右/下、详情密度、回复编辑留白及composer单行均已验收。保留原始JSON与未知费用口径；57前端/351后端及集成CI通过，类型新增0（5763既有诊断）。只更新隔离预发。


## R57 — Reply-leading usage, stable disclosures and bounded editing
Show essential usage/cost metrics before the reply body, with technical/raw details in the native info entry. This supersedes R56's summary placement. Preserve disclosure state across ledger refresh. Use a fixed-height viewport-bounded context popover and consistent section spacing. Edit body/reasoning through tabs without internal type titles; bound desktop editor width and keep controls/actions within a 430×932 viewport. Batch validation (U49), isolated preview only (R50).

R57落地：八项批注已在P42实现并完成内置浏览器及隔离预发验收，关联F02/F03/F04/F12/F16/F18。正式上线仍受R50限制。


## R58 — Compact wrapping usage and complete info
Keep a separate wrapping metadata row before the reply, with input/output/cache-read/cache-write/current cost always visible, restrained icons/colors, and precise actual/estimated/unknown semantics. Info retains complete Overview/Calls/Raw sibling tabs without nested disclosure. Implemented and verified under U56; see usage-density-acceptance.json.

## R59 — Gemini streaming thought summaries
Request GenerateContent summaries by default, preserving explicit false and existing reasoning effort/budget. Provide independent native model controls on both server and browser connections. Display only provider-returned readable thought text, retain signatures separately, and preserve CPA historical-thought exclusion from RP inputs. Do not add prompts or auxiliary summary-generation calls.
OI implementation and synthetic/HTTP/browser/CI acceptance pass and are deployed to isolated preview. One actual CPA stream still returned only reasoning usage and a signature, without readable summary text; the existing CPA/AGY live path is therefore not fully verified. Do not claim complete live acceptance or fabricate missing text. See thoughts-session-acceptance.json.

## R60 — Clear session recovery
Keep page/draft state during reauthentication. Stop repeat renewal of a credential after a current 401, allow a new login to resume, and retain the network-failure/expiry/revocation distinction. Use a compact viewport-bounded embedded form with clear invalid-session wording. Verified and deployed to isolated preview; authentication duration and security rules are unchanged. R50/U49 remain.

## R61 — Deliberate mobile usage layout and consistent metric styling
Replace natural wrapping on small screens with media-query-driven metric rows; preserve complete amounts, unknown values and all fields. Use consistent colored icons in usage details, and add horizontal padding inside the connection header hover target. UI only; no request, accounting or context changes. R50/U49 remain.


## R62 — Upstream stable synchronization and inline costs
Merge upstream stable v0.12.5 while preserving customized identity, accounting, drafts, authentication, native history and UI contracts. On narrow screens, current and cumulative cost share one deliberate row. Keep full monetary precision, explicit unknowns and R50/U49 boundaries.


2026-10-30：R62收口：稳定上游v0.12.5已合并；本次费用/累计同排，390/430px、1.1缩放下完整金额单行。全量CI与隔离预发验证通过，R50生产边界不变。 源码 `197335833f08ad54adf5fa423215757c22ac70ac`；CI https://github.com/BppleMan/open-webui/actions/runs/40992376952；证据 `upstream-v0125-acceptance.json`。


R63: Independently reproduce and repair CR30-01 through CR30-14. Restore existing privacy, native fidelity, accounting and migration contracts; preserve R50.


2026-10-30：R63/U62收口：14项审查问题已复核、修复并通过回归和隔离预发验证。包括原生状态、临时保存费用、ACL与embedding路由、迁移/删除、历史统计与线性快照读取；R50生产边界保持。 源码 `b96836df3e0faf88be9187957995de729fd97e45`；CI https://github.com/BppleMan/open-webui/actions/runs/41022967897。

## R64 — Second review remediation

Verify and repair CR2-01..11, preserve R50 and existing accounting, identity, privacy and native protocol contracts. Preserve original review evidence. Batch validation and one isolated preview update.
