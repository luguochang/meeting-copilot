export function MeetingReuseGuide({ saved }: { saved: boolean }) {
  return <section className="meeting-reuse-guide" aria-label="会后内容复用步骤">
    <p>资料文本与项目事项分别确认；入库不等于已进入 Wiki。</p>
    <ol>
      <li><strong>核对并保存人工终稿</strong><small>{saved ? "所选终稿已保存，可继续下方操作。" : "先完成文稿编辑并保存，不使用未保存草稿。"}</small></li>
      <li><strong>资料路径：入库后检查并确认</strong><small>先生成资料草稿；确认有效版后，才能作为后续会议背景。</small></li>
      <li><strong>可选：核对并确认项目变化</strong><small>明确点击 Pi 核对才调用模型；候选经你确认后才更新事项和 Wiki。</small></li>
      <li><strong>下次会议选择同项目与范围</strong><small>会前查看已确认事项、未完成待办和有效资料；准备简报仍由你明确发起。</small></li>
    </ol>
  </section>;
}
