import { useEffect, useState } from "react";
import type { CoachDecisionProjection, CoachHistoryEntry, FollowUpProjection } from "../../domain/events";
import { isParticipationProjection, piRevisionIdentity, piRevisionIsValid } from "./answerThreads";

function readIdentity(key: string): string | null {
  try { return sessionStorage.getItem(key); } catch { return null; }
}

/** Like the answer reader, follow new results until the user starts reading. */
export function useContributionReading(latest: FollowUpProjection | null, history: CoachHistoryEntry[],
  decision: CoachDecisionProjection | null, scope: string, nowMs: number) {
  const key = `meeting-copilot-participation-reader:${scope}`;
  const [pin, setPin] = useState<{ key: string; id: string | null; value?: FollowUpProjection }>(() => ({ key, id: readIdentity(key) }));
  useEffect(() => { setPin({ key, id: readIdentity(key) }); }, [key]);
  const identity = pin.key === key ? pin.id : null;
  const historyItem = identity ? history.find((item) => piRevisionIdentity(item) === identity) : undefined;
  const held = identity ? pin.value ?? historyItem ?? (latest && piRevisionIdentity(latest) === identity ? latest : null) : null;
  const selected = held ?? latest;
  const matchingDecision = held && decision && decision.decisionId === held.decisionId ? decision : null;
  const changedTopic = Boolean(held?.topicId && latest?.topicId && held.topicId !== latest.topicId);
  const changedBasis = Boolean(held?.topicId && latest?.topicId === held.topicId && held.analysisBasisRevision !== undefined
    && latest.analysisBasisRevision !== undefined && held.analysisBasisRevision !== latest.analysisBasisRevision);
  const superseded = Boolean(held?.decisionId && decision?.supersedesDecisionId === held.decisionId);
  const invalid = Boolean(held && (!piRevisionIsValid(historyItem ?? held, nowMs) || superseded
    || matchingDecision && !piRevisionIsValid(matchingDecision, nowMs) || changedTopic || changedBasis));
  const hold = () => {
    if (!selected || !isParticipationProjection(selected) || held) return;
    const id = piRevisionIdentity(selected);
    setPin({ key, id, value: selected });
    try { sessionStorage.setItem(key, id); } catch { /* Reading still works without session storage. */ }
  };
  const release = () => {
    setPin({ key, id: null });
    try { sessionStorage.removeItem(key); } catch { /* No persistence is required to switch. */ }
  };
  return { selected, held: Boolean(held), invalid,
    newAvailable: Boolean(held && latest && piRevisionIdentity(held) !== piRevisionIdentity(latest)),
    hold, release };
}
