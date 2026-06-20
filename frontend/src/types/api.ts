export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue };

export type JsonObject = Record<string, JsonValue>;

export type RunStatus = "queued" | "running" | "completed" | "failed" | "cancelled";

export type RunStage =
  | "preparing"
  | "analyzing"
  | "planning"
  | "generating"
  | "executing"
  | "reflecting"
  | "reexecuting"
  | "summarizing";

export type StepType =
  | "plan"
  | "tool_call"
  | "observation"
  | "generation"
  | "reflection"
  | "report"
  | "system";

export interface ProjectOut {
  id: string;
  name: string;
  description: string | null;
  source_type: string;
  repo_url: string | null;
  local_path: string;
  default_branch: string | null;
  language: string;
  framework: string;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface ProjectSnapshotOut {
  id: string;
  project_id: string;
  source_kind: string;
  root_path: string;
  content_hash: string | null;
  created_at: string;
}

export interface TestPlanOut {
  id: string;
  project_id: string;
  name: string;
  target_scope: string[];
  goal: string;
  budget: JsonObject;
  output_options: JsonObject;
  default_strategy_version_id: string | null;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface StrategyVersionOut {
  id: string;
  name: string;
  workflow_type: string;
  model_provider: string;
  model_name: string;
  model_params: JsonObject;
  allow_reflection: boolean;
  max_tool_calls: number;
  prompt_ref: string;
  tool_schema_ref: string;
}

export interface StrategyOut {
  id: string;
  name: string;
  workflow_type: string;
  created_at: string;
}

export interface TestRunOut {
  id: string;
  test_plan_id: string;
  retry_of_run_id: string | null;
  project_snapshot_id: string;
  runtime_profile_id: string | null;
  strategy_version_id: string;
  runtime_snapshot: JsonObject;
  strategy_snapshot: JsonObject;
  status: RunStatus;
  stage: RunStage | null;
  started_at: string | null;
  finished_at: string | null;
  total_tokens: number;
  total_cost: number | string | null;
  tool_call_count: number;
  pytest_summary: JsonObject;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
}

export interface TraceStepOut {
  id: string;
  run_id: string;
  attempt_id: string | null;
  step_index: number;
  step_type: StepType;
  name: string;
  input_summary: string | null;
  output_summary: string | null;
  tool_name: string | null;
  payload: JsonObject | null;
  tokens: number | null;
  duration_ms: number | null;
  status: "ok" | "error" | string;
  error: string | null;
  created_at: string;
}

export interface RunEventOut {
  id: string;
  run_id: string;
  stage: RunStage | null;
  event_type: string;
  status_before: RunStatus | null;
  status_after: RunStatus | null;
  message: string | null;
  created_at: string;
}

export interface RunAttemptOut {
  id: string;
  run_plan_item_id: string;
  attempt_no: number;
  kind: "initial" | "reflection" | string;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  pytest_exit_code: number | null;
  error_code: string | null;
  reflection_reason: string | null;
}

export interface PytestCaseResultOut {
  id: string;
  attempt_id: string;
  generated_test_case_id: string | null;
  nodeid: string;
  mapping_status: "matched" | "unmatched" | string;
  status: "passed" | "failed" | "error" | "skipped" | string;
  duration_ms: number;
  failure_type: string | null;
  failure_message: string | null;
  traceback_hash: string | null;
  stdout_excerpt: string | null;
  stderr_excerpt: string | null;
  is_collection_error: boolean;
}

export interface RunArtifactOut {
  id: string;
  run_id: string;
  attempt_id: string | null;
  runner_job_id: string | null;
  artifact_type: string;
  uri: string;
  content_hash: string | null;
  size_bytes: number | null;
  metadata_json: JsonObject;
  created_at: string;
}

export interface TestReportOut {
  id: string;
  run_id: string;
  summary: string;
  metrics: JsonObject;
  risk_notes: string | null;
  markdown_artifact_id: string | null;
  json_artifact_id: string | null;
  markdown_uri: string | null;
  json_uri: string | null;
  created_at: string;
}

export interface RunCreateRequest {
  snapshot_id: string;
  strategy_version_id?: string | null;
  budget_override?: {
    timeout_seconds?: number | null;
    allow_reflection?: boolean | null;
  };
  output_options?: JsonObject;
}

export interface ProjectCreateRequest {
  name: string;
  local_path: string;
  description?: string | null;
}

export interface SnapshotCreateRequest {
  root_path?: string | null;
}

export interface TestPlanCreateRequest {
  project_id: string;
  name: string;
  target_scope: string[];
  goal: string;
  budget?: {
    timeout_seconds?: number | null;
    allow_reflection?: boolean | null;
  };
  output_options?: JsonObject;
  default_strategy_version_id?: string | null;
}

export interface RunBundle {
  run: TestRunOut;
  traceSteps: TraceStepOut[];
  events: RunEventOut[];
  attempts: RunAttemptOut[];
  pytestResults: PytestCaseResultOut[];
  artifacts: RunArtifactOut[];
  report: TestReportOut | null;
  strategy: StrategyVersionOut | null;
}

export interface ComparisonRow {
  strategy_id: string;
  strategy_name: string;
  repeats: number;
  captured_per_repeat: number[];
  captured_mean: number;
  total_in_scope: number;
  capture_rate_mean: number;
  capture_rate_std: number;
  false_positive_rate: number;
  avg_tokens: number;
  avg_tool_calls: number;
  reflection_used: boolean;
  cost_per_captured_bug: number | null;
}

export interface ComparisonResult {
  rows: ComparisonRow[];
  capture_matrix: Record<string, Record<string, boolean>>;
}

export interface EvalDatasetOut {
  id: string;
  name: string;
  version: string;
  description: string | null;
  project_snapshot_ids: string[];
  created_at: string;
}

export interface EvalTaskOut {
  id: string;
  dataset_id: string;
  project_snapshot_id: string;
  target_scope: JsonObject;
  goal: string;
  expected_capabilities: JsonValue[];
  created_at: string;
}

export interface SeededBugOut {
  id: string;
  eval_task_id: string;
  bug_type: string;
  description: string;
  expected_detection: string;
  created_at: string;
}

export interface BugVariantOut {
  id: string;
  seeded_bug_id: string;
  variant_name: string;
  canonical_kind: string;
  patch_artifact_id: string | null;
  mutated_snapshot_id: string | null;
  ground_truth: JsonObject;
  created_at: string;
}

export interface SeededBugDetailOut extends SeededBugOut {
  variants: BugVariantOut[];
}

export interface EvalTaskDetailOut extends EvalTaskOut {
  seeded_bugs: SeededBugDetailOut[];
}

export interface EvalDatasetDetailOut extends EvalDatasetOut {
  tasks: EvalTaskDetailOut[];
}

export interface EvalTaskCreateRequest {
  id?: string | null;
  project_snapshot_id: string;
  target_scope?: JsonObject;
  goal: string;
  expected_capabilities?: JsonValue[];
}

export interface SeededBugCreateRequest {
  id?: string | null;
  bug_type: string;
  description: string;
  expected_detection: string;
}

export interface CanonicalPatch {
  file: string;
  old: string;
  new: string;
}

export interface BugVariantCreateRequest {
  id?: string | null;
  variant_name: string;
  canonical_kind?: "patch";
  patch: CanonicalPatch;
  mutated_snapshot_id?: string | null;
  ground_truth?: JsonObject;
}

export type MutationOperatorKind =
  | "comparison_boundary"
  | "comparison_negation"
  | "boolean_negation"
  | "arithmetic_operator"
  | "constant_replacement"
  | "return_value"
  | "exception_type"
  | "statement_deletion"
  | "unknown";

export type MutantSelectionStatus = "selected" | "not_selected" | "excluded";
export type MutantSelectedBy = "auto_sampler" | "manual";

export interface MutantMatcherContract {
  matcher_kind: "source_location_hash" | "operator_signature";
  source_path: string;
  start_line: number;
  end_line: number;
  original_content_hash: string;
  operator: MutationOperatorKind;
  target_symbol: string | null;
  context_hash: string | null;
}

export interface MutantSelectionContract {
  status: MutantSelectionStatus;
  selected_by: MutantSelectedBy;
  reason: string;
  sample_seed: number | null;
  sample_index: number | null;
}

export interface MutationCandidateContract {
  candidate_id: string;
  eval_task_id: string;
  source_snapshot_id: string;
  operator: MutationOperatorKind;
  patch: CanonicalPatch;
  matcher: MutantMatcherContract;
  selection: MutantSelectionContract;
  probe: JsonObject;
}

export type MutationDiscoveryExclusionCode =
  | "syntax_error"
  | "target_not_found"
  | "source_segment_unavailable"
  | "unsupported_compare"
  | "non_unique_patch";

export interface MutationDiscoveryExclusionContract {
  reason_code: MutationDiscoveryExclusionCode;
  message: string;
  target_ref: string | null;
  source_path: string | null;
  line: number | null;
}

export interface MutationDiscoveryResultContract {
  eval_task_id: string;
  source_snapshot_id: string;
  sample_seed: number;
  max_selected: number;
  candidates: MutationCandidateContract[];
  exclusions: MutationDiscoveryExclusionContract[];
  selected_count: number;
  excluded_count: number;
}

export interface MutationDiscoveryAuditReportContract {
  schema_version: "v2.mutation_discovery_audit";
  generated_at: string;
  eval_task_id: string;
  dataset_id: string;
  source_snapshot_id: string;
  target_scope: JsonObject | JsonValue[] | null;
  sample_seed: number;
  max_selected: number;
  dry_run: true;
  writes_database: false;
  runs_replay: false;
  selected_candidate_ids: string[];
  exclusion_summary: Partial<Record<MutationDiscoveryExclusionCode, number>>;
  discovery: MutationDiscoveryResultContract;
}

export interface MutationDiscoveryDryRunRequest {
  sample_seed?: number;
  max_selected?: number;
  target_scope_override?: JsonObject | JsonValue[] | null;
}

export interface MutationProbeSpec {
  target_kind: string;
  probe: string;
  clean_value: JsonValue;
  buggy_value: JsonValue;
}

export interface MutationCandidateConfirmRequest {
  audit_report: MutationDiscoveryAuditReportContract;
  candidate_id: string;
  probe: MutationProbeSpec;
  seeded_bug_id?: string | null;
  variant_id?: string | null;
  bug_type?: "auto_mutation";
  description?: string | null;
  expected_detection?: string | null;
  variant_name?: string | null;
}
export type ExperimentDataSourceKind = "mock" | "scripted" | "real_llm";
export type ExperimentStatus = "draft" | "queued" | "running" | "completed" | "failed" | "cancelled";
export type ReplayStatus = "queued" | "running" | "completed" | "failed" | "cancelled";

export interface TemperaturePolicy {
  mode: "strategy_default" | "fixed" | "repeat_jitter";
  value: number | null;
  seed: number | null;
}

export interface LlmOverride {
  provider: string | null;
  model: string | null;
  temperature_policy: TemperaturePolicy;
  max_output_tokens: number | null;
  model_params: JsonObject;
}

export interface ExperimentCreateRequest {
  id?: string | null;
  name: string;
  dataset_id: string;
  runtime_profile_id?: string | null;
  strategy_version_ids: string[];
  repeat_count: number;
  llm_override?: Partial<LlmOverride> | null;
}

export interface ResolvedLlmSpec {
  provider: string;
  model: string;
  temperature: number | null;
  max_output_tokens: number | null;
  model_params: JsonObject;
  resolution_order: ("strategy_version_defaults" | "experiment_llm_override" | "repeat_derivation")[];
  repeat_index: number;
  secret_included: false;
}

export interface ExperimentDefinition {
  id: string;
  name: string;
  dataset_id: string;
  runtime_profile_id: string | null;
  strategy_version_ids: string[];
  repeat_count: number;
  llm_override: LlmOverride | null;
  status: ExperimentStatus;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  error_code: string | null;
  error_message: string | null;
}

export interface ExperimentCleanRunOut {
  id: string;
  experiment_id: string;
  eval_task_id: string;
  strategy_version_id: string;
  repeat_index: number;
  clean_run_id: string;
  generated_test_set_artifact_id: string;
  false_positive: boolean;
  clean_metrics: JsonObject;
  created_at: string;
}

export interface TestReplayOut {
  id: string;
  experiment_clean_run_id: string;
  generated_test_set_artifact_id: string;
  target_snapshot_id: string;
  bug_variant_id: string | null;
  status: ReplayStatus;
  pytest_summary: JsonObject;
  runtime_snapshot: RuntimeSnapshotContract;
  executor_metadata: Record<string, unknown>;
  workspace_manifest: Record<string, unknown>;
  cache_key: string | null;
  cache_status: "miss" | "hit" | "stale";
  source_replay_id: string | null;
  replay_mode: string;
  llm_calls: number;
  started_at: string | null;
  finished_at: string | null;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
}

export interface ExperimentDataSourceLabel {
  kind: ExperimentDataSourceKind;
  label: string;
  provider_label: string | null;
  model_label: string | null;
  generated_by: string;
}

export interface RuntimeSnapshotContract {
  runtime_profile_id: string | null;
  runtime_profile_name?: string | null;
  executor: "local_subprocess" | "docker";
  image?: string | null;
  working_dir?: string | null;
  python_version: string | null;
  install_command?: string | null;
  test_command: string;
  network_policy: "default" | "disabled" | "install_only";
  timeout_seconds: number;
  resource_limits: JsonObject;
  artifact_policy?: JsonObject;
  cleanup_policy?: JsonObject;
  executor_capabilities?: JsonObject;
  env_keys: string[];
  secret_included: false;
}

export interface RuntimeProfileOut {
  id: string;
  project_id: string;
  name: string;
  executor: "local_subprocess" | "docker";
  image: string | null;
  working_dir: string | null;
  python_version: string | null;
  install_command: string | null;
  test_command: string;
  env_template: JsonObject;
  resource_limits: JsonObject;
  network_policy: "default" | "disabled" | "install_only";
  artifact_policy: JsonObject;
  cleanup_policy: JsonObject;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface RuntimeProfileUpsertRequest {
  name: string;
  executor?: "local_subprocess" | "docker";
  image?: string | null;
  working_dir?: string | null;
  test_command?: string | null;
  python_version?: string | null;
  install_command?: string | null;
  env_template?: JsonObject;
  resource_limits?: JsonObject;
  network_policy?: "default" | "disabled" | "install_only";
  timeout_seconds?: number | null;
  artifact_policy?: JsonObject;
  cleanup_policy?: JsonObject;
}

export interface RuntimeExecutionSummary {
  executor_kind_distribution: Record<string, number>;
  runtime_profiles: JsonObject[];
  replay_cache_counts: Record<string, number>;
  setup_status_counts: Record<string, number>;
  replay_concurrency: JsonObject;
  observed_replay_count: number;
  reused_replay_count: number;
}

export interface StrategySnapshotContract {
  strategy_version_id: string;
  prompt_version_id: string;
  prompt_content_hash: string;
  tool_schema_version_id: string;
  tool_schema_content_hash: string;
  workflow_type: "direct" | "plan_execute" | "react_reflection";
  allow_reflection: boolean;
  resolved_llm: ResolvedLlmSpec;
}

export interface SourceContextSnippet {
  path: string;
  target_ref: string;
  start_line: number;
  end_line: number;
  content_hash: string;
  bytes: number;
  trace_step_id: string | null;
}

export interface ContextCompletenessEvidence {
  status: "complete" | "partial" | "incomplete";
  context_incomplete: boolean;
  snippets: SourceContextSnippet[];
  missing_targets: string[];
  risk_notes: string[];
}

export interface TargetMappingEvidence {
  target_type: "function" | "route" | "model" | "module" | "unknown";
  target_ref: string;
  source_path: string | null;
  symbol: string | null;
  mapping_status: "mapped" | "partial" | "unmapped";
  trace_step_id: string | null;
}

export interface TestInventoryEvidence {
  test_name: string;
  source_path: string;
  start_line: number;
  end_line: number;
  nodeid: string | null;
}

export interface AssertionEvidence {
  test_name: string;
  nodeid: string | null;
  assertion_summary: string;
  target_ref: string | null;
}

export interface FailureClassificationEvidence {
  nodeid: string;
  classification: "assertion" | "error" | "import" | "collection" | "fixture" | "timeout";
  message: string | null;
}

export interface ReflectionEvidence {
  used: boolean;
  contract_checked: boolean;
  contract_passed: boolean | null;
  violation_reasons: string[];
  accepted_attempt_id: string | null;
  rejected_attempt_ids: string[];
}

export interface ReportQualityEvidence {
  test_inventory: TestInventoryEvidence[];
  target_mappings: TargetMappingEvidence[];
  assertion_summaries: AssertionEvidence[];
  failure_classifications: FailureClassificationEvidence[];
  reflection_evidence: ReflectionEvidence;
  context_completeness: ContextCompletenessEvidence;
}

export interface CleanRunContract {
  id: string;
  experiment_id: string;
  eval_task_id: string;
  strategy_version_id: string;
  repeat_index: number;
  clean_run_id: string;
  status: RunStatus;
  generated_test_set_artifact_id: string;
  false_positive: boolean;
  clean_metrics: CleanRunMetricsContract;
  runtime_snapshot: RuntimeSnapshotContract;
  strategy_snapshot: StrategySnapshotContract;
  report_quality: ReportQualityEvidence;
}

export interface CleanRunMetricsContract {
  final_cases_total: number;
  final_passed: number;
  final_failed: number;
  final_skipped: number;
  collection_errors: number;
  tool_call_count: number;
  total_tokens: number;
  validity_status?: "evaluable" | "invalid_test_set";
  invalid_reason?: string | null;
  pipeline_reject_error?: string | null;
  context_completeness?: ContextCompletenessEvidence | null;
  analysis_warnings?: string[];
}

export interface PytestSummaryContract {
  collected: number;
  passed: number;
  failed: number;
  skipped: number;
  collection_errors: number;
  duration_ms: number | null;
}

export interface TestReplayContract {
  id: string;
  experiment_clean_run_id: string;
  generated_test_set_artifact_id: string;
  target_snapshot_id: string;
  bug_variant_id: string | null;
  status: ReplayStatus;
  pytest_summary: PytestSummaryContract;
  runtime_snapshot: RuntimeSnapshotContract;
  executor_metadata: Record<string, unknown>;
  workspace_manifest: Record<string, unknown>;
  cache_key: string | null;
  cache_status: "miss" | "hit" | "stale";
  source_replay_id: string | null;
  replay_mode: "frozen_test_set";
  llm_calls: 0;
  started_at: string | null;
  finished_at: string | null;
  error_code: string | null;
  error_message: string | null;
}

export interface ReplayMetricsContract {
  capture_rule: "clean_passed_variant_assertion_failure_same_nodeid";
  clean_passed_nodeids: string[];
  variant_assertion_failure_nodeids: string[];
  capturing_nodeids: string[];
}

export interface ExperimentReplayRunContract {
  id: string;
  experiment_clean_run_id: string;
  bug_variant_id: string;
  replay_id: string;
  captured_bug: boolean;
  replay_metrics: ReplayMetricsContract;
}

export interface LlmDisplayEvidence {
  provider: string;
  model: string;
  derived_from_clean_run_ids: string[];
  source: "derived_from_clean_run_strategy_snapshots";
}

export interface ExperimentMetricRow extends ComparisonRow {
  invalid_test_set_count: number;
  metric_status: "ok" | "invalid_test_set" | "evaluable_zero_capture";
  cost_per_captured_bug_status: "ok" | "no_bug_captured";
  data_source: ExperimentDataSourceKind;
  llm_display: LlmDisplayEvidence;
  runtime_execution?: RuntimeExecutionSummary;
}

export interface SourceContextPolicy {
  project_root_boundary: "project_snapshot_root";
  read_allowlist_globs: string[];
  deny_globs: string[];
  generated_tests_dir: string;
  max_file_bytes: number;
  max_total_bytes: number;
  max_snippets: number;
  trace_payload_required: boolean;
  missing_context_marker: "context_incomplete";
  agent_source_write_permission: "generated_tests_only";
}

export type EvaluationEventType =
  | "invalid_test_set"
  | "context_incomplete_blocking"
  | "flaky_clean_replay"
  | "probe_check_passed"
  | "probe_check_failed"
  | "replay_captured"
  | "replay_uncaptured"
  | "replay_failure"
  | "artifact_hash_mismatch"
  | "provider_failure"
  | "executor_unavailable"
  | "executor_timeout"
  | "setup_failed"
  | "cleanup_failed"
  | "resource_limit_exceeded"
  | "network_policy_violation"
  | "replay_cache_hit"
  | "replay_cache_stale";

export interface EvaluationEventContract {
  event_id: string;
  event_type: EvaluationEventType;
  severity: "info" | "warning" | "error" | "blocking";
  scope: "experiment" | "clean_run" | "replay" | "variant" | "task" | "artifact";
  experiment_id: string;
  clean_run_id: string | null;
  replay_id: string | null;
  bug_variant_id: string | null;
  eval_task_id: string | null;
  strategy_version_id: string | null;
  repeat_index: number | null;
  stable_code: string;
  reason: string;
  source_ids: Record<string, string>;
  artifact_ids: string[];
  nodeids: string[];
  payload: Record<string, unknown>;
  created_from: "experiment_service_projection";
}

export interface ReflectionEventBackfeedDecisionContract {
  event_id: string;
  event_type: EvaluationEventType;
  action: "included" | "filtered";
  reason: string;
}

export interface ReflectionEventBackfeedContract {
  clean_run_id: string;
  eval_task_id: string;
  strategy_version_id: string;
  repeat_index: number;
  max_events: number;
  included_event_ids: string[];
  decisions: ReflectionEventBackfeedDecisionContract[];
  created_from: "evaluation_event_projection";
}

export type SampledMutationScoreStatus = "ok" | "not_applicable" | "incomplete_replay";

export interface SampledMutationScoreRow {
  strategy_id: string;
  strategy_name: string;
  mutant_count: number;
  repeat_count: number;
  expected_replay_count: number;
  observed_replay_count: number;
  captured_mutant_count: number;
  score: number | null;
  status: SampledMutationScoreStatus;
}

export interface SampledMutationMatrixStats {
  captured: boolean;
  captured_count: number;
  replayed_count: number;
  repeat_count: number;
  capture_rate: number;
}

export interface SampledMutationScoreContract {
  status: SampledMutationScoreStatus;
  unit: "auto_mutation_bug_variant";
  denominator_source: "bug_variants.ground_truth.source=auto_mutation + ground_truth.probe.probe_check.status=passed";
  mutant_count: number;
  included_variant_ids: string[];
  excluded_variant_counts: Record<string, number>;
  rows: SampledMutationScoreRow[];
  matrix_counts: Record<string, Record<string, SampledMutationMatrixStats>>;
}

export interface ArtifactMetadataContract {
  kind:
    | "generated_test_set"
    | "pytest_json"
    | "junit_xml"
    | "report_markdown"
    | "report_json"
    | "bug_patch"
    | "experiment_metrics";
  created_by: "agent" | "executor" | "experiment_service" | "seed_script";
  experiment_id: string | null;
  clean_run_id: string | null;
  replay_id: string | null;
  content_role: string;
  relative_paths: string[];
  hash_algorithm: "sha256";
}

export interface ArtifactContract {
  id: string;
  artifact_type: ArtifactMetadataContract["kind"];
  uri: string;
  content_hash: string;
  size_bytes: number;
  metadata: ArtifactMetadataContract;
}

export interface ExperimentMetricsResponse {
  schema_version: "v2.phase0";
  generated_at: string;
  data_source: ExperimentDataSourceLabel;
  experiment: ExperimentDefinition;
  source_context_policy: SourceContextPolicy;
  runtime_execution: RuntimeExecutionSummary;
  rows: ExperimentMetricRow[];
  capture_matrix: Record<string, Record<string, boolean>>;
  capture_matrix_counts?: Record<
    string,
    Record<
      string,
      {
        captured: boolean;
        captured_count: number;
        repeat_count: number;
        capture_rate: number;
      }
    >
  >;
  sampled_mutation_score?: SampledMutationScoreContract;
  clean_runs: CleanRunContract[];
  replay_runs: TestReplayContract[];
  experiment_replay_runs: ExperimentReplayRunContract[];
  evaluation_events?: EvaluationEventContract[];
  reflection_event_backfeed?: ReflectionEventBackfeedContract[];
  artifacts: ArtifactContract[];
}
