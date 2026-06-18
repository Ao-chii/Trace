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
  executor: "local_subprocess" | "docker";
  python_version: string | null;
  test_command: string;
  network_policy: "default" | "disabled" | "install_only";
  timeout_seconds: number;
  resource_limits: JsonObject;
  env_keys: string[];
  secret_included: false;
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
  clean_runs: CleanRunContract[];
  replay_runs: TestReplayContract[];
  experiment_replay_runs: ExperimentReplayRunContract[];
  artifacts: ArtifactContract[];
}
