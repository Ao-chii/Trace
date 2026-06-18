import { requestJson } from "./client";
import type {
  ExperimentCreateRequest,
  ExperimentCleanRunOut,
  ExperimentDefinition,
  ExperimentMetricsResponse,
  TestReplayOut
} from "../types/api";

export function listExperiments(): Promise<ExperimentDefinition[]> {
  return requestJson<ExperimentDefinition[]>("/api/v1/experiments");
}

export function createExperiment(body: ExperimentCreateRequest): Promise<ExperimentDefinition> {
  return requestJson<ExperimentDefinition>("/api/v1/experiments", {
    method: "POST",
    body: JSON.stringify(body)
  });
}

export function getExperiment(experimentId: string): Promise<ExperimentDefinition> {
  return requestJson<ExperimentDefinition>(`/api/v1/experiments/${experimentId}`);
}

export function runExperiment(experimentId: string): Promise<ExperimentDefinition> {
  return requestJson<ExperimentDefinition>(`/api/v1/experiments/${experimentId}/runs`, {
    method: "POST"
  });
}

export function cancelExperiment(experimentId: string): Promise<ExperimentDefinition> {
  return requestJson<ExperimentDefinition>(`/api/v1/experiments/${experimentId}/cancel`, {
    method: "POST"
  });
}

export function listExperimentCleanRuns(experimentId: string): Promise<ExperimentCleanRunOut[]> {
  return requestJson<ExperimentCleanRunOut[]>(`/api/v1/experiments/${experimentId}/clean-runs`);
}

export function listExperimentReplayRuns(experimentId: string): Promise<TestReplayOut[]> {
  return requestJson<TestReplayOut[]>(`/api/v1/experiments/${experimentId}/replay-runs`);
}

export function getExperimentMetrics(experimentId: string): Promise<ExperimentMetricsResponse> {
  return requestJson<ExperimentMetricsResponse>(`/api/v1/experiments/${experimentId}/metrics`);
}
