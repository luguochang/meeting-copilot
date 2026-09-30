import { AnswerCoachReader } from "./AnswerCoachReader";
import { CoachSignals } from "./CoachSignals";
import type { NowRailProps } from "./nowRailTypes";

export function NowRail(props: NowRailProps) {
  if (!props.suggestions.some((item) => item.kind === "answer")) return <>
    {props.historyOnly ? <p className="rail-empty">本场会议还没有问答记录。</p> : null}
    <CoachSignals {...props} />
  </>;
  const hasOtherSignals = props.coachHistory?.some((item) => item.promptProfile !== "deep_answer");
  return <AnswerCoachReader key={props.viewStateKey ?? "current"} {...props}>
    {hasOtherSignals ? <details className="coach-other-history"><summary>其他会中提示</summary><CoachSignals {...props} historyOnly suggestions={[]} followUp={null} semanticFollowUp={null} coachRuntime={null} /></details> : null}
  </AnswerCoachReader>;
}
