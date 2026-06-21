<script setup lang="ts">
import { computed, onMounted, ref, shallowRef, watch } from "vue";
import { ArrowLeft, Bug, CheckCircle2, Database, FlaskConical, GitBranch, RefreshCw } from "@lucide/vue";
import { confirmSelectedMutationCandidate, createBugVariant, createEvalDataset, createEvalTask, createSeededBug, dryRunTaskMutationDiscovery, getEvalDataset, getEvalDatasetReadiness, updateBugVariant, updateEvalTask, updateSeededBug } from "../api/evaluation";
import JsonViewer from "../components/JsonViewer.vue";
import { demoEvalDataset } from "../demo/staticRunFixture";
import { useLatestRequest } from "../composables/useLatestRequest";
import { useI18n } from "../i18n";
import type {
  BugVariantOut,
  DatasetReadinessOut,
  EvalDatasetDetailOut,
  EvalTaskDetailOut,
  JsonObject,
  SeededBugDetailOut,
  JsonValue,
  MutationCandidateContract,
  MutationDiscoveryAuditReportContract,
  MutationDiscoveryExclusionCode,
  MutationDiscoveryResultContract,
  MutationProbeSpec
} from "../types/api";
import type { DataSource } from "../types/ui";

const props = defineProps<{
  datasetId: string;
  dataSource: DataSource;
}>();

const emit = defineEmits<{
  navigate: [hash: string];
}>();

const { t } = useI18n();

const dataset = shallowRef<EvalDatasetDetailOut | null>(null);
const serverDatasetReadiness = shallowRef<DatasetReadinessOut | null>(null);
const loading = ref(false);
const errorMessage = ref<string | null>(null);
const selectedTaskId = ref<string | null>(null);
const creating = ref(false);
const createMessage = ref<string | null>(null);
const createForm = ref({
  id: "",
  name: "local eval dataset",
  version: "v1",
  description: "",
  projectSnapshotIds: ""
});

const mutationDiscovery = shallowRef<MutationDiscoveryResultContract | null>(null);
const mutationLoading = ref(false);
const mutationConfirming = ref(false);
const mutationError = ref<string | null>(null);
const mutationMessage = ref<string | null>(null);
const selectedCandidateId = ref<string | null>(null);
const discoveryForm = ref({
  sampleSeed: 0,
  maxSelected: 20
});
const confirmForm = ref({
  seededBugId: "",
  variantId: "",
  description: "",
  expectedDetection: "",
  variantName: "",
  probeJson: '{\n  "target_kind": "function",\n  "probe": "",\n  "clean_value": null,\n  "buggy_value": null\n}'
});
const authoring = ref(false);
const editingTaskId = ref<string | null>(null);
const editingBugId = ref<string | null>(null);
const editingVariantId = ref<string | null>(null);
const taskForm = ref({
  id: "",
  projectSnapshotId: "",
  goal: "",
  expectedCapabilities: "",
  targetScopeJson: "{}"
});
const bugForm = ref({
  id: "",
  bugType: "semantic",
  description: "",
  expectedDetection: ""
});
const variantForm = ref({
  seededBugId: "",
  id: "",
  variantName: "",
  patchFile: "",
  patchOld: "",
  patchNew: "",
  mutatedSnapshotId: "",
  groundTruthJson: "{}"
});
const datasetRequest = useLatestRequest();

const isCreateOnly = computed(() => props.dataSource === "api" && props.datasetId === "new");
const selectedTask = computed<EvalTaskDetailOut | null>(() => {
  const current = dataset.value;
  if (!current) {
    return null;
  }
  return current.tasks.find((task) => task.id === selectedTaskId.value) ?? current.tasks[0] ?? null;
});
const bugCount = computed<number>(() => {
  const current = dataset.value;
  return current ? current.tasks.reduce((count, task) => count + task.seeded_bugs.length, 0) : 0;
});
const variantCount = computed<number>(() => {
  const current = dataset.value;
  return current
    ? current.tasks.reduce((count, task) => count + task.seeded_bugs.reduce((inner, bug) => inner + bug.variants.length, 0), 0)
    : 0;
});

const availableSnapshotIds = computed(() => dataset.value?.project_snapshot_ids.map((item) => String(item)) ?? []);

type ReadinessStatus = "ready" | "incomplete";
type TaskReadinessFilter = "all" | ReadinessStatus;

const taskSearchQuery = ref("");
const taskReadinessFilter = ref<TaskReadinessFilter>("all");


type VariantConfirmationStatus = "ordinary" | "confirmed" | "probe_failed" | "probe_missing";

function objectField(value: JsonObject | null, key: string): JsonObject | null {
  const next = value?.[key];
  if (next && typeof next === "object" && !Array.isArray(next)) {
    return next as JsonObject;
  }
  return null;
}

function stringField(value: JsonObject | null, key: string): string | null {
  const next = value?.[key];
  return typeof next === "string" ? next : null;
}

function compactJson(value: JsonValue | undefined): string {
  if (typeof value === "string") {
    return value;
  }
  if (value === undefined) {
    return "-";
  }
  return JSON.stringify(value);
}

function variantGroundTruthSource(variant: BugVariantOut): string {
  return stringField(variant.ground_truth, "source") ?? "seeded_bug";
}

function variantProbeCheck(variant: BugVariantOut): JsonObject | null {
  return objectField(objectField(variant.ground_truth, "probe"), "probe_check");
}

function probeCheckFailureDetail(probeCheck: JsonObject): string | null {
  for (const key of ["reason", "message", "error", "detail"]) {
    const value = stringField(probeCheck, key);
    if (value?.trim()) {
      return value.trim();
    }
  }
  return null;
}

function variantConfirmationStatus(variant: BugVariantOut): VariantConfirmationStatus {
  if (variantGroundTruthSource(variant) !== "auto_mutation") {
    return "ordinary";
  }
  const status = stringField(variantProbeCheck(variant), "status");
  if (status === "passed") {
    return "confirmed";
  }
  if (status === "failed") {
    return "probe_failed";
  }
  return "probe_missing";
}

function variantConfirmationLabel(variant: BugVariantOut): string {
  const status = variantConfirmationStatus(variant);
  if (status === "confirmed") {
    return t("datasets.confirmationConfirmed");
  }
  if (status === "probe_failed") {
    return t("datasets.confirmationProbeFailed");
  }
  if (status === "probe_missing") {
    return t("datasets.confirmationProbeMissing");
  }
  return t("datasets.confirmationOrdinary");
}

function variantConfirmationDetail(variant: BugVariantOut): string {
  if (variantGroundTruthSource(variant) !== "auto_mutation") {
    return t("datasets.confirmationOrdinaryDetail");
  }
  const probe = objectField(variant.ground_truth, "probe");
  const probeCheck = variantProbeCheck(variant);
  const probeExpr = stringField(probe, "probe") ?? t("common.unknown");
  if (!probeCheck) {
    return t("datasets.confirmationProbeMissingDetail", { probe: probeExpr });
  }
  const failureDetail = probeCheckFailureDetail(probeCheck);
  if (failureDetail) {
    return t("datasets.confirmationProbeFailureDetail", { probe: probeExpr, reason: failureDetail });
  }
  const clean = `${compactJson(probeCheck.clean_actual)} / ${compactJson(probeCheck.clean_expected)}`;
  const buggy = `${compactJson(probeCheck.buggy_actual)} / ${compactJson(probeCheck.buggy_expected)}`;
  return t("datasets.confirmationProbeDetail", { probe: probeExpr, clean, buggy });
}

type DatasetReadinessIssue = {
  id: string;
  label: string;
  detail: string;
};

function hasEvidence(value: unknown): boolean {
  if (Array.isArray(value)) {
    return value.length > 0;
  }
  if (value && typeof value === "object") {
    return Object.keys(value as Record<string, unknown>).length > 0;
  }
  if (typeof value === "string") {
    return value.trim().length > 0;
  }
  return value !== null && value !== undefined;
}

function readinessStatus(issues: DatasetReadinessIssue[]): ReadinessStatus {
  return issues.length === 0 ? "ready" : "incomplete";
}

function bugReadinessIssues(bug: SeededBugDetailOut): DatasetReadinessIssue[] {
  const issues: DatasetReadinessIssue[] = [];
  if (bug.variants.length === 0) {
    issues.push({
      id: `bug:${bug.id}:variants`,
      label: t("datasets.issueBugNoVariantsLabel"),
      detail: t("datasets.issueBugNoVariantsDetail", { id: bug.id })
    });
  }
  for (const variant of bug.variants) {
    if (!hasEvidence(variant.ground_truth)) {
      issues.push({
        id: `variant:${variant.id}:ground_truth`,
        label: t("datasets.issueVariantNoGroundTruthLabel"),
        detail: t("datasets.issueVariantNoGroundTruthDetail", { id: variant.id })
      });
    }
  }
  return issues;
}

function taskReadinessIssues(task: EvalTaskDetailOut): DatasetReadinessIssue[] {
  const issues: DatasetReadinessIssue[] = [];
  if (!hasEvidence(task.target_scope)) {
    issues.push({
      id: `task:${task.id}:target_scope`,
      label: t("datasets.issueMissingScopeLabel"),
      detail: t("datasets.issueMissingScopeDetail", { id: task.id })
    });
  }
  if (task.seeded_bugs.length === 0) {
    issues.push({
      id: `task:${task.id}:seeded_bugs`,
      label: t("datasets.issueTaskNoBugsLabel"),
      detail: t("datasets.issueTaskNoBugsDetail", { id: task.id })
    });
  }
  for (const bug of task.seeded_bugs) {
    issues.push(...bugReadinessIssues(bug));
  }
  return issues;
}

function taskReadinessStatus(task: EvalTaskDetailOut): ReadinessStatus {
  return readinessStatus(taskReadinessIssues(task));
}

function bugReadinessStatus(bug: SeededBugDetailOut): ReadinessStatus {
  return readinessStatus(bugReadinessIssues(bug));
}

const datasetReadiness = computed(() => {
  const current = dataset.value;
  const issues: DatasetReadinessIssue[] = [];
  if (!current) {
    return { status: "incomplete" as ReadinessStatus, issues };
  }
  if (current.tasks.length === 0) {
    issues.push({
      id: "dataset:tasks",
      label: t("datasets.issueNoTasksLabel"),
      detail: t("datasets.issueNoTasksDetail")
    });
  }
  for (const task of current.tasks) {
    issues.push(...taskReadinessIssues(task));
  }
  return { status: readinessStatus(issues), issues };
});



const displayedDatasetReadiness = computed(() => {
  const server = serverDatasetReadiness.value;
  if (!server) {
    return datasetReadiness.value;
  }
  return {
    status: server.status,
    issues: server.issues.map((issue) => ({
      id: issue.id,
      label: issue.code,
      detail: issue.message
    }))
  };
});
function taskSearchText(task: EvalTaskDetailOut): string {
  return [
    task.id,
    task.project_snapshot_id,
    task.goal,
    capabilityText(task),
    JSON.stringify(task.target_scope),
    ...task.seeded_bugs.flatMap((bug) => [
      bug.id,
      bug.bug_type,
      bug.description,
      bug.expected_detection,
      ...bug.variants.flatMap((variant) => [
        variant.id,
        variant.variant_name,
        variantGroundTruthSource(variant),
        variantConfirmationStatus(variant),
        variantConfirmationDetail(variant)
      ])
    ])
  ]
    .join(" ")
    .toLowerCase();
}

const filteredTasks = computed<EvalTaskDetailOut[]>(() => {
  const current = dataset.value;
  if (!current) {
    return [];
  }
  const query = taskSearchQuery.value.trim().toLowerCase();
  return current.tasks.filter((task) => {
    const status = taskReadinessStatus(task);
    const statusMatches = taskReadinessFilter.value === "all" || taskReadinessFilter.value === status;
    const queryMatches = !query || taskSearchText(task).includes(query);
    return statusMatches && queryMatches;
  });
});

const taskReadinessSummary = computed(() => {
  const tasks = dataset.value?.tasks ?? [];
  let ready = 0;
  let incomplete = 0;
  for (const task of tasks) {
    if (taskReadinessStatus(task) === "ready") {
      ready += 1;
    } else {
      incomplete += 1;
    }
  }
  return {
    total: tasks.length,
    ready,
    incomplete,
    visible: filteredTasks.value.length,
    hidden: Math.max(0, tasks.length - filteredTasks.value.length)
  };
});

const selectedMutationCandidates = computed<MutationCandidateContract[]>(() =>
  (mutationDiscovery.value?.candidates ?? []).filter((candidate) => candidate.selection.status === "selected")
);
const selectedCandidate = computed<MutationCandidateContract | null>(() => {
  const candidates = mutationDiscovery.value?.candidates ?? [];
  return candidates.find((candidate) => candidate.candidate_id === selectedCandidateId.value) ?? null;
});
const canConfirmMutation = computed(() =>
  props.dataSource === "api" &&
  Boolean(selectedTask.value) &&
  Boolean(mutationDiscovery.value) &&
  selectedCandidate.value?.selection.status === "selected" &&
  !mutationConfirming.value
);

function resetMutationState() {
  mutationDiscovery.value = null;
  mutationError.value = null;
  mutationMessage.value = null;
  selectedCandidateId.value = null;
}

function asPositiveInteger(value: number, fallback: number): number {
  if (!Number.isFinite(value)) {
    return fallback;
  }
  return Math.max(0, Math.trunc(value));
}

function mutationStatusLabel(candidate: MutationCandidateContract): string {
  return candidate.selection.status === "selected" ? t("datasets.selected") : t("datasets.notSelected");
}

function buildExclusionSummary(discovery: MutationDiscoveryResultContract): Partial<Record<MutationDiscoveryExclusionCode, number>> {
  return discovery.exclusions.reduce<Partial<Record<MutationDiscoveryExclusionCode, number>>>((summary, exclusion) => {
    summary[exclusion.reason_code] = (summary[exclusion.reason_code] ?? 0) + 1;
    return summary;
  }, {});
}

function slugForResourceId(value: string, fallback: string): string {
  const slug = value
    .toLowerCase()
    .replace(/[^a-z0-9_.-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 42);
  return slug || fallback;
}

function probePresetForCandidate(candidate: MutationCandidateContract): MutationProbeSpec {
  const rawProbe = candidate.probe as Partial<MutationProbeSpec>;
  if (
    typeof rawProbe.target_kind === "string" &&
    typeof rawProbe.probe === "string" &&
    rawProbe.target_kind.trim() &&
    rawProbe.probe.trim() &&
    "clean_value" in rawProbe &&
    "buggy_value" in rawProbe
  ) {
    return {
      target_kind: rawProbe.target_kind,
      probe: rawProbe.probe,
      clean_value: rawProbe.clean_value as JsonValue,
      buggy_value: rawProbe.buggy_value as JsonValue
    };
  }
  const target = candidate.matcher.target_symbol ?? candidate.matcher.source_path;
  return {
    target_kind: candidate.matcher.target_symbol ? "function" : "module",
    probe: candidate.matcher.target_symbol ? target + "(...)" : target + ": " + candidate.patch.old + " -> " + candidate.patch.new,
    clean_value: null,
    buggy_value: null
  };
}

function fillMutationConfirmPreset(candidate: MutationCandidateContract) {
  const slug = slugForResourceId(candidate.matcher.target_symbol ?? candidate.operator, candidate.operator);
  confirmForm.value.seededBugId = `bug-auto-${slug}`.slice(0, 64);
  confirmForm.value.variantId = `variant-auto-${slug}`.slice(0, 64);
  confirmForm.value.variantName = `${candidate.operator} mutation`;
  confirmForm.value.description = `Auto mutation candidate ${candidate.operator} in ${candidate.matcher.source_path}`;
  confirmForm.value.expectedDetection = "A generated test should pass on clean replay and fail on this confirmed mutated behavior.";
  confirmForm.value.probeJson = JSON.stringify(probePresetForCandidate(candidate), null, 2);
}

function selectMutationCandidate(candidate: MutationCandidateContract) {
  selectedCandidateId.value = candidate.candidate_id;
  if (candidate.selection.status === "selected") {
    fillMutationConfirmPreset(candidate);
  }
}

function buildAuditReport(task: EvalTaskDetailOut, discovery: MutationDiscoveryResultContract): MutationDiscoveryAuditReportContract {
  return {
    schema_version: "v2.mutation_discovery_audit",
    generated_at: new Date().toISOString(),
    eval_task_id: task.id,
    dataset_id: task.dataset_id,
    source_snapshot_id: task.project_snapshot_id,
    target_scope: task.target_scope,
    sample_seed: discovery.sample_seed,
    max_selected: discovery.max_selected,
    dry_run: true,
    writes_database: false,
    runs_replay: false,
    selected_candidate_ids: discovery.candidates
      .filter((candidate) => candidate.selection.status === "selected")
      .map((candidate) => candidate.candidate_id),
    exclusion_summary: buildExclusionSummary(discovery),
    discovery
  };
}

function parseProbeJson(): MutationProbeSpec {
  const parsed = JSON.parse(confirmForm.value.probeJson) as unknown;
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(t("datasets.probeJsonInvalid"));
  }
  const probe = parsed as Partial<MutationProbeSpec>;
  if (typeof probe.target_kind !== "string" || typeof probe.probe !== "string" || !probe.target_kind.trim() || !probe.probe.trim()) {
    throw new Error(t("datasets.probeJsonInvalid"));
  }
  if (!("clean_value" in probe) || !("buggy_value" in probe)) {
    throw new Error(t("datasets.probeJsonInvalid"));
  }
  return {
    target_kind: probe.target_kind,
    probe: probe.probe,
    clean_value: probe.clean_value as JsonValue,
    buggy_value: probe.buggy_value as JsonValue
  };
}

async function runMutationDiscovery() {
  const task = selectedTask.value;
  if (props.dataSource !== "api" || !task) {
    return;
  }
  mutationLoading.value = true;
  mutationError.value = null;
  mutationMessage.value = null;
  try {
    const result = await dryRunTaskMutationDiscovery(task.id, {
      sample_seed: asPositiveInteger(discoveryForm.value.sampleSeed, 0),
      max_selected: asPositiveInteger(discoveryForm.value.maxSelected, 20)
    });
    mutationDiscovery.value = result;
    selectedCandidateId.value = selectedMutationCandidates.value[0]?.candidate_id ?? null;
    mutationMessage.value = t("datasets.discoveryComplete");
  } catch (error) {
    mutationDiscovery.value = null;
    selectedCandidateId.value = null;
    mutationError.value = error instanceof Error ? error.message : t("datasets.discoveryFailed");
  } finally {
    mutationLoading.value = false;
  }
}

async function confirmMutationCandidate() {
  const task = selectedTask.value;
  const discovery = mutationDiscovery.value;
  const candidate = selectedCandidate.value;
  if (!task || !discovery || !candidate || candidate.selection.status !== "selected") {
    mutationError.value = t("datasets.selectCandidateFirst");
    return;
  }
  mutationConfirming.value = true;
  mutationError.value = null;
  mutationMessage.value = null;
  try {
    const probe = parseProbeJson();
    const confirmed = await confirmSelectedMutationCandidate(task.id, {
      audit_report: buildAuditReport(task, discovery),
      candidate_id: candidate.candidate_id,
      probe,
      bug_type: "auto_mutation",
      seeded_bug_id: confirmForm.value.seededBugId.trim() || null,
      variant_id: confirmForm.value.variantId.trim() || null,
      description: confirmForm.value.description.trim() || null,
      expected_detection: confirmForm.value.expectedDetection.trim() || null,
      variant_name: confirmForm.value.variantName.trim() || null
    });
    mutationMessage.value = `${t("datasets.confirmedMutation")}: ${confirmed.id}`;
    await loadDataset();
    mutationDiscovery.value = null;
    selectedCandidateId.value = null;
  } catch (error) {
    mutationError.value = error instanceof Error ? error.message : t("datasets.confirmFailed");
  } finally {
    mutationConfirming.value = false;
  }
}

function formatDate(value: string): string {
  return new Date(value).toLocaleString();
}

function capabilityText(task: EvalTaskDetailOut): string {
  return task.expected_capabilities.length > 0 ? task.expected_capabilities.map((item) => String(item)).join(", ") : t("common.none");
}

function parseJsonObject(value: string, messageKey: string): JsonObject {
  const parsed = JSON.parse(value || "{}") as unknown;
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(t(messageKey));
  }
  return parsed as JsonObject;
}

function commaList(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function formatJsonInput(value: JsonObject): string {
  return JSON.stringify(value, null, 2);
}

const demoTargetScopePreset: JsonObject = {
  files: ["checkout/pricing.py", "checkout/status.py"],
  symbols: ["checkout.pricing.calculate_total", "checkout.status.resolve_status"]
};

const ordinaryGroundTruthPreset: JsonObject = {
  source: "seeded_bug",
  target: "checkout.pricing.calculate_total",
  expected_behavior: "clean tests pass; the same frozen tests fail on the patched variant",
  capture_signal: "assertion failure on buggy snapshot, not import/setup/runtime failure"
};

function firstSnapshotId(): string {
  return availableSnapshotIds.value[0] ?? "";
}

function fillTaskDemoPreset() {
  if (!editingTaskId.value && !taskForm.value.projectSnapshotId) {
    taskForm.value.projectSnapshotId = firstSnapshotId();
  }
  taskForm.value.goal = "Generate pytest cases that catch pricing/status regressions while staying clean on the base snapshot.";
  taskForm.value.expectedCapabilities = "boundary assertions, status-code assertions, frozen replay compatibility";
  taskForm.value.targetScopeJson = formatJsonInput(demoTargetScopePreset);
}

function fillOrdinaryVariantPreset() {
  const firstBug = selectedTask.value?.seeded_bugs[0];
  if (!editingVariantId.value && !variantForm.value.seededBugId) {
    variantForm.value.seededBugId = firstBug?.id ?? "";
  }
  if (!variantForm.value.variantName) {
    variantForm.value.variantName = "free shipping threshold off by one";
  }
  if (!editingVariantId.value) {
    variantForm.value.patchFile = variantForm.value.patchFile || "checkout/pricing.py";
    variantForm.value.patchOld = variantForm.value.patchOld || "subtotal >= 100";
    variantForm.value.patchNew = variantForm.value.patchNew || "subtotal > 100";
  }
  variantForm.value.groundTruthJson = formatJsonInput(ordinaryGroundTruthPreset);
}

function resetTaskForm() {
  editingTaskId.value = null;
  taskForm.value = {
    id: "",
    projectSnapshotId: "",
    goal: "",
    expectedCapabilities: "",
    targetScopeJson: "{}"
  };
}

function resetBugForm() {
  editingBugId.value = null;
  bugForm.value = {
    id: "",
    bugType: "semantic",
    description: "",
    expectedDetection: ""
  };
}

function resetVariantForm() {
  editingVariantId.value = null;
  variantForm.value = {
    seededBugId: "",
    id: "",
    variantName: "",
    patchFile: "",
    patchOld: "",
    patchNew: "",
    mutatedSnapshotId: "",
    groundTruthJson: "{}"
  };
}

function loadSelectedTaskForEdit() {
  const task = selectedTask.value;
  if (!task) {
    return;
  }
  authoring.value = true;
  editingTaskId.value = task.id;
  taskForm.value = {
    id: task.id,
    projectSnapshotId: task.project_snapshot_id,
    goal: task.goal,
    expectedCapabilities: task.expected_capabilities.map((item) => String(item)).join(", "),
    targetScopeJson: formatJsonInput(task.target_scope)
  };
}

function loadBugForEdit(bug: SeededBugDetailOut) {
  authoring.value = true;
  editingBugId.value = bug.id;
  bugForm.value = {
    id: bug.id,
    bugType: bug.bug_type,
    description: bug.description,
    expectedDetection: bug.expected_detection
  };
}

function loadVariantForEdit(variant: BugVariantOut) {
  authoring.value = true;
  editingVariantId.value = variant.id;
  variantForm.value = {
    seededBugId: variant.seeded_bug_id,
    id: variant.id,
    variantName: variant.variant_name,
    patchFile: "",
    patchOld: "",
    patchNew: "",
    mutatedSnapshotId: variant.mutated_snapshot_id ?? "",
    groundTruthJson: formatJsonInput(variant.ground_truth)
  };
}
async function submitTask() {
  if (props.dataSource !== "api" || !dataset.value) {
    return;
  }
  creating.value = true;
  errorMessage.value = null;
  createMessage.value = null;
  try {
    const targetScope = parseJsonObject(taskForm.value.targetScopeJson, "datasets.targetScopeJsonInvalid");
    if (editingTaskId.value) {
      const updated = await updateEvalTask(editingTaskId.value, {
        target_scope: targetScope,
        goal: taskForm.value.goal.trim(),
        expected_capabilities: commaList(taskForm.value.expectedCapabilities)
      });
      createMessage.value = `${t("datasets.updatedTask")}: ${updated.id}`;
      await loadDataset();
      selectedTaskId.value = updated.id;
      resetTaskForm();
      return;
    }
    const created = await createEvalTask(dataset.value.id, {
      id: taskForm.value.id.trim() || null,
      project_snapshot_id: taskForm.value.projectSnapshotId.trim(),
      target_scope: targetScope,
      goal: taskForm.value.goal.trim(),
      expected_capabilities: commaList(taskForm.value.expectedCapabilities)
    });
    createMessage.value = `${t("datasets.createdTask")}: ${created.id}`;
    await loadDataset();
    selectedTaskId.value = created.id;
    resetTaskForm();
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : t(editingTaskId.value ? "datasets.updateTaskFailed" : "datasets.createTaskFailed");
  } finally {
    creating.value = false;
  }
}

async function submitSeededBug() {
  const task = selectedTask.value;
  if (props.dataSource !== "api" || (!task && !editingBugId.value)) {
    return;
  }
  creating.value = true;
  errorMessage.value = null;
  createMessage.value = null;
  try {
    if (editingBugId.value) {
      const updated = await updateSeededBug(editingBugId.value, {
        bug_type: bugForm.value.bugType.trim(),
        description: bugForm.value.description.trim(),
        expected_detection: bugForm.value.expectedDetection.trim()
      });
      createMessage.value = `${t("datasets.updatedSeededBug")}: ${updated.id}`;
      await loadDataset();
      selectedTaskId.value = updated.eval_task_id;
      resetBugForm();
      return;
    }
    if (!task) {
      return;
    }
    const created = await createSeededBug(task.id, {
      id: bugForm.value.id.trim() || null,
      bug_type: bugForm.value.bugType.trim(),
      description: bugForm.value.description.trim(),
      expected_detection: bugForm.value.expectedDetection.trim()
    });
    createMessage.value = `${t("datasets.createdSeededBug")}: ${created.id}`;
    await loadDataset();
    selectedTaskId.value = task.id;
    variantForm.value.seededBugId = created.id;
    resetBugForm();
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : t(editingBugId.value ? "datasets.updateSeededBugFailed" : "datasets.createSeededBugFailed");
  } finally {
    creating.value = false;
  }
}

async function submitVariant() {
  if (props.dataSource !== "api") {
    return;
  }
  creating.value = true;
  errorMessage.value = null;
  createMessage.value = null;
  try {
    const groundTruth = parseJsonObject(variantForm.value.groundTruthJson, "datasets.groundTruthJsonInvalid");
    if (groundTruth.source === "auto_mutation") {
      throw new Error(t("datasets.autoMutationVariantBlocked"));
    }
    if (editingVariantId.value) {
      const updated = await updateBugVariant(editingVariantId.value, {
        variant_name: variantForm.value.variantName.trim(),
        mutated_snapshot_id: variantForm.value.mutatedSnapshotId.trim() || null,
        ground_truth: groundTruth
      });
      createMessage.value = `${t("datasets.updatedVariant")}: ${updated.id}`;
      await loadDataset();
      resetVariantForm();
      return;
    }
    const created = await createBugVariant(variantForm.value.seededBugId.trim(), {
      id: variantForm.value.id.trim() || null,
      variant_name: variantForm.value.variantName.trim(),
      canonical_kind: "patch",
      patch: {
        file: variantForm.value.patchFile.trim(),
        old: variantForm.value.patchOld,
        new: variantForm.value.patchNew
      },
      mutated_snapshot_id: variantForm.value.mutatedSnapshotId.trim() || null,
      ground_truth: groundTruth
    });
    createMessage.value = `${t("datasets.createdVariant")}: ${created.id}`;
    await loadDataset();
    resetVariantForm();
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : t(editingVariantId.value ? "datasets.updateVariantFailed" : "datasets.createVariantFailed");
  } finally {
    creating.value = false;
  }
}
async function loadDataset() {
  const requestSeq = datasetRequest.next();
  if (isCreateOnly.value) {
    dataset.value = null;
    serverDatasetReadiness.value = null;
    selectedTaskId.value = null;
    errorMessage.value = null;
    loading.value = false;
    return;
  }
  loading.value = true;
  errorMessage.value = null;
  try {
    const [next, readiness] = props.dataSource === "demo"
      ? [demoEvalDataset, null]
      : await Promise.all([getEvalDataset(props.datasetId), getEvalDatasetReadiness(props.datasetId)]);
    if (!datasetRequest.isCurrent(requestSeq)) {
      return;
    }
    dataset.value = next;
    serverDatasetReadiness.value = readiness;
    selectedTaskId.value = next.tasks[0]?.id ?? null;
  } catch (error) {
    if (!datasetRequest.isCurrent(requestSeq)) {
      return;
    }
    dataset.value = null;
    serverDatasetReadiness.value = null;
    selectedTaskId.value = null;
    errorMessage.value = error instanceof Error ? error.message : t("datasets.loadFailed");
  } finally {
    if (datasetRequest.isCurrent(requestSeq)) {
      loading.value = false;
    }
  }
}

async function submitDataset() {
  if (props.dataSource !== "api") {
    return;
  }
  creating.value = true;
  createMessage.value = null;
  errorMessage.value = null;
  try {
    const created = await createEvalDataset({
      id: createForm.value.id.trim() || null,
      name: createForm.value.name.trim(),
      version: createForm.value.version.trim(),
      description: createForm.value.description.trim() || null,
      project_snapshot_ids: createForm.value.projectSnapshotIds
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean)
    });
    createMessage.value = `${t("datasets.created")}: ${created.id}`;
    emit("navigate", `#/datasets/${created.id}`);
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : t("datasets.createFailed");
  } finally {
    creating.value = false;
  }
}

onMounted(() => {
  void loadDataset();
});

watch(
  () => selectedTask.value?.id,
  () => {
    resetMutationState();
  }
);

watch(
  () => [props.datasetId, props.dataSource],
  () => {
    void loadDataset();
  }
);
</script>

<template>
  <main class="dataset-page app-page">
    <section class="detail-head">
      <button class="text-button" type="button" @click="emit('navigate', '#/experiments')">
        <ArrowLeft :size="16" aria-hidden="true" />
        {{ t("experiments.back") }}
      </button>
      <button class="text-button" type="button" @click="loadDataset">
        <RefreshCw :size="16" aria-hidden="true" />
        {{ t("projects.refresh") }}
      </button>
    </section>

    <p v-if="errorMessage" class="error-banner">{{ errorMessage }}</p>
    <p v-if="createMessage" class="mode-note">{{ createMessage }}</p>
    <p v-if="loading" class="mode-note">{{ t("datasets.loading") }}</p>

    <section v-if="props.dataSource === 'api'" class="subtle-panel create-dataset-panel">
      <div class="panel-head">
        <div>
          <p class="eyebrow">CREATE DATASET</p>
          <h2>{{ t("datasets.createTitle") }}</h2>
        </div>
        <button class="text-button" type="button" :disabled="creating" @click="submitDataset">
          {{ creating ? t("datasets.creating") : t("datasets.create") }}
        </button>
      </div>
      <div class="dataset-form-grid">
        <label>
          <span>{{ t("experiments.optionalId") }}</span>
          <input v-model="createForm.id" type="text" placeholder="dataset-local-v1" />
        </label>
        <label>
          <span>{{ t("experiments.name") }}</span>
          <input v-model="createForm.name" type="text" />
        </label>
        <label>
          <span>{{ t("datasets.version") }}</span>
          <input v-model="createForm.version" type="text" />
        </label>
        <label>
          <span>{{ t("projects.description") }}</span>
          <input v-model="createForm.description" type="text" />
        </label>
        <label class="wide-field">
          <span>{{ t("datasets.snapshots") }}</span>
          <input v-model="createForm.projectSnapshotIds" type="text" placeholder="snapshot id, snapshot id" />
        </label>
      </div>
    </section>

    <template v-if="dataset">
      <section class="hero-band subtle-panel">
        <div>
          <p class="eyebrow">TRACE / DATASET DETAIL</p>
          <h1>{{ dataset.name }}</h1>
          <p>{{ dataset.description ?? t("datasets.noDescription") }}</p>
        </div>
        <div class="source-card">
          <Database :size="18" aria-hidden="true" />
          <span>
            <strong>{{ dataset.id }}</strong>
            <small>{{ t("datasets.version") }} {{ dataset.version }} · {{ formatDate(dataset.created_at) }}</small>
          </span>
        </div>
      </section>

      <section class="metadata-grid">
        <article class="subtle-panel meta-tile">
          <span>{{ t("datasets.tasks") }}</span>
          <strong>{{ dataset.tasks.length }}</strong>
        </article>
        <article class="subtle-panel meta-tile">
          <span>{{ t("datasets.seededBugs") }}</span>
          <strong>{{ bugCount }}</strong>
        </article>
        <article class="subtle-panel meta-tile">
          <span>{{ t("datasets.variants") }}</span>
          <strong>{{ variantCount }}</strong>
        </article>
        <article class="subtle-panel meta-tile">
          <span>{{ t("datasets.snapshots") }}</span>
          <strong>{{ dataset.project_snapshot_ids.length }}</strong>
          <small>{{ dataset.project_snapshot_ids.join(", ") || t("common.none") }}</small>
        </article>
      </section>

      <section class="subtle-panel readiness-panel" :data-status="displayedDatasetReadiness.status">
        <div class="panel-head">
          <div>
            <p class="eyebrow">DATASET READINESS</p>
            <h2>{{ t("datasets.readiness") }}</h2>
          </div>
          <span class="status-chip" :data-status="displayedDatasetReadiness.status">
            {{ displayedDatasetReadiness.status === "ready" ? t("datasets.readinessReady") : t("datasets.readinessIncomplete") }}
          </span>
        </div>
        <p v-if="displayedDatasetReadiness.status === 'ready'" class="mode-note compact-note">{{ t("datasets.readinessReadyBody") }}</p>
        <div v-else class="readiness-issues">
          <p class="mode-note compact-note">
            <strong>{{ displayedDatasetReadiness.issues.length }}</strong> {{ t("datasets.readinessIssueCount") }}
          </p>
          <ul>
            <li v-for="issue in displayedDatasetReadiness.issues.slice(0, 8)" :key="issue.id">
              <strong>{{ issue.label }}</strong>
              <span>{{ issue.detail }}</span>
            </li>
          </ul>
          <small v-if="displayedDatasetReadiness.issues.length > 8">
            {{ t("datasets.readinessMore", { count: displayedDatasetReadiness.issues.length - 8 }) }}
          </small>
        </div>
      </section>

      <section v-if="props.dataSource === 'api'" class="subtle-panel authoring-panel">
        <div class="panel-head">
          <div>
            <p class="eyebrow">DATASET AUTHORING</p>
            <h2>{{ t("datasets.authoring") }}</h2>
          </div>
          <div class="authoring-actions">
            <button class="text-button" type="button" :disabled="!selectedTask" @click="loadSelectedTaskForEdit">
              {{ t("datasets.editSelectedTask") }}
            </button>
            <button class="text-button" type="button" @click="authoring = !authoring">
              {{ authoring ? t("json.collapse") : t("json.expand") }}
            </button>
          </div>
        </div>

        <div v-if="authoring" class="authoring-grid">
          <form class="authoring-form" @submit.prevent="submitTask">
            <div class="form-title">
              <strong>{{ editingTaskId ? t("datasets.updateTask") : t("datasets.createTask") }}</strong>
              <small>{{ t("datasets.task") }}</small>
            </div>
            <label>
              <span>{{ t("experiments.optionalId") }}</span>
              <input v-model="taskForm.id" type="text" placeholder="task-local-pricing" :disabled="Boolean(editingTaskId)" />
            </label>
            <label>
              <span>{{ t("datasets.snapshot") }}</span>
              <select v-if="availableSnapshotIds.length" v-model="taskForm.projectSnapshotId" required :disabled="Boolean(editingTaskId)">
                <option value="">{{ t("common.none") }}</option>
                <option v-for="snapshotId in availableSnapshotIds" :key="snapshotId" :value="snapshotId">{{ snapshotId }}</option>
              </select>
              <input v-else v-model="taskForm.projectSnapshotId" type="text" placeholder="snapshot id" required :disabled="Boolean(editingTaskId)" />
            </label>
            <label>
              <span>{{ t("projects.goal") }}</span>
              <input v-model="taskForm.goal" type="text" required />
            </label>
            <label>
              <span>{{ t("datasets.capabilities") }}</span>
              <input v-model="taskForm.expectedCapabilities" type="text" placeholder="boundary, validation" />
            </label>
            <label class="wide-field">
              <span>{{ t("datasets.targetScope") }}</span>
              <textarea v-model="taskForm.targetScopeJson" rows="5" spellcheck="false" />
            </label>
            <div class="form-actions">
              <button class="text-button" type="button" :disabled="creating" @click="fillTaskDemoPreset">
                {{ t("datasets.fillDemoTaskPreset") }}
              </button>
              <button class="text-button" type="submit" :disabled="creating">
                {{ editingTaskId ? t("datasets.updateTask") : t("datasets.createTask") }}
              </button>
              <button v-if="editingTaskId" class="text-button" type="button" :disabled="creating" @click="resetTaskForm">
                {{ t("common.cancel") }}
              </button>
            </div>
          </form>

          <form class="authoring-form" @submit.prevent="submitSeededBug">
            <div class="form-title">
              <strong>{{ editingBugId ? t("datasets.updateSeededBug") : t("datasets.createSeededBug") }}</strong>
              <small>{{ selectedTask?.id ?? t("common.none") }}</small>
            </div>
            <label>
              <span>{{ t("experiments.optionalId") }}</span>
              <input v-model="bugForm.id" type="text" placeholder="bug-local-pricing" :disabled="Boolean(editingBugId)" />
            </label>
            <label>
              <span>{{ t("datasets.bugType") }}</span>
              <input v-model="bugForm.bugType" type="text" required />
            </label>
            <label>
              <span>{{ t("projects.description") }}</span>
              <input v-model="bugForm.description" type="text" required />
            </label>
            <label>
              <span>{{ t("datasets.expectedDetection") }}</span>
              <input v-model="bugForm.expectedDetection" type="text" required />
            </label>
            <div class="form-actions">
              <button class="text-button" type="submit" :disabled="creating || (!selectedTask && !editingBugId)">
                {{ editingBugId ? t("datasets.updateSeededBug") : t("datasets.createSeededBug") }}
              </button>
              <button v-if="editingBugId" class="text-button" type="button" :disabled="creating" @click="resetBugForm">
                {{ t("common.cancel") }}
              </button>
            </div>
          </form>

          <form class="authoring-form variant-authoring" @submit.prevent="submitVariant">
            <div class="form-title">
              <strong>{{ editingVariantId ? t("datasets.updateVariant") : t("datasets.createVariant") }}</strong>
              <small>{{ editingVariantId ? t("datasets.patchLocked") : t("datasets.canonicalKind") + " patch" }}</small>
            </div>
            <label>
              <span>{{ t("datasets.seededBugId") }}</span>
              <select v-model="variantForm.seededBugId" required :disabled="Boolean(editingVariantId)">
                <option value="">{{ t("common.none") }}</option>
                <option v-for="bug in selectedTask?.seeded_bugs ?? []" :key="bug.id" :value="bug.id">{{ bug.id }}</option>
              </select>
            </label>
            <label>
              <span>{{ t("experiments.optionalId") }}</span>
              <input v-model="variantForm.id" type="text" placeholder="variant-local-pricing" :disabled="Boolean(editingVariantId)" />
            </label>
            <label>
              <span>{{ t("datasets.variantName") }}</span>
              <input v-model="variantForm.variantName" type="text" required />
            </label>
            <label>
              <span>{{ t("datasets.snapshot") }}</span>
              <input v-model="variantForm.mutatedSnapshotId" type="text" placeholder="optional mutated snapshot" />
            </label>
            <label>
              <span>{{ t("datasets.patchFile") }}</span>
              <input v-model="variantForm.patchFile" type="text" placeholder="shop/pricing.py" :required="!editingVariantId" :disabled="Boolean(editingVariantId)" />
            </label>
            <label>
              <span>{{ t("datasets.patchOld") }}</span>
              <textarea v-model="variantForm.patchOld" rows="4" spellcheck="false" :required="!editingVariantId" :disabled="Boolean(editingVariantId)" />
            </label>
            <label>
              <span>{{ t("datasets.patchNew") }}</span>
              <textarea v-model="variantForm.patchNew" rows="4" spellcheck="false" :disabled="Boolean(editingVariantId)" />
            </label>
            <label>
              <span>{{ t("datasets.groundTruthJson") }}</span>
              <textarea v-model="variantForm.groundTruthJson" rows="4" spellcheck="false" />
            </label>
            <div class="form-actions">
              <button class="text-button" type="button" :disabled="creating" @click="fillOrdinaryVariantPreset">
                {{ t("datasets.fillGroundTruthPreset") }}
              </button>
              <button class="text-button" type="submit" :disabled="creating || (!editingVariantId && !variantForm.seededBugId.trim())">
                {{ editingVariantId ? t("datasets.updateVariant") : t("datasets.createVariant") }}
              </button>
              <button v-if="editingVariantId" class="text-button" type="button" :disabled="creating" @click="resetVariantForm">
                {{ t("common.cancel") }}
              </button>
            </div>
          </form>
        </div>
      </section>

      <section class="dataset-grid">
        <aside class="subtle-panel task-list">
          <div class="panel-head">
            <div>
              <p class="eyebrow">{{ t("datasets.tasks") }}</p>
              <h2>{{ taskReadinessSummary.visible }} / {{ taskReadinessSummary.total }}</h2>
            </div>
            <GitBranch :size="18" aria-hidden="true" />
          </div>
          <div class="task-filter-panel">
            <label class="task-search-field">
              <span>{{ t("datasets.searchTasks") }}</span>
              <input v-model="taskSearchQuery" type="search" :placeholder="t('datasets.searchTasksPlaceholder')" />
            </label>
            <label>
              <span>{{ t("datasets.readinessFilter") }}</span>
              <select v-model="taskReadinessFilter">
                <option value="all">{{ t("datasets.filterAll") }}</option>
                <option value="ready">{{ t("datasets.readinessReady") }}</option>
                <option value="incomplete">{{ t("datasets.readinessIncomplete") }}</option>
              </select>
            </label>
          </div>
          <div class="task-summary-strip">
            <span><strong>{{ taskReadinessSummary.ready }}</strong><small>{{ t("datasets.readinessReady") }}</small></span>
            <span><strong>{{ taskReadinessSummary.incomplete }}</strong><small>{{ t("datasets.readinessIncomplete") }}</small></span>
            <span><strong>{{ taskReadinessSummary.hidden }}</strong><small>{{ t("datasets.hiddenByFilter") }}</small></span>
          </div>
          <button
            v-for="task in filteredTasks"
            :key="task.id"
            type="button"
            class="task-row"
            :class="{ selected: task.id === selectedTask?.id }"
            @click="selectedTaskId = task.id"
          >
            <span>
              <strong>{{ task.id }}</strong>
              <small>{{ task.project_snapshot_id }}</small>
            </span>
            <small class="task-row-meta">
              <span>{{ task.seeded_bugs.length }} {{ t("datasets.seededBugs") }}</span>
              <i class="status-chip" :data-status="taskReadinessStatus(task)">
                {{ taskReadinessStatus(task) === "ready" ? t("datasets.readinessReady") : t("datasets.readinessIncomplete") }}
              </i>
            </small>
          </button>
          <p v-if="filteredTasks.length === 0" class="empty-state compact-note">{{ t("datasets.noFilteredTasks") }}</p>
        </aside>

        <section v-if="selectedTask" class="task-detail">
          <article class="subtle-panel task-overview">
            <div>
              <p class="eyebrow">{{ t("datasets.selectedTask") }}</p>
              <h2>{{ selectedTask.id }}</h2>
              <p>{{ selectedTask.goal }}</p>
            </div>
            <dl>
              <div>
                <dt>{{ t("datasets.snapshot") }}</dt>
                <dd>{{ selectedTask.project_snapshot_id }}</dd>
              </div>
              <div>
                <dt>{{ t("datasets.capabilities") }}</dt>
                <dd>{{ capabilityText(selectedTask) }}</dd>
              </div>
            </dl>
          </article>

          <article class="subtle-panel json-panel">
            <div>
              <p class="eyebrow">{{ t("datasets.targetScope") }}</p>
              <h3>{{ t("datasets.scopeEvidence") }}</h3>
            </div>
            <JsonViewer :value="selectedTask.target_scope" :max-depth="5" :max-array-items="20" />
          </article>

          <article v-if="props.dataSource === 'api'" class="subtle-panel mutation-panel">
            <div class="panel-head">
              <div>
                <p class="eyebrow">AUTO MUTATION V0</p>
                <h3>{{ t("datasets.mutationDiscovery") }}</h3>
              </div>
              <FlaskConical :size="18" aria-hidden="true" />
            </div>

            <div class="mutation-controls">
              <label>
                <span>{{ t("datasets.sampleSeed") }}</span>
                <input v-model.number="discoveryForm.sampleSeed" type="number" min="0" />
              </label>
              <label>
                <span>{{ t("datasets.maxSelected") }}</span>
                <input v-model.number="discoveryForm.maxSelected" type="number" min="0" />
              </label>
              <button class="text-button" type="button" :disabled="mutationLoading" @click="runMutationDiscovery">
                {{ mutationLoading ? t("datasets.discovering") : t("datasets.runDiscovery") }}
              </button>
            </div>

            <p v-if="mutationError" class="error-banner compact-banner">{{ mutationError }}</p>
            <p v-if="mutationMessage" class="mode-note compact-note">{{ mutationMessage }}</p>

            <div v-if="mutationDiscovery" class="mutation-summary">
              <span>
                <strong>{{ mutationDiscovery.candidates.length }}</strong>
                <small>{{ t("datasets.candidates") }}</small>
              </span>
              <span>
                <strong>{{ mutationDiscovery.selected_count }}</strong>
                <small>{{ t("datasets.selected") }}</small>
              </span>
              <span>
                <strong>{{ mutationDiscovery.excluded_count }}</strong>
                <small>{{ t("datasets.excluded") }}</small>
              </span>
            </div>

            <div v-if="mutationDiscovery?.candidates.length" class="table-shell mutation-table">
              <table>
                <thead>
                  <tr>
                    <th>{{ t("experiments.status") }}</th>
                    <th>{{ t("datasets.candidate") }}</th>
                    <th>{{ t("datasets.operator") }}</th>
                    <th>{{ t("datasets.patch") }}</th>
                    <th>{{ t("experiments.action") }}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="candidate in mutationDiscovery.candidates" :key="candidate.candidate_id">
                    <td>
                      <span class="mutation-status" :data-status="candidate.selection.status">{{ mutationStatusLabel(candidate) }}</span>
                    </td>
                    <td>
                      <strong>{{ candidate.candidate_id }}</strong>
                      <small class="block-mono">{{ candidate.matcher.source_path }}:{{ candidate.matcher.start_line }}</small>
                    </td>
                    <td>{{ candidate.operator }}</td>
                    <td>
                      <code>{{ candidate.patch.file }}</code>
                      <pre>{{ candidate.patch.old }}
=> {{ candidate.patch.new }}</pre>
                    </td>
                    <td>
                      <button
                        class="text-button"
                        type="button"
                        :disabled="candidate.selection.status !== 'selected'"
                        @click="selectMutationCandidate(candidate)"
                      >
                        {{ selectedCandidateId === candidate.candidate_id ? t("datasets.selected") : t("datasets.select") }}
                      </button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p v-else-if="mutationDiscovery" class="mode-note compact-note">{{ t("datasets.noCandidates") }}</p>

            <div v-if="mutationDiscovery?.exclusions.length" class="table-shell mutation-exclusion-table">
              <table>
                <thead>
                  <tr>
                    <th>{{ t("datasets.exclusionReason") }}</th>
                    <th>{{ t("datasets.exclusionTarget") }}</th>
                    <th>{{ t("datasets.exclusionMessage") }}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(exclusion, index) in mutationDiscovery.exclusions" :key="`${exclusion.reason_code}:${index}`">
                    <td>
                      <span class="mutation-status" data-status="excluded">{{ exclusion.reason_code }}</span>
                    </td>
                    <td>
                      <strong>{{ exclusion.target_ref ?? t("common.none") }}</strong>
                      <small class="block-mono">{{ exclusion.source_path ?? t("common.none") }}:{{ exclusion.line ?? "-" }}</small>
                    </td>
                    <td>{{ exclusion.message }}</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <div v-if="selectedCandidate" class="confirm-grid">
              <label>
                <span>{{ t("datasets.seededBugId") }}</span>
                <input v-model="confirmForm.seededBugId" type="text" placeholder="bug-auto-id" />
              </label>
              <label>
                <span>{{ t("datasets.variantId") }}</span>
                <input v-model="confirmForm.variantId" type="text" placeholder="variant-auto-id" />
              </label>
              <label>
                <span>{{ t("datasets.variantName") }}</span>
                <input v-model="confirmForm.variantName" type="text" />
              </label>
              <label>
                <span>{{ t("projects.description") }}</span>
                <input v-model="confirmForm.description" type="text" />
              </label>
              <label class="wide-field">
                <span>{{ t("datasets.expectedDetection") }}</span>
                <input v-model="confirmForm.expectedDetection" type="text" />
              </label>
              <label class="probe-field">
                <span>{{ t("datasets.probeJson") }}</span>
                <textarea v-model="confirmForm.probeJson" rows="7" spellcheck="false" />
                <small>{{ t("datasets.fillProbePresetHint") }}</small>
              </label>
              <button class="text-button confirm-button" type="button" :disabled="!canConfirmMutation" @click="confirmMutationCandidate">
                <CheckCircle2 :size="16" aria-hidden="true" />
                {{ mutationConfirming ? t("datasets.confirming") : t("datasets.confirmSelected") }}
              </button>
            </div>
          </article>
          <section class="bug-stack">
            <p v-if="selectedTask.seeded_bugs.length === 0" class="empty-state subtle-panel">{{ t("datasets.noSeededBugs") }}</p>
            <article v-for="bug in selectedTask.seeded_bugs" :key="bug.id" class="subtle-panel bug-card">
              <header class="bug-head">
                <div>
                  <p class="eyebrow">{{ bug.bug_type }}</p>
                  <h3>{{ bug.id }}</h3>
                  <p>{{ bug.description }}</p>
                </div>
                <div class="bug-tools">
                  <button class="text-button" type="button" @click="loadBugForEdit(bug)">{{ t("datasets.edit") }}</button>
                  <span class="status-chip" :data-status="bugReadinessStatus(bug)">
                    {{ bugReadinessStatus(bug) === "ready" ? t("datasets.readinessReady") : t("datasets.readinessIncomplete") }}
                  </span>
                  <Bug :size="18" aria-hidden="true" />
                </div>
              </header>
              <dl>
                <div>
                  <dt>{{ t("datasets.expectedDetection") }}</dt>
                  <dd>{{ bug.expected_detection }}</dd>
                </div>
              </dl>
              <p v-if="bug.variants.length === 0" class="mode-note compact-note">{{ t("datasets.noVariants") }}</p>
              <div v-else class="table-shell variant-table">
                <table>
                  <thead>
                    <tr>
                      <th>{{ t("datasets.variant") }}</th>
                      <th>{{ t("datasets.canonicalKind") }}</th>
                      <th>{{ t("datasets.snapshot") }}</th>
                      <th>{{ t("datasets.confirmation") }}</th>
                      <th>{{ t("datasets.patch") }}</th>
                      <th>{{ t("experiments.action") }}</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="variant in bug.variants" :key="variant.id">
                      <td>
                        <strong>{{ variant.variant_name }}</strong>
                        <small class="block-mono">{{ variant.id }}</small>
                      </td>
                      <td>{{ variant.canonical_kind }}</td>
                      <td class="mono">{{ variant.mutated_snapshot_id ?? t("common.none") }}</td>
                      <td>
                        <span class="mutation-status" :data-status="variantConfirmationStatus(variant)">{{ variantConfirmationLabel(variant) }}</span>
                        <small class="block-mono confirmation-detail">{{ variantConfirmationDetail(variant) }}</small>
                      </td>
                      <td>
                        <JsonViewer :value="variant.ground_truth as JsonObject" :max-depth="4" :max-array-items="10" />
                      </td>
                      <td>
                        <button class="text-button" type="button" @click="loadVariantForEdit(variant)">{{ t("datasets.edit") }}</button>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </article>
          </section>
        </section>
      </section>
    </template>
  </main>
</template>

<style scoped>
.detail-head,
.hero-band,
.metadata-grid,
.readiness-panel,
.dataset-grid,
.create-dataset-panel,
.authoring-panel {
  margin-top: 18px;
}

.detail-head,
.hero-band,
.panel-head,
.bug-head {
  display: flex;
  gap: 16px;
  align-items: flex-start;
  justify-content: space-between;
}

.hero-band {
  padding: 18px;
}

.hero-band h1 {
  margin: 8px 0 10px;
}

.hero-band p {
  max-width: 760px;
  font-family: var(--font-serif);
}

.source-card {
  width: min(380px, 100%);
  display: flex;
  gap: 10px;
  align-items: flex-start;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: 7px;
  background: rgba(251, 250, 247, 0.78);
}

.source-card span,
.task-row span {
  display: grid;
  gap: 2px;
  min-width: 0;
}

.source-card small,
.task-row small,
.block-mono,
.meta-tile small {
  overflow-wrap: anywhere;
  color: var(--muted);
  font-family: var(--font-mono);
  font-size: 11px;
}

.readiness-panel,
.authoring-panel {
  display: grid;
  gap: 14px;
}

.readiness-issues {
  display: grid;
  gap: 10px;
}

.readiness-issues ul {
  display: grid;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.readiness-issues li {
  display: grid;
  gap: 2px;
  padding: 10px;
  border: 1px solid var(--border);
  border-radius: 7px;
  background: rgba(251, 250, 247, 0.72);
}

.readiness-issues li span,
.readiness-issues small {
  color: var(--muted);
}

.status-chip {
  display: inline-flex;
  width: fit-content;
  min-height: 24px;
  align-items: center;
  padding: 2px 7px;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--panel-soft);
  color: var(--muted-strong);
  font-family: var(--font-mono);
  font-size: 11px;
  font-style: normal;
  text-transform: uppercase;
}

.status-chip[data-status="ready"] {
  border-color: rgba(49, 95, 125, 0.28);
  background: var(--tool-bg);
  color: var(--tool);
}

.status-chip[data-status="incomplete"] {
  border-color: rgba(145, 93, 38, 0.28);
  background: rgba(145, 93, 38, 0.08);
  color: #795125;
}

.authoring-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
  align-items: start;
}

.authoring-form {
  display: grid;
  gap: 10px;
  min-width: 0;
}

.authoring-form label {
  display: grid;
  gap: 6px;
}

.authoring-form label span,
.form-title small {
  color: var(--muted);
  font-family: var(--font-mono);
  font-size: 11px;
  text-transform: uppercase;
}

.authoring-form textarea {
  resize: vertical;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.5;
}

.form-title {
  display: grid;
  gap: 2px;
}

.variant-authoring {
  grid-column: 1 / -1;
  grid-template-columns: repeat(4, minmax(0, 1fr));
}

.variant-authoring .form-title,
.variant-authoring .form-actions {
  grid-column: 1 / -1;
  justify-self: start;
}

.metadata-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.meta-tile,
.create-dataset-panel,
.readiness-panel,
.authoring-panel,
.task-list,
.task-overview,
.json-panel,
.mutation-panel,
.bug-card {
  padding: 18px;
}

.create-dataset-panel {
  display: grid;
  gap: 14px;
}

.dataset-form-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.dataset-form-grid label {
  display: grid;
  gap: 6px;
}

.dataset-form-grid label span {
  color: var(--muted);
  font-family: var(--font-mono);
  font-size: 11px;
  text-transform: uppercase;
}

.dataset-form-grid input,
.authoring-form input,
.authoring-form select,
.authoring-form textarea,
.confirm-grid input,
.confirm-grid textarea,
.mutation-controls input {
  width: 100%;
  min-height: 34px;
  padding: 6px 9px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--panel);
  color: var(--ink);
}

.wide-field {
  grid-column: span 2;
}

.meta-tile {
  display: grid;
  gap: 3px;
}

.meta-tile span {
  color: var(--muted);
  font-family: var(--font-mono);
  font-size: 11px;
  text-transform: uppercase;
}

.meta-tile strong {
  overflow-wrap: anywhere;
  font-size: 24px;
  line-height: 1.2;
}

.dataset-grid {
  display: grid;
  grid-template-columns: minmax(260px, 340px) minmax(0, 1fr);
  gap: 18px;
  align-items: start;
}

.task-list,
.task-detail,
.bug-stack,
.json-panel,
.mutation-panel,
.bug-card {
  display: grid;
  gap: 14px;
}


.task-filter-panel {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(120px, 150px);
  gap: 10px;
}

.task-filter-panel label {
  display: grid;
  gap: 6px;
}

.task-filter-panel label span {
  color: var(--muted);
  font-family: var(--font-mono);
  font-size: 11px;
  text-transform: uppercase;
}

.task-filter-panel input,
.task-filter-panel select {
  width: 100%;
  min-height: 34px;
  padding: 6px 9px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--panel);
  color: var(--ink);
}

.task-summary-strip {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
}

.task-summary-strip span {
  display: grid;
  gap: 1px;
  padding: 8px;
  border: 1px solid var(--border);
  border-radius: 7px;
  background: rgba(251, 250, 247, 0.72);
}

.task-summary-strip strong {
  line-height: 1.15;
}

.task-summary-strip small {
  overflow-wrap: anywhere;
  color: var(--muted);
  font-family: var(--font-mono);
  font-size: 10px;
  text-transform: uppercase;
}
.task-row {
  width: 100%;
  display: flex;
  gap: 10px;
  justify-content: space-between;
  margin-top: 10px;
  padding: 10px;
  text-align: left;
}

.task-row:hover:not(.selected) {
  border-color: var(--border-strong);
  background: rgba(251, 250, 247, 0.9);
  box-shadow: var(--shadow-hover);
  transform: translateY(-1px);
}

.task-row.selected {
  border-color: var(--tool);
  background: rgba(49, 95, 125, 0.08);
  box-shadow: inset 3px 0 0 var(--tool);
}

.task-row-meta span {
  display: inline;
}

.task-row-meta,
.bug-tools {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
}

.task-overview {
  display: grid;
  gap: 14px;
}

.task-overview dl,
.bug-card dl {
  display: grid;
  gap: 10px;
  margin: 0;
}

.task-overview dt,
.bug-card dt {
  color: var(--muted);
  font-family: var(--font-mono);
  font-size: 11px;
  text-transform: uppercase;
}

.task-overview dd,
.bug-card dd {
  margin: 0;
  color: var(--muted-strong);
  overflow-wrap: anywhere;
}

.bug-head p {
  margin-top: 6px;
}


.confirmation-detail {
  display: block;
  max-width: 280px;
  margin-top: 6px;
}
.variant-table {
  margin-top: 4px;
}

.variant-table :deep(.json-tools),
.variant-table :deep(.json-note) {
  display: none;
}

.variant-table :deep(.json-viewer) {
  max-height: 180px;
}


.mutation-panel {
  display: grid;
  gap: 14px;
}

.mutation-controls,
.confirm-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  align-items: end;
}

.mutation-controls label,
.confirm-grid label {
  display: grid;
  gap: 6px;
}

.mutation-controls label span,
.confirm-grid label span {
  color: var(--muted);
  font-family: var(--font-mono);
  font-size: 11px;
  text-transform: uppercase;
}

.confirm-grid textarea {
  min-height: 148px;
  resize: vertical;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.5;
}

.probe-field {
  grid-column: 1 / -1;
}

.confirm-button {
  justify-self: start;
}

.compact-banner,
.compact-note {
  margin: 0;
}

.mutation-summary {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.mutation-summary span {
  display: grid;
  gap: 2px;
  padding: 10px;
  border: 1px solid var(--border);
  border-radius: 7px;
  background: rgba(251, 250, 247, 0.72);
}

.mutation-summary strong {
  font-size: 20px;
  line-height: 1.15;
}

.mutation-summary small,
.mutation-status {
  color: var(--muted);
  font-family: var(--font-mono);
  font-size: 11px;
  text-transform: uppercase;
}

.mutation-status {
  display: inline-flex;
  min-height: 24px;
  align-items: center;
  padding: 2px 7px;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--panel-soft);
}

.mutation-status[data-status="selected"] {
  border-color: rgba(49, 95, 125, 0.28);
  background: rgba(49, 95, 125, 0.08);
  color: var(--tool);
}

.mutation-status[data-status="ordinary"] {
  border-color: rgba(94, 101, 111, 0.28);
  background: rgba(94, 101, 111, 0.08);
  color: var(--muted-strong);
}

.mutation-status[data-status="confirmed"] {
  border-color: rgba(49, 95, 125, 0.28);
  background: rgba(49, 95, 125, 0.08);
  color: var(--tool);
}

.mutation-status[data-status="probe_failed"],
.mutation-status[data-status="probe_missing"] {
  border-color: rgba(145, 93, 38, 0.28);
  background: rgba(145, 93, 38, 0.08);
  color: #795125;
}
.mutation-status[data-status="excluded"] {
  border-color: rgba(145, 93, 38, 0.28);
  background: rgba(145, 93, 38, 0.08);
  color: #795125;
}

.mutation-table code,
.mutation-table pre {
  font-family: var(--font-mono);
  font-size: 11px;
}

.mutation-table pre {
  max-width: 420px;
  margin: 6px 0 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
@media (max-width: 1080px) {
  .hero-band,
  .metadata-grid,
  .dataset-grid,
  .dataset-form-grid,
  .authoring-grid,
  .variant-authoring {
    grid-template-columns: 1fr;
  }

  .hero-band {
    display: grid;
  }

  .wide-field {
    grid-column: auto;
  }
}
</style>
