import { requestJson } from "./client";
import type {
  BugVariantCreateRequest,
  BugVariantOut,
  EvalDatasetDetailOut,
  EvalDatasetOut,
  EvalTaskCreateRequest,
  EvalTaskOut,
  MutationCandidateConfirmRequest,
  MutationDiscoveryDryRunRequest,
  MutationDiscoveryResultContract,
  SeededBugCreateRequest,
  SeededBugDetailOut,
  SeededBugOut
} from "../types/api";

export function listEvalDatasets(): Promise<EvalDatasetOut[]> {
  return requestJson<EvalDatasetOut[]>("/api/v1/eval-datasets");
}

export function getEvalDataset(datasetId: string): Promise<EvalDatasetDetailOut> {
  return requestJson<EvalDatasetDetailOut>(`/api/v1/eval-datasets/${datasetId}`);
}

export function createEvalDataset(body: {
  id?: string | null;
  name: string;
  version: string;
  description?: string | null;
  project_snapshot_ids: string[];
}): Promise<EvalDatasetOut> {
  return requestJson<EvalDatasetOut>("/api/v1/eval-datasets", {
    method: "POST",
    body: JSON.stringify(body)
  });
}

export function createEvalTask(datasetId: string, body: EvalTaskCreateRequest): Promise<EvalTaskOut> {
  return requestJson<EvalTaskOut>(`/api/v1/eval-datasets/${datasetId}/tasks`, {
    method: "POST",
    body: JSON.stringify(body)
  });
}

export function listEvalTasks(datasetId: string): Promise<EvalTaskOut[]> {
  return requestJson<EvalTaskOut[]>(`/api/v1/eval-datasets/${datasetId}/tasks`);
}

export function createSeededBug(taskId: string, body: SeededBugCreateRequest): Promise<SeededBugOut> {
  return requestJson<SeededBugOut>(`/api/v1/eval-tasks/${taskId}/seeded-bugs`, {
    method: "POST",
    body: JSON.stringify(body)
  });
}

export function listSeededBugs(taskId: string): Promise<SeededBugOut[]> {
  return requestJson<SeededBugOut[]>(`/api/v1/eval-tasks/${taskId}/seeded-bugs`);
}

export function dryRunTaskMutationDiscovery(
  taskId: string,
  body: MutationDiscoveryDryRunRequest = {}
): Promise<MutationDiscoveryResultContract> {
  return requestJson<MutationDiscoveryResultContract>(`/api/v1/eval-tasks/${taskId}/mutation-discovery/dry-run`, {
    method: "POST",
    body: JSON.stringify(body)
  });
}

export function confirmSelectedMutationCandidate(
  taskId: string,
  body: MutationCandidateConfirmRequest
): Promise<SeededBugDetailOut> {
  return requestJson<SeededBugDetailOut>(`/api/v1/eval-tasks/${taskId}/mutation-discovery/confirm-selected`, {
    method: "POST",
    body: JSON.stringify(body)
  });
}

export function createBugVariant(seededBugId: string, body: BugVariantCreateRequest): Promise<BugVariantOut> {
  return requestJson<BugVariantOut>(`/api/v1/seeded-bugs/${seededBugId}/variants`, {
    method: "POST",
    body: JSON.stringify(body)
  });
}

export function listBugVariants(seededBugId: string): Promise<BugVariantOut[]> {
  return requestJson<BugVariantOut[]>(`/api/v1/seeded-bugs/${seededBugId}/variants`);
}
