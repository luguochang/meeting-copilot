import { AnswerCoachReader } from "./AnswerCoachReader";
import { CoachSignals } from "./CoachSignals";
import type { NowRailProps } from "./nowRailTypes";
import { useEffect, useState } from "react";
import { isCurrentAnswer, isParticipationProjection } from "./answerThreads";
import { CoachActivityStatus } from "./CoachActivityStatus";
import { ContributionControls } from "./ContributionControls";
import { DiscussionUnderstandingStatus } from "./DiscussionUnderstandingStatus";
import { coachLifecycleView } from "./coachLifecycle";
import { isLocalReflexCoachProjection } from "../../domain/events";

function CoachStatus({ props, hasAnswer }: { props: NowRailProps; hasAnswer: boolean }) {
  const [, refresh] = useState(0);
  const deadline = props.coachDecision?.validUntil ?? props.followUp?.validUntil;
  useEffect(() => {
    if (deadline === undefined || deadline <= Date.now()) return;
    const timer = window.setTimeout(() => refresh((value) => value + 1), Math.min(deadline - Date.now() + 25, 2_147_483_647));
    return () => window.clearTimeout(timer);
  }, [deadline]);
  if (props.historyOnly) return null;
  const trustedFollowUp = props.followUp && (props.followUp.formalAi?.llmCalled === true || isLocalReflexCoachProjection(props.followUp)) ? props.followUp : null;
  const lifecycle = props.coachDecision ? coachLifecycleView(props.coachDecision, Date.now()) : trustedFollowUp ? coachLifecycleView(trustedFollowUp, Date.now()) : null;
  const failed = props.coachDecision?.status === "failed" || props.coachDecision?.status === "timed_out"
    || props.coachRuntime?.state === "error" || props.discussionRuntime?.decision === "failed";
  const busy = props.coachRuntime?.state === "busy" || props.discussionRuntime?.decision === "running";
  const label = failed ? "部分分析未完成，可展开核对" : busy ? "正在分析讨论，已有内容保留"
    : lifecycle?.label ?? props.coachRuntime?.label ?? "可随时主动请求建议";
  return <details className="coach-status-details">
    <summary><span role="status" aria-live="polite" title={props.coachRuntime?.detail ?? undefined}>{busy && !failed && props.coachRuntime?.label ? props.coachRuntime.label : label}</span><small>运行详情</small></summary>
    <DiscussionUnderstandingStatus runtime={props.discussionRuntime} />
    <CoachActivityStatus decision={props.coachDecision} runtime={props.coachRuntime} dueItems={props.coachDueItems} hasAnswer={hasAnswer} />
    {lifecycle?.detail ? <p>{lifecycle.detail}</p> : null}
    {!props.discussionRuntime && !props.coachDecision && !props.coachRuntime ? <p>听到讨论后提供建议，也可用上方按钮主动请求。</p> : null}
  </details>;
}

export function NowRail(props: NowRailProps) {
  const [historyReading, setHistoryReading] = useState(false);
  useEffect(() => setHistoryReading(false), [props.viewStateKey]);
  const answers = props.suggestions.filter((item) => item.kind === "answer");
  const hasCurrentAnswer = answers.some(isCurrentAnswer);
  const activity = <CoachStatus props={props} hasAnswer={hasCurrentAnswer} />;
  const contribution = !props.historyOnly ? <ContributionControls {...props} /> : null;
  const hasOtherSignals = props.coachHistory?.some((item) => item.promptProfile !== "deep_answer" && (item.status === undefined || item.status === "intervention" || item.status === "stale"));
  if (!answers.length || (!props.historyOnly && !hasCurrentAnswer && !historyReading)) return <div className="coach-rail-stack">
    {contribution}
    {activity}
    <div className="coach-rail-content">
    {props.historyOnly && !hasOtherSignals ? <p className="rail-empty">本场会议还没有问答与建议记录。</p> : null}
    <CoachSignals {...props} suppressActivityStatus suppressEmptyExpectation />
    {answers.length ? <button type="button" className="coach-text-button" onClick={() => setHistoryReading(true)}>查看问答历史 {answers.length}</button> : null}
    </div>
  </div>;
  const hasCurrentContribution = !props.historyOnly && Boolean(props.followUp && isParticipationProjection(props.followUp)
    || !props.historyOnly && props.coachHistory?.some(isParticipationProjection));
  const otherSignals = hasCurrentContribution ? props.coachHistory?.filter((item) => !isParticipationProjection(item)) : props.coachHistory;
  const otherHistory = hasOtherSignals && otherSignals?.length ? <details className="coach-other-history"><summary>其他会中提示</summary><CoachSignals {...props} coachHistory={otherSignals} historyOnly suggestions={[]} followUp={null} semanticFollowUp={null} coachRuntime={null} /></details> : null;
  return <div className="coach-rail-stack">{contribution}{activity}<div className="coach-rail-content">{hasCurrentContribution ? <CoachSignals {...props} suggestions={[]} suppressActivityStatus suppressEmptyExpectation /> : null}<AnswerCoachReader key={props.viewStateKey ?? "current"} {...props}
    startInHistory={!hasCurrentAnswer && historyReading} onHistoryReadingChange={setHistoryReading}>
    {props.historyOnly ? null : otherHistory}
  </AnswerCoachReader>{props.historyOnly ? otherHistory : null}</div></div>;
}
