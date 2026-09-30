import type {
  ActionItemProjection,
  CoachDecisionProjection,
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
  viewStateKey?: string;
  historyOnly?: boolean;
  onCoachRequestStatus?(jobId: string, signal?: AbortSignal): Promise<"pending" | "succeeded" | "failed" | "no_change">;
  currentTopic: TopicProjection | null;
  followUp: FollowUpProjection | null | undefined;
  semanticFollowUp?: FollowUpProjection | null;
  coachDecision?: CoachDecisionProjection | null;
  coachHistory?: CoachHistoryEntry[];
  recentContextHistory?: RecentContextEntry[];
  openQuestions: OpenQuestionProjection[];
  suggestions: Suggestion[];
  decisionCandidates: DecisionCandidate[];
  actionItems: ActionItemProjection[];
  risks: RiskProjection[];
  coachRuntime?: RuntimeIndicator | null;
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
}
