import type { FollowUpProjection } from "../../domain/events";

export function ContributionBasis({ value }: { value: FollowUpProjection }) {
  if (!value.analysisKind && !value.proposalType) return null;
  return <section className="contribution-basis" aria-label="建议性质与依据">
    <span>{value.proposalType === "grounded_fact" ? "资料事实" : value.proposalType === "external_claim" ? "外部事实 · 查看来源" : "专业提议 · 供讨论"}</span>
    {value.supportingFacts?.length || value.assumptions?.length ? <details><summary>已知条件与待核实假设</summary>
      {value.supportingFacts?.length ? <><strong>已知条件</strong><ul>{value.supportingFacts.map((fact, index) => <li key={`${fact.sourceRef}:${index}`}>{fact.text}</li>)}</ul></> : null}
      {value.assumptions?.length ? <><strong>待核实假设</strong><ul>{value.assumptions.map((text, index) => <li key={index}>{text}</li>)}</ul></> : null}
    </details> : null}
    <details className="contribution-coverage"><summary>本次资料覆盖</summary>
      {value.materialContext ? <>
        <p>以下是提供给这次分析的正文范围；允许使用资料不代表已经提供或理解全文。</p>
        {value.materialContext.attachmentCatalog.length ? <ul>{value.materialContext.attachmentCatalog.map((item) => {
          const coverage = item.coverage;
          const complete = coverage.completeRead && coverage.providedSpanCount === coverage.authorizedSpanCount && coverage.providedCharacters === coverage.authorizedCharacters;
          return <li key={`${item.attachmentId}:${item.versionId}`}><strong>{item.title}</strong>
            <p>已提供 {coverage.providedSpanCount} 段 / {coverage.providedCharacters} 字；授权 {coverage.authorizedSpanCount} 段 / {coverage.authorizedCharacters} 字</p>
            <span>{!coverage.providedSpanCount ? "本次未提供正文片段" : complete ? coverage.selection === "selected_spans" ? "所选范围已完整提供" : "授权全文已完整提供" : "本次仅提供相关片段"}</span>
          </li>;
        })}</ul> : <p>本次分析没有授权的本场附件。</p>}
        {value.materialContext.catalogTruncated ? <p>这里只列出部分附件，不能据此判断其余资料覆盖。</p> : null}
      </> : <p>未记录本次资料覆盖，不能据此判断是否提供全文。</p>}
    </details>
  </section>;
}
