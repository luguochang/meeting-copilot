# 平台形态、打包分发与应用商店合规规划

> 日期：2026-06-19  
> 阶段：产品路线 / 技术架构预研  
> 结论：PC 端应采用“共享核心 + 平台适配器 + 分平台打包”的架构；移动端先作为后续 companion app 规划，不进入当前 MVP 主链路。

说明：本文是产品和技术规划预研，不构成法律意见。真正进入中国大陆移动端上架、订阅商业化、企业客户交付或大规模云同步前，需要按主体、服务器、数据流、商业模式和目标市场做专项合规确认。

## 1. 总结结论

### 1.1 PC 端是否一套代码导出 Windows/Mac 安装包

不能理解成“一套代码点一下就完美兼容 Windows 和 Mac”。更准确的说法是：

```text
一套核心代码
一套主要 UI 代码
两套平台适配层
两套打包/签名/发布流水线
```

可以共享：

- Meeting Copilot core：EvidenceSpan、状态机、建议卡片、LLM scheduler、报告生成。
- 前端 UI：如果采用 Tauri/Electron/Flutter，大部分页面和交互可复用。
- LLM gateway：OpenAI-compatible 协议层可复用。
- ASR provider 抽象：FunASR、sherpa-onnx 的接口抽象可复用。
- 本地存储 schema、会话 JSON、导出格式。
- 评测、demo pipeline、质量门禁。

必须分平台处理：

- 麦克风权限。
- 系统音频采集。
- 屏幕录制/系统音频权限提示。
- 托盘、窗口、自动启动、快捷键。
- ASR worker 打包方式和依赖路径。
- 模型下载/缓存目录。
- 安装包格式。
- 代码签名、notarization、SmartScreen。
- 自动更新。

因此后续代码规划应是：

```text
shared core + shared UI + platform adapters
```

不是：

```text
Windows 一套业务代码，Mac 一套业务代码
```

也不是：

```text
完全无平台差异的一套包
```

### 1.2 当前最推荐的 PC 技术路线

当前阶段建议：

```text
Local Web MVP
  -> 先验证核心 Copilot 价值

Tauri Desktop Shell
  -> 后续接 macOS/Windows 桌面壳
  -> 平台音频采集做 adapter
```

原因：

- Tauri 可复用 Web UI，桌面包体通常比 Electron 轻。
- 现有核心逻辑已经偏向 local worker / CLI / JSON pipeline，适合被桌面壳调用。
- ASR 依赖重，最好作为 sidecar worker，不塞进前端进程。
- 后续可用 GitHub Actions 或分平台 CI 分别构建 macOS/Windows。

备选：

- Electron：生态成熟，打包和跨平台资料多，但包体更大、资源占用更高。
- Flutter：桌面和移动共用 UI 的潜力更好，但与当前 Web MVP / Python worker 结合的开发路径不如 Tauri 直接。
- Swift + Windows native：体验最好但开发成本最高，不适合作为当前阶段。

当前建议不把客户端技术栈一次性锁死，但应锁死架构边界：

```text
core 不依赖 Tauri/Electron/Flutter
desktop 只作为 shell + platform adapter
ASR/LLM/state/suggestion/report 都在 core/worker 层
```

## 2. PC 端平台差异

### 2.1 macOS

分发方式：

- MVP/内测优先：官网或私有链接下载 `.dmg` / `.zip` / `.pkg`。
- 后续可选：Mac App Store。

主要要求：

- 加入 Apple Developer Program 才能获得正式分发所需的证书能力。
- Developer ID 主要用于 Mac App Store 外分发的软件签名。
- Mac App Store 是 App Store Connect 审核/上架路径，不能和 Developer ID 外部分发签名链路混为一谈。
- Apple Developer Program 官方费用是 99 USD/年，或按地区本币计费。
- macOS 10.15 之后，使用 Developer ID 分发的软件通常需要 notarization，Apple 官方文档说明 Developer ID 分发的软件需 notarize。
- App Store 外分发没有 App Review，但 notarization 会做恶意软件和签名检查，不等于功能审核。

关键风险：

- 麦克风权限和系统音频权限必须清晰说明。
- 系统音频采集在 macOS 上比普通麦克风录音复杂，可能涉及 ScreenCaptureKit、CoreAudio、系统权限或虚拟音频设备。
- 如果走 Mac App Store，sandbox 约束可能影响系统音频采集、后台进程、模型下载和本地文件访问。
- 如果走 App Store 外分发，要处理 Gatekeeper、Developer ID、notarization、更新器和用户信任。

当前建议：

```text
Mac MVP 优先走 App Store 外分发：
  Apple Developer Program
  Developer ID signing
  notarization
  .dmg 或 .pkg
```

Mac App Store 放到后续评估，因为会议录音/系统音频采集和本地 ASR worker 可能与 sandbox 产生冲突。

### 2.2 Windows

分发方式：

- MVP/内测优先：官网或私有链接下载 `.exe` / `.msi` / `.msix`。
- 后续可选：Microsoft Store。

主要要求：

- Microsoft Store 官方资料显示，个人开发者新流程已免注册费；公司注册费也已取消。
- Windows 直接分发 `.exe` / `.msi` 时，代码签名证书不是语法上的绝对必需，但强烈建议，否则容易触发 SmartScreen 和杀软信任问题。
- `.msix` 比普通 `.exe` / `.msi` 更接近 Store/包身份模型，签名、证书、安装策略和更新策略需要单独评估。
- Microsoft 官方文档指出 EV 证书已经不再默认绕过 SmartScreen，OV/EV 都需要建立 reputation。
- Microsoft 当前文档把 Artifact Signing（formerly Trusted Signing）作为非 Store 分发的推荐代码签名服务之一，但它也不等于立即消除 SmartScreen，reputation 仍需积累。

关键风险：

- WASAPI loopback 可用于系统音频采集，但不同设备、蓝牙耳机、会议软件、声卡驱动会有差异。
- 杀软误报、SmartScreen、安装器签名、自动更新是 Windows 分发的主要摩擦。
- 如果走 Microsoft Store，打包形态、权限声明和更新策略会不同。

当前建议：

```text
Windows 在 Mac MVP 后进入：
  共享 core/UI
  单独实现 windows_audio_capture adapter
  单独 CI 构建 Windows installer
  MVP direct download 优先 .exe/.msi
  上线前准备代码签名或 Artifact Signing
  .msix / Microsoft Store 后置评估
```

## 3. PC 端代码结构建议

推荐结构：

```text
code/
  core/
    transcript/
    evidence/
    normalizer/
    scheduler/
    state/
    suggestions/
    report/
    llm_gateway/

  asr_runtime/
    providers/
      funasr/
      sherpa/
      mock/
    worker/

  desktop/
    shell/
      tauri_or_electron/
    platform/
      macos/
        audio_capture/
        permissions/
        updater/
      windows/
        audio_capture/
        permissions/
        updater/

  web_mvp/
    backend/
    frontend/
```

关键原则：

- `core` 不依赖任何桌面框架。
- `asr_runtime` 作为 worker/sidecar，不直接嵌入 UI 进程。
- `desktop/platform/macos` 和 `desktop/platform/windows` 只处理平台能力。
- UI 通过本地 API/WebSocket 订阅 transcript/state/card/report。
- 安装包差异由 CI 和 platform packaging 处理，不污染核心业务逻辑。

### 3.1 ASR worker 和模型分发边界

本产品不是普通桌面壳，还包含本地 ASR worker、模型缓存、音频处理依赖和可选 Python/sidecar runtime。后续打包必须提前设计：

- 模型是否随安装包内置，还是首次运行由用户明确触发下载。
- 模型许可证是否允许商用、再分发、离线缓存和二次打包。
- 模型下载源、版本、大小、hash、缓存目录和清理方式。
- sidecar worker 的启动、崩溃重启、日志、内存上限和退出释放。
- Mac App Store sandbox 对动态下载模型、执行 sidecar、本地文件访问和音频采集的限制。
- Windows 安装包体积、杀软误报、模型目录权限和自动更新差分包风险。

当前建议：

```text
MVP 不把大模型文件默认塞进主安装包。
首次运行由用户确认下载本地 ASR 模型。
下载前展示模型大小、许可证提示和磁盘占用。
所有模型文件进入可清理缓存目录，并记录 provider/model/hash。
```

## 4. iOS 上架规划

### 4.1 是否适合做完整移动端

当前不建议把 iOS 作为 MVP 主链路。

原因：

- iOS 对后台录音、系统音频、其他 App 音频捕获有强限制。
- iOS 更适合做“移动 companion app”，例如查看会议纪要、管理历史会议、录制线下会议麦克风音频。
- 它不适合作为“捕获 Mac/PC 会议系统音频”的主方案。

建议的 iOS 形态：

```text
后续 companion app：
  查看会议记录
  查看建议卡片和证据
  线下会议麦克风录音
  会后报告阅读/分享
  不作为桌面会议系统音频主采集端
  不承诺捕获其他 App 音频、通话音频或会议 App 系统音频
```

### 4.2 Apple 个人开发者上架要求

官方要求和风险：

- Apple Developer Program 是 99 USD/年。
- 个人开发者可以注册并上架，但 App Store 会显示个人法定姓名作为 seller/developer name。
- 如果想显示公司名称，需要组织开发者账号，组织需要法律实体和 D-U-N-S Number。
- 每个 App 和更新都需要 App Review。
- 需要隐私政策 URL。
- 需要填写 App Privacy Details / Privacy Nutrition Labels，声明收集和共享的数据，包括第三方 SDK 和第三方 AI 服务处理的数据。
- 使用麦克风需要系统权限说明 purpose string。
- 如果把会议内容发送给第三方 AI/LLM，需要在隐私政策和应用内明确披露，并取得用户同意。

对本产品的特殊风险：

- 会议录音属于高敏感场景，必须有清晰的“开始/暂停/停止/删除”。
- 不能做隐蔽录音或后台偷偷录音。
- 需要明确提示参会人告知责任。
- 如果录音或转写发送给 LLM 中转站，必须披露数据会发送到第三方/远程服务。
- 如果产品面向儿童或可能处理未成年人数据，合规复杂度会明显上升；当前不建议定位儿童/教育未成年人场景。

### 4.3 中国大陆 App Store 的额外问题

如果计划在中国大陆 App Store 提供 iOS App，需要提前考虑：

- Apple 中文文档说明，中国工信部要求部分 App 必须具备有效 ICP 备案号；App Store Connect 的 ICP 编号和元数据应与工信部备案信息一致。
- Apple 同页说明游戏、图书/报刊、宗教、新闻等特定类型还需要额外许可证。
- 工信部关于 APP 备案的通知说明，在中国境内从事互联网信息服务的 APP 主办者应履行备案手续，未履行备案不得从事 APP 互联网信息服务。

本产品如果是联网 App，尤其涉及账号、云端 LLM、中转站、订阅或同步，基本需要按中国大陆合规路径准备：

```text
域名 / 服务器 / ICP 备案
APP 备案
隐私政策
个人信息处理规则
用户注销和数据删除
第三方 SDK/AI 服务披露
必要时公安联网备案
```

个人开发者风险：

- 个人可以做部分备案，但如果涉及商业化订阅、企业客户、付费服务、多人协作或更复杂的数据处理，后续大概率需要公司主体更稳。
- 个人 Apple Developer 上架会显示个人姓名，不利于品牌和商业化。

当前建议：

```text
iOS 后续如果做：
  先考虑非中国大陆 storefront 或 TestFlight 内测
  中国大陆上架前先完成 ICP/APP 备案路径确认
  商业化前评估是否注册公司主体
```

不要把 iOS 理解成“交 99 美元就一定能轻松上架”。

## 5. Android 上架规划

### 5.1 Google Play

官方要求和风险：

- Google Play Console 官方说明有 25 USD 一次性注册费。
- Google Play 个人开发者账号需要身份验证。
- 新个人开发者账号在生产发布前，需要至少 12 名测试者连续 14 天 opt-in 的 closed test，并申请 production access。
- 需要填写 Data safety form。
- 需要隐私政策。
- 如果访问或处理个人/敏感数据，包括麦克风、音频、用户数据，需要符合 User Data policy 的 prominent disclosure 和 consent 要求。

对本产品的特殊风险：

- 麦克风、录音、音频文件、转写文本、会议纪要都属于敏感数据或至少高隐私数据。
- 如果发送给 LLM 中转站，必须在 Data safety、隐私政策和应用内披露。
- Android 对后台录音、通话录音、系统音频也有平台限制和审核风险。

当前建议：

```text
Google Play Android 端也作为 companion app：
  查看记录
  线下会议麦克风录音
  不承诺捕获其他 App/系统音频
  不承诺后台隐蔽录音
  只做用户显式开始/暂停/停止的录音流程
```

### 5.2 中国 Android 应用市场

中国 Android 不是一个统一商店，而是多个市场：

- 华为应用市场
- 小米应用商店
- 应用宝
- OPPO / vivo 等

共性要求：

- APP 备案。
- ICP/互联网信息服务相关证明。
- 隐私政策和权限合规。
- 应用名称、包名、主体与备案信息一致。
- 应用内展示 APP 备案编号，部分市场要求可点击跳转备案查询。

额外市场差异：

- 小米官方文档显示，上架需提供有效 ICP 证明，且上架要求中包含软件著作权证书或 APP 电子版权认证证书，并要求备案主体、应用名称、包名一致。
- 华为官方资质审核要求引用工信部 APP 备案要求，未备案不得从事 APP 互联网信息服务。
- 应用宝页面也提示 2023-09-01 后新增 APP 必须先履行 APP 备案再申请上架。

个人开发者风险：

- 国内 Android 市场对个人开发者越来越不友好，部分市场/品类更倾向企业主体。
- 软件著作权、ICP、APP 备案、隐私合规材料会拉长周期。
- 如果涉及 AI、录音、会议内容和云端服务，审核解释成本更高。

当前建议：

```text
国内 Android 暂不作为早期目标。
如果未来要做中国 Android 分发：
  先准备公司主体
  先完成软著/APP备案/ICP路径
  再选 1-2 个主流商店试点
```

## 6. 费用和周期预估

以下是当前可确认或需要预留的成本项：

| 项目 | 当前判断 | 备注 |
|---|---|---|
| Apple Developer Program | 99 USD/年 | iOS、Mac App Store、Developer ID 外部分发都需要 |
| Google Play Console | 25 USD 一次性 | 官方 Play Console 注册费 |
| Microsoft Store | 当前个人/公司注册新流程可免费 | 仍需身份验证 |
| macOS notarization | 无单独列出的额外费用 | 需要 Apple Developer Program 和 Developer ID |
| Windows 代码签名证书 | 可能需要付费 | 价格随 CA、OV/EV、年限变化；EV 不再保证绕过 SmartScreen |
| Microsoft Artifact Signing / Trusted Signing | 可能需要月费 | Microsoft 推荐的非 Store 分发签名候选之一，仍需身份验证和 reputation 积累 |
| 域名 | 需要 | 隐私政策、下载页、后端服务、备案 |
| 服务器/对象存储 | 视是否做同步/官网/下载而定 | 中国大陆备案通常与服务器/接入商有关 |
| ICP/APP 备案 | 官方通常不按备案本身收费 | 但会产生服务器、域名、材料、时间、人力或代办成本 |
| 软件著作权/APP 电子版权认证 | 中国 Android 市场可能需要 | 周期和费用取决于申请方式 |
| 隐私政策/法律文本 | 建议预留 | 录音 + AI + 第三方服务场景风险较高 |
| 测试设备 | 建议预留 | Mac、Windows、iPhone、Android 真机 |
| App Store / Google Play 数字商品服务费 | 商业化后需要评估 | 如果做订阅、付费 App、IAP、云服务套餐或数字功能解锁，平台服务费可能成为主要成本，不等于只交开发者账号费 |

周期风险：

- App Store Review 通常不是纯技术提交，隐私、录音、AI 数据共享说明可能导致反复沟通。
- Google Play 新个人开发者账号通常有至少 12 testers / 14 days 的生产发布门槛；组织账号、老账号和不同发布路径要求可能不同。
- 中国 Android 市场如果缺软著、备案或主体材料，可能卡数周甚至更久。
- ICP/APP 备案时间取决于主体、服务器接入商、管局和材料完整度。

## 7. 对当前产品路线的影响

### 7.1 当前仍坚持 PC-first

原因：

- 本产品核心场景是电脑开会。
- PC 端更适合接会议软件音频、系统音频和本地 ASR。
- 移动端录音能力和后台限制较多，不能替代 PC 客户端。

### 7.2 移动端暂时只做规划，不进入 MVP

移动端如果过早进入，会引入：

- App Store / Google Play 审核。
- 中国大陆备案。
- 麦克风/后台权限。
- 第三方 AI 数据披露。
- 多端同步和账号系统。

这些都会拖慢当前 MVP。

建议：

```text
当前 1-2 个阶段：
  不做移动端实现

Web MVP 价值验证后：
  评估 iOS companion app

Mac/Windows 客户端稳定后：
  再评估 Android / 国内应用市场
```

## 8. 后续实现建议

### 8.1 PC 端代码策略

后续必须保持：

```text
core 独立
platform adapter 独立
packaging 独立
```

不能把 macOS 音频采集、Windows 音频采集、LLM prompt、EvidenceSpan 规则混在一个大客户端里。

### 8.2 打包策略

初期：

```text
Mac:
  Developer ID 签名 + notarization + dmg/pkg

Windows:
  signed installer + direct download
```

后续：

```text
Microsoft Store:
  可作为 Windows 分发补充

Mac App Store:
  仅在确认 sandbox 不影响核心能力后考虑
```

自动更新必须作为单独设计项，不能临时拼：

```text
Mac:
  评估 Tauri updater / Sparkle / 自研更新器
  更新包同样需要签名、notarization、hash 校验和回滚策略

Windows:
  评估 Tauri updater / Squirrel / MSIX / Microsoft Store 更新
  更新包同样需要签名、完整性校验、失败回滚和 SmartScreen 风险评估
```

### 8.3 移动端策略

后续如果做 iOS/Android：

```text
移动端不承诺系统音频采集
移动端先做 companion app
移动端必须有独立隐私/权限/AI 数据披露设计
中国大陆分发前先完成 ICP/APP 备案路径确认
```

## 9. 当前决策

Accepted：

- PC 端采用共享 core/UI + 平台 adapter，不做 Windows/Mac 两套业务代码。
- Mac-first，Windows later。
- 移动端不进入当前 MVP，只做后续 companion app 规划。
- Mac MVP 初期优先 App Store 外分发，不优先 Mac App Store。
- 中国大陆移动端上架前必须先处理 ICP/APP 备案和隐私合规，不把“交平台费”当作唯一上架成本。

Open：

- 桌面壳最终采用 Tauri、Electron 还是其他方案。
- Windows 是否优先 Microsoft Store，还是 direct download。
- 未来是否成立公司主体用于品牌名、App Store seller name、中国 Android 市场资质和商业化。

## 10. 主要资料来源

- Apple Developer Program 费用、个人/组织账号、seller name、D-U-N-S：  
  https://developer.apple.com/programs/enroll/  
  https://developer.apple.com/help/account/membership/program-enrollment/

- Apple Developer Program 99 USD/年与 App Store 费用说明：  
  https://developer.apple.com/programs/whats-included/

- Apple App Privacy Details 与 App Review Guidelines：  
  https://developer.apple.com/app-store/app-privacy-details/  
  https://developer.apple.com/app-store/review/guidelines/

- Apple App Store 商业模式和小型企业计划：  
  https://developer.apple.com/app-store/business-models/  
  https://developer.apple.com/app-store/small-business-program/

- Apple macOS Developer ID / notarization：  
  https://developer.apple.com/macos/distribution/  
  https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution

- Apple 中国大陆 App Store ICP/许可证说明：  
  https://developer.apple.com/cn/help/app-store-connect/reference/app-information/app-information/

- Google Play Console 注册费、个人账号测试要求、Data safety、User Data policy：  
  https://support.google.com/googleplay/android-developer/answer/6112435  
  https://support.google.com/googleplay/android-developer/answer/14151465  
  https://support.google.com/googleplay/android-developer/answer/10787469  
  https://support.google.com/googleplay/android-developer/answer/10144311

- Google Play 服务费说明：  
  https://support.google.com/googleplay/android-developer/answer/112622  
  https://support.google.com/googleplay/android-developer/answer/11131145

- Microsoft Store 注册费与 Windows code signing / SmartScreen：  
  https://learn.microsoft.com/en-us/windows/apps/publish/whats-new-individual-developer  
  https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/code-signing-options  
  https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation

- 工信部 APP 备案通知、中央网信办 APP 信息服务管理规定：  
  https://www.hunan.gov.cn/zqt/zcsd/202308/t20230809_29456035.html  
  https://www.cac.gov.cn/2022-06/14/c_1656821626455324.htm

- 华为、小米、应用宝关于 APP 备案/资质的公开说明：  
  https://developer.huawei.com/consumer/cn/doc/app/80301  
  https://dev.mi.com/xiaomihyperos/documentation/detail?pId=1322  
  https://dev.mi.com/xiaomihyperos/documentation/detail?pId=1832  
  https://app.open.qq.com/

- Tauri / Electron / Flutter 桌面跨平台资料：  
  https://v2.tauri.app/start/prerequisites/  
  https://electronjs.org/docs/latest/tutorial/code-signing  
  https://docs.flutter.dev/platform-integration/desktop
