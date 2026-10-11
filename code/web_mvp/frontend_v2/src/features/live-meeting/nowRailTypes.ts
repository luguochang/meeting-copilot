import type { KnowledgeApi } from "../../api/knowledge";
import type { CoachRequestOutcome, ContributionIntent, DiscussionWorkItem } from "../../api/client";
import type { DocumentSourceEvidence } from "../../domain/events";
import type {
  ActionItemProjection,
  CoachDecisionProjection,
  CoachDueWorkItem,
  CoachHistoryEntry,
  DecisionCandidate,
  FollowUpProjection,
  MeetingFactKind,
  MeetingFactStatus,
  OpenQuestionProjection,
  RecentContextEntry,
  RiskProjection,
  RuntimeIndicator,
  Suggestion,
  SuggestionFeedback,
  TopicProjection,
} from "../../domain/events";
export interface NowRailProps {
  knowledgeApi?: KnowledgeApi;
  sourceMeetingId?: string;
  onKnowledgeSource?: (value: DocumentSourceEvidence, sourceMeetingId?: string) => void;
  onProjectCandidate?: (factType: "decision" | "action_item", factId: string) => Promise<void>;
  viewStateKey?: string;
  historyOnly?: boolean;
  onCoachRequestStatus?(jobId: string, signal?: AbortSignal): Promise<"pending" | "succeeded" | "failed" | "timed_out" | "no_change">;
  onCoachRequestOutcome?(jobId: string, signal?: AbortSignal): Promise<CoachRequestOutcome>;
  currentTopic: TopicProjection | null;
  followUp: FollowUpProjection | null | undefined;
  semanticFollowUp?: FollowUpProjection | null;
  coachDecision?: CoachDecisionProjection | null;
  coachHistory?: CoachHistoryEntry[];
  coachDueItems?: CoachDueWorkItem[];
  recentContextHistory?: RecentContextEntry[];
  openQuestions: OpenQuestionProjection[];
  suggestions: Suggestion[];
  decisionCandidates: DecisionCandidate[];
  actionItems: ActionItemProjection[];
  risks: RiskProjection[];
  coachRuntime?: RuntimeIndicator | null;
  discussionRuntime?: RuntimeIndicator | null;
  activeCoachSkillId?: "general" | "decision" | "project" | "interview" | "brainstorm" | null;
  onEvidence(segmentId: string): void;
  onFeedback(suggestionId: string, feedback: SuggestionFeedback): Promise<void>;
  onFactStatus(factType: MeetingFactKind, factId: string, status: Extract<MeetingFactStatus, "confirmed" | "dismissed">): Promise<void>;
  onFactEdit?(
    factType: MeetingFactKind,
    factId: string,
    changes: { text: string; owner?: string | null; deadline?: string | null; mitigation?: string | null },
    expectedVersion: number,
  ): Promise<void>;
  onFactMerge?(
    factType: MeetingFactKind,
    targetFactId: string,
    sourceFactId: string,
    expectedTargetVersion: number,
    expectedSourceVersion: number,
  ): Promise<void>;
  onMessage(message: string): void;
  onCoachRefine?(answerId: string, request: string): Promise<string | void>;
  onContribute?(intent: ContributionIntent): Promise<string | void>;
  onOpenAsk?(): void;
  onReadDiscussion?(signal?: AbortSignal): Promise<DiscussionWorkItem | null>;
  onUpdateDiscussion?(item: DiscussionWorkItem, changes: { title?: string; context?: Record<string, unknown>; state?: string }): Promise<DiscussionWorkItem>;
}
