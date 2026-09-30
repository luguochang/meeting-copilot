import { Quote } from "lucide-react";
import type { FollowUpProjection } from "../../domain/events";

export function PiSupplement({ value: revision, onEvidence }: {
  value: FollowUpProjection;
  onEvidence(segmentId: string): void;
}) {
  const value = revision.coachingPackage;
  if (!value) return <p className="coach-prose">{revision.sayThis ?? revision.question}</p>;
  const groups = [
    { label: "遗漏重点", items: value.missingPoints.filter((item) => item !== value.coreJudgement && item !== value.whyItMatters) },
    { label: "适用约束", items: value.constraints },
    { label: "关键风险", items: value.risks },
    { label: "下一步", items: value.nextActions },
  ].filter((group) => group.items.length);
  return <div className="coach-supplement-content">
    <h3>{value.headline}</h3>
    <blockquote className="coach-say-this">{value.sayThisAddition}</blockquote>
    <p className="coach-prose">{value.coreJudgement}</p>
    {value.whyItMatters !== value.coreJudgement ? <p className="coach-prose">{value.whyItMatters}</p> : null}
    {groups.map((group) => <section className="coach-detail-group" key={group.label}>
      <h4>{group.label}</h4>
      <ul>{group.items.map((item) => <li key={item}>{item}</li>)}</ul>
    </section>)}
    {value.likelyFollowUps.length ? <section className="coach-detail-group">
      <h4>可能追问</h4>
      {value.likelyFollowUps.map((item) => <div key={`${item.question}:${item.answerAngle}`}>
        <p className="coach-follow-up-question">{item.question}</p><p className="coach-prose">{item.answerAngle}</p>
      </div>)}
    </section> : null}
    <details className="coach-evidence-details">
      <summary>查看分析依据</summary>
      <p>{value.questionIntent}</p>
      <div className="coach-evidence-links">{value.evidenceRefs.map((ref, index) =>
        <button className="coach-text-button" key={ref.segmentId} type="button" onClick={() => onEvidence(ref.segmentId)} title={ref.quote}>
          <Quote size={13} />原话 {index + 1}
        </button>)}
      </div>
    </details>
  </div>;
}
