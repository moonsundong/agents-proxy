export interface DecisionTier {
  min_confidence: number;
  model_id: number | null;
}

export interface RoutingPolicy {
  id: number;
  name: string;
  scenario: string;
  decision_model_id: number | null;
  decision_content_limit: number;
  decision_head_ratio: number;
  decision_tiers: DecisionTier[] | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface RoutingPolicyCreatePayload {
  name: string;
  scenario?: string;
  decision_model_id?: number | null;
  decision_content_limit?: number;
  decision_head_ratio?: number;
  decision_tiers?: DecisionTier[] | null;
  is_active?: boolean;
}

export type RoutingPolicyUpdatePayload = Partial<RoutingPolicyCreatePayload>;
