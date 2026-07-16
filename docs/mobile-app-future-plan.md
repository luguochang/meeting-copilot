# Mobile App Future Plan

> 日期：2026-07-08
> 状态：P3-1 completed as future plan
> 范围：Meeting Copilot iOS / Android 后续路线，不进入当前 PC MVP 实现。
> 结论：移动端适合做 companion app，不适合作为当前“PC 会议实时音频捕获 + 实时建议”的主链路。

## 1. 当前定位

当前 MVP 优先级：

```text
PC Web/Local MVP
  -> Mac desktop dev shell
  -> Mac real mic / worker / packaging
  -> Windows compatibility
  -> mobile companion app
```

移动端不作为当前 P0/P1/P2 blocker。

## 2. iOS 规划

适合的 iOS 形态：

- 查看会议纪要。
- 管理历史会议。
- 线下会议麦克风录音。
- 轻量复盘和搜索。
- 接收会议后的行动项提醒。

不适合作为当前主链路：

- 捕获 Mac/PC 上其他会议软件的系统音频。
- 长时间后台录音。
- 绕过 iOS 权限模型采集其他 App 音频。

账号和费用：

- Apple Developer Program 官方费用通常为 99 USD/年。
- 个人开发者可以上架，但 App Store seller/developer name 通常会显示个人姓名。
- 如果未来需要品牌名、团队协作、商业合规和中国应用市场材料，建议评估公司主体。

审核和隐私风险：

- 麦克风权限需要 purpose string。
- 音频、转写文本、会议纪要、AI 建议都属于高隐私数据。
- 如果上传音频或文本到 LLM/云服务，App Privacy Details 必须披露数据类型、用途、是否关联用户、是否用于追踪。
- AI 生成内容需要清晰说明用途和限制。
- 中国大陆 App Store 可能涉及 ICP/许可证等 App filing 要求；不能理解为“交 99 USD 就一定能上架”。

参考：

- Apple Developer Program membership：<https://developer.apple.com/support/compare-memberships/>
- Apple App privacy details：<https://developer.apple.com/app-store/app-privacy-details/>
- Apple App Store Review Guidelines：<https://developer.apple.com/app-store/review/guidelines/>
- Apple 中国大陆 App 备案/ICP 帮助：<https://developer.apple.com/help/app-store-connect/manage-compliance-information/provide-app-filing-information-for-apps-distributed-in-china-mainland/>

## 3. Android 规划

适合的 Android 形态：

- 查看会议纪要。
- 管理历史会议。
- 线下会议麦克风录音。
- 接收行动项提醒。

不适合作为当前主链路：

- 稳定捕获 PC/Mac 会议系统音频。
- 作为 PC 桌面会议实时建议的替代入口。

Google Play：

- Google Play Console 官方注册费通常为 25 USD 一次性。
- Google Play 个人开发者账号存在更严格的测试/验证要求；新个人账号常见要求是在生产发布前完成 closed testing，例如 12 名测试者连续 14 天加入。
- 处理麦克风、音频、转写文本、会议纪要等数据，需要符合 User Data policy 和 Data safety disclosure。

中国 Android 市场：

- 不是统一市场，通常需要分别处理华为、小米、OPPO、vivo、荣耀、应用宝等渠道。
- 可能涉及软著、隐私政策、权限合规、ICP备案、主体资质和人工审核。
- 个人开发者在部分市场/品类可能受限，周期和材料不确定性较大。

参考：

- Google Play Console account creation / registration fee：<https://support.google.com/googleplay/android-developer/answer/6112435>
- Google Play personal developer testing requirements：<https://support.google.com/googleplay/android-developer/answer/14151465>
- Google Play User Data policy：<https://support.google.com/googleplay/android-developer/answer/10144311>
- Google Play Data safety：<https://support.google.com/googleplay/android-developer/answer/10787469>

## 4. 数据与权限边界

移动端进入实现阶段前，必须新增独立隐私设计：

```text
麦克风权限说明
音频本地保存目录
转写文本保存策略
云端 LLM/ASR 调用 disclosure
删除账号/删除会议数据
Data safety / App privacy answers
日志脱敏
崩溃上报脱敏
```

默认策略仍应保持：

```text
remote_asr_default_enabled=false
raw_audio_uploaded_by_default=false
llm_gateway_called_only_when_ai_analysis_enabled=true
```

## 5. 当前不做的事情

P3-1 当前只是 future plan，不做：

- iOS 原生工程。
- Android 原生工程。
- App Store / Google Play 提交。
- 国内 Android 市场提交。
- 账号注册。
- 付费 IAP / 订阅。
- 移动端真实麦克风采集。
- 移动端后台录音。

## 6. 后续进入条件

移动端进入实现阶段前，至少需要：

- PC/Mac 主链路稳定。
- 桌面端隐私和删除策略稳定。
- 明确移动端只做 companion 还是也做线下会议录音。
- 确认开发者主体：个人或公司。
- 写完 App Privacy / Data Safety 草稿。
- 准备测试设备和审核周期。
